"""明确授权、单次请求、发送前费用预留；不包含任何生产Memory策略修改。"""
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter
import math
import re

from min_agent.models import Message
from min_agent.long_memory.answer_eval import encoded,FRAMING_RESERVE,TOTAL_BUDGET,OUTPUT_TOKENS


def reserve(group,rates):
    return (Decimal(group['input_upper_bound'])*Decimal(str(rates['input']))+
            Decimal(group['max_output_tokens'])*Decimal(str(rates['output'])))/Decimal(1_000_000)


def validate_authorization(pack,authorization,pack_sha):
    if not isinstance(authorization,dict) or authorization.get('approved_by_user') is not True:
        raise PermissionError('没有本轮明确用户授权，禁止读取凭据/调用模型')
    for key in ['model','reasoning_effort']:
        if authorization.get(key)!=pack[key]:raise PermissionError('授权模型/推理配置不匹配')
    if authorization.get('pack_sha256')!=pack_sha:raise PermissionError('授权未绑定此请求包')
    if authorization.get('data_scope')!='public_book_and_synthetic' or authorization.get('external_judge') is not False:
        raise PermissionError('本轮只允许公开/合成回答，不允许外部Judge')
    if not authorization.get('user_confirmation_reference'):raise PermissionError('必须记录真实用户确认来源')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',authorization.get('approval_id','')):raise ValueError('approval_id无效')
    limit=authorization.get('max_requests');cap=authorization.get('max_cost_usd')
    if type(limit) is not int or not 1<=limit<=100:raise ValueError('授权请求上限无效')
    if type(cap) not in {int,float} or not math.isfinite(cap) or cap<=0:raise ValueError('费用上限无效')
    all_groups=[g for c in pack['cases'] for g in c['groups'].values()]
    if len(all_groups)>limit or pack['request_count']!=len(all_groups):raise PermissionError('请求数超过授权')
    if pack['model']!='deepseek-flash' or pack['reasoning_effort']!='high':raise PermissionError('不隐式切换模型')
    rates=pack['peak_usd_per_million']
    if rates!={'input':.30,'output':1.20}:raise PermissionError('费率须重新核验与授权')
    for group in all_groups:
        if group['max_output_tokens']!=pack['max_output_tokens'] or group['tools']!=[]:raise ValueError('组配置不一致')
        if group['total_budget']!=TOTAL_BUDGET or group['max_output_tokens']!=OUTPUT_TOKENS:
            raise ValueError('预算配置与冻结协议不一致')
        if len(encoded(group['messages']))+FRAMING_RESERVE!=group['input_upper_bound']:
            raise ValueError('不能低报输入预算或费用预留')
        if group['input_upper_bound']+group['max_output_tokens']>group['total_budget']:raise ValueError('输入超预算')
        if sha256(encoded(group['messages'])).hexdigest()!=group['request_sha256']:raise ValueError('请求包内容hash失配')
    worst=sum((reserve(g,rates) for g in all_groups),Decimal(0))
    if worst>Decimal(str(cap)):raise PermissionError('最坏预留费用超过授权，禁止发送')
    return worst


def parse_public_answer(text):
    try:
        value=json.loads(text)
        if not isinstance(value,dict) or not isinstance(value.get('answer'),str):raise ValueError()
        for key in ['used_evidence_ids','uncertainties']:
            if not isinstance(value.get(key),list) or any(not isinstance(v,str) for v in value[key]):raise ValueError()
        return {k:value[k] for k in ['answer','used_evidence_ids','uncertainties']}
    except (ValueError,TypeError):return None


def run_authorized(pack,authorization,*,pack_sha,factory,journal,claim):
    worst=validate_authorization(pack,authorization,pack_sha)
    journal=Path(journal);claim=Path(claim)
    if journal.exists() or claim.exists():raise FileExistsError('已有批次/授权占用，禁止自动重复计费')
    journal.parent.mkdir(parents=True,exist_ok=True);claim.parent.mkdir(parents=True,exist_ok=True)
    # exclusive claim保证同一授权最多启动一次。失败后不自动重试，保留未知执行证据。
    with claim.open('x',encoding='utf-8') as f:json.dump(dict(approval_id=authorization['approval_id'],pack_sha256=pack_sha,worst_reserved_usd=float(worst)),f)
    responses=[];requests=0;reserved=Decimal(0);usage_cost=Decimal(0);status='COMPLETE';model=None
    with journal.open('x',encoding='utf-8') as stream:
        def record(value):stream.write(json.dumps(value,ensure_ascii=False)+'\n');stream.flush()
        record(dict(event='authorized_start',approval_id=authorization['approval_id'],pack_sha256=pack_sha,
                    max_requests=authorization['max_requests'],max_cost_usd=authorization['max_cost_usd']))
        try:
            model=factory()  # 只有上述全部授权/预算检查成功后才能读取凭据。
            for number,case in enumerate(pack['cases']):
                order=['A','B','C'];offset=number%3;order=order[offset:]+order[:offset]
                for group in order:
                    request=case['groups'][group];fee=reserve(request,pack['peak_usd_per_million'])
                    if requests>=authorization['max_requests'] or reserved+fee>Decimal(str(authorization['max_cost_usd'])):
                        raise RuntimeError('local_budget')
                    requests+=1;reserved+=fee
                    record(dict(event='request_reserved',request_number=requests,case_id=case['case_id'],group=group,
                                request_sha256=request['request_sha256'],reserved_usd=float(fee)))
                    before=len(model.usage);start=perf_counter()
                    try:
                        response=model.generate([Message.model_validate(m) for m in request['messages']],tools=[])
                    except Exception as error:
                        # 不写原始异常/请求头/远端正文，防止凭据泄露。
                        record(dict(event='request_failed',request_number=requests,error_code=getattr(error,'code','transport_or_execution_error')))
                        raise RuntimeError('request_failed') from None
                    elapsed=(perf_counter()-start)*1000
                    parsed=parse_public_answer(response.content)
                    row=dict(case_id=case['case_id'],group=group,request_number=requests,
                        raw_answer=response.content,answer_sha256=sha256(response.content.encode()).hexdigest(),
                        parsed=parsed,finish_reason=response.finish_reason,latency_ms=elapsed,
                        response_model=getattr(response.provider_state,'response_model',pack['model']),
                        status='ANSWERED' if parsed is not None and response.finish_reason=='stop' and not response.tool_calls else 'INCOMPLETE_OR_FORMAT',
                        usage_status='UNKNOWN',usage=None,peak_usage_cost_usd=None,tool_requests_executed=0)
                    if len(model.usage)>before:
                        usage=model.usage[-1]
                        if all(type(usage.get(k)) is int and usage[k]>=0 for k in ['input_tokens','output_tokens']):
                            price=(Decimal(usage['input_tokens'])*Decimal('.30')+Decimal(usage['output_tokens'])*Decimal('1.20'))/Decimal(1_000_000)
                            row.update(usage_status='REPORTED',usage=usage,peak_usage_cost_usd=float(price));usage_cost+=price
                    responses.append(row);record(dict(event='response',**row))
                    if row['usage_status']=='UNKNOWN':raise RuntimeError('usage_unknown_stop')
                    if Decimal(str(row['peak_usage_cost_usd']))>fee:raise RuntimeError('usage_exceeds_reservation')
        except Exception as error:
            status='PARTIAL';record(dict(event='batch_stopped',reason=str(error) if isinstance(error,RuntimeError) else 'local_setup_error'))
        finally:
            if model is not None:model.close()
        result=dict(status=status,responses=responses,model_calls=requests,reserved_upper_usd=float(reserved),
                    total_peak_usage_cost_usd=float(usage_cost),invoice_cost='NOT_AVAILABLE',
                    missing_usage=any(r['usage_status']=='UNKNOWN' for r in responses),automatic_retries=0)
        record(dict(event='finished',**{k:v for k,v in result.items() if k!='responses'}))
    return result
