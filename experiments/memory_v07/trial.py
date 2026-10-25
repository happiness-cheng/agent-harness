"""v07三题最小B0/B2验收；预算阻断即整批停止，默认离线。"""
import argparse
from copy import deepcopy
import gzip
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

from pydantic import SecretStr
from min_agent.llm.deepseek import DeepSeekSettings
from min_agent.models import Message
from min_agent.long_memory.answer_eval import encoded
from min_agent.long_memory.evaluation import build_approved_fixture,timestamp
from min_agent.long_memory.active_evidence import active_registry,group_messages,validate_answer,visible_ids
from min_agent.long_memory.integration import bind_runtime_memory
from min_agent.long_memory.models import Query
from min_agent.tools.registry import ToolRegistry
from min_agent.long_memory.evidence_budget import CallLedger
from experiments.memory_v07.runtime_loop import AccountedModel,run_active_v07
from experiments.memory_v07.budget import BudgetBlocked

ROOT=Path(__file__).resolve().parents[2]
LIMITS=dict(single=32768,cumulative=65536,output_reserve=4096,model_calls=3,tool_calls=4)
TOOLS=['search_long_memory','read_long_memory_topic','read_long_memory_evidence']
BUDGET_STOPS={'single_request_limit','cumulative_token_limit','model_call_limit','tool_call_limit',
              'external_call_limit','fee_limit','usage_unknown','usage_exceeds_reservation','fee_exceeds_reservation'}


def load(path):return json.loads(gzip.decompress(path.read_bytes()))
def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8',newline='\n') as f:json.dump(value,f,ensure_ascii=False,indent=2)
def source_hashes():
    paths=[*ROOT.glob('src/min_agent/**/*.py'),*ROOT.glob('experiments/**/*.py'),
           ROOT/'src/min_agent/prompts/system.md',ROOT/'scripts/eval_memory_active_v07.py',
           ROOT/'tests/fixtures/memory_v07_pilot.json',ROOT/'reports/memory_v07/policy-freeze.json']
    return {p.relative_to(ROOT).as_posix():sha256(p.read_bytes()).hexdigest() for p in paths}
def make_fixture(case):
    memory,ctx,info=build_approved_fixture(case['fixture'],approval='approved_fixture')
    at=timestamp(case['as_of']);memory.clock=lambda:at
    return memory,ctx,Query(text=case['question'],as_of=at)


def prepare_pack():
    # 先验证原客户端参数，使用明确的假密钥；不读凭据/不创建网络客户端。
    DeepSeekSettings(api_key=SecretStr('PREFLIGHT_FIXTURE_ONLY'),model='deepseek-flash',reasoning_effort='high',
        max_requests=3,max_output_tokens=4096,timeout_seconds=180.0)
    freeze=json.loads((ROOT/'reports/memory_v07/policy-freeze.json').read_text(encoding='utf-8'))
    assert all(sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in freeze['sha256'].items())
    old=load(ROOT/'reports/memory_v06/prepared-client-fix.json.gz')
    public=next(c for c in old['cases'] if c['case_id']=='layer1_01_bank_account')
    selected=[dict(fixture=public['fixture'],question=public['question'],as_of=public['as_of'],gold=public['gold'],origin='public_seen_dev')]
    data=json.loads((ROOT/'tests/fixtures/memory_v07_pilot.json').read_text(encoding='utf-8'))
    for item in data['synthetic_cases']:
        clean={k:item[k] for k in ('test_id','category','user_question','conversation_histories')}
        at=max(timestamp(h['timestamp']) for h in clean['conversation_histories'])
        selected.append(dict(fixture=clean,question=clean['user_question'],as_of=at.isoformat(),gold=item['gold'],origin='new_controlled_synthetic'))
    cases=[]
    for item in selected:
        memory,ctx,q=make_fixture(item)
        registry=ToolRegistry(sessions=memory.sessions,context=ctx)
        bind_runtime_memory(registry,memory,ctx)
        b0=registry.memory_view([Message(role='user',content=q.text)]) or {}
        active,state=active_registry(memory,ctx,q)
        initial=active.memory_view([Message(role='user',content=q.text)]) or {}
        cases.append(dict(item,case_id=item['fixture']['test_id'],b0_reference=b0,
            b0_messages=[m.model_dump(mode='json') for m in group_messages(q.text,q.as_of,b0)],
            b2_initial_messages=[m.model_dump(mode='json') for m in group_messages(q.text,q.as_of,initial)],
            tools=active.schemas(),source_episodes=[m.model_dump(mode='json') for m in memory.records(ctx,as_of=q.as_of,scenario_ids=[])]))
    return dict(protocol='v07-minimal-active-v1',base_commit='30f6126',limits=LIMITS,
        model='deepseek-flash',reasoning_effort='high',max_output_tokens=4096,reference_budget=4096,
        max_requests=len(cases)*4,max_cost_usd=.5,allowed_tools=TOOLS,cases=cases,source_hashes=source_hashes(),
        oracle=False,private_imports=0,judge=False,stop_on_b2_budget=True,
        upper_fee_estimate_usd=len(cases)*4*((32768-4096)*.30+4096*1.20)/1_000_000)


def validate_authorization(pack,permission,pack_hash):
    if not isinstance(permission,dict) or permission.get('approved_by_user') is not True:raise PermissionError('尚无本轮明确授权')
    expected=dict(pack_sha256=pack_hash,model='deepseek-flash',reasoning_effort='high',max_requests=12,
        max_cost_usd=.5,data_scope='public_book_and_synthetic',external_judge=False,tools=TOOLS)
    if any(permission.get(k)!=v for k,v in expected.items()) or not permission.get('user_confirmation_reference'):
        raise PermissionError('授权模型/数据/次数/费用/工具范围不匹配')
    if pack['limits']!=LIMITS or pack['max_requests']!=12 or len(pack['cases'])!=3 or pack['stop_on_b2_budget'] is not True:
        raise PermissionError('冻结的门禁或样本范围已变')
    if pack['model']!='deepseek-flash' or pack['max_output_tokens']!=4096 or pack['max_cost_usd']!=.5:
        raise PermissionError('不允许变更模型或输出/费用上限')
    if pack['source_hashes']!=source_hashes():raise PermissionError('源码或Fixture哈希已变化，尚未读取凭据')


def run_trial(pack,*,factory,ledger,backend):
    cells=[];status='COMPLETE';stop=None
    for case in pack['cases']:
        for group in ('B0','B2'):
            before=len(ledger.rows);provider=None
            try:
                provider=factory(group)
                label=dict(case_id=case['case_id'],group=group)
                if group=='B2':
                    memory,ctx,q=make_fixture(case)
                    row=run_active_v07(memory,ctx,q,provider=provider,ledger=ledger,label=label)
                else:
                    model=AccountedModel(provider,ledger,label=label)
                    try:
                        response=model.generate([Message.model_validate(m) for m in case['b0_messages']],tools=[])
                        raw=response.content if response.finish_reason=='stop' and not response.tool_calls else None
                        row=dict(runtime_status='completed' if raw else 'incomplete',raw_answer=raw,stop_reason=None,
                            validation=validate_answer(raw,visible_ids(case['b0_reference'])),tool_calls=0,
                            budget_events=model.account.events,delivered_requests=model.delivered,last_delivered_reference=case['b0_reference'])
                    except BudgetBlocked as error:
                        row=dict(runtime_status='partial',raw_answer=None,stop_reason=error.code,
                            validation=validate_answer(None,set()),tool_calls=0,
                            budget_events=model.account.events,delivered_requests=model.delivered,last_delivered_reference={})
                used=ledger.rows[before:]
                row.update(case_id=case['case_id'],group=group,provider_calls=len(used),provider_responses=used,
                    input_tokens=sum((r.get('usage') or {}).get('input_tokens',0) for r in used),
                    output_tokens=sum((r.get('usage') or {}).get('output_tokens',0) for r in used),
                    provider_latency_ms=sum(r['latency_ms'] for r in used),
                    estimated_cost_usd=sum(r['cost_peak_usd'] or 0 for r in used),semantic_review='NOT_RUN')
                cells.append(row);ledger.record(dict(event='cell',**row))
                reason=row.get('stop_reason')
                if group=='B2' and reason in BUDGET_STOPS:
                    status='STOPPED_ON_B2_BUDGET';stop=reason;break
                if ledger.stop_reason or reason in {'provider_error_no_retry','usage_unknown','usage_exceeds_reservation','external_call_limit','fee_limit'}:
                    status='STOPPED';stop=reason or ledger.stop_reason;break
            except Exception:
                status='STOPPED';stop=ledger.stop_reason or 'local_execution_error';break
            finally:
                if provider is not None:provider.close()
        if status!='COMPLETE':break
    return dict(status=status,stop_reason=stop,backend=backend,cells=cells,provider_calls=ledger.calls,
        reserved_peak_usd=float(ledger.reserved),known_usage_peak_estimate_usd=float(ledger.cost),
        invoice='NOT_AVAILABLE' if backend=='REAL' else 'NOT_APPLICABLE_MOCK',
        actual_external_requests=ledger.calls if backend=='REAL' else 0,
        automatically_retried_requests=0,judge_calls=0,private_imports=0)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['prepare','run'])
    parser.add_argument('--pack',type=Path);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--authorization',type=Path);parser.add_argument('--allow-live',action='store_true')
    parser.add_argument('--credentials-file',type=Path);parser.add_argument('--proxy')
    args=parser.parse_args()
    if args.output.exists():parser.error('不覆盖已有产物')
    if args.mode=='prepare':
        pack=prepare_pack();args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_bytes(gzip.compress(encoded(pack),mtime=0))
        print(json.dumps(dict(cases=3,max_requests=12,fee_cap_usd=.5,
            estimated_upper_usd=pack['upper_fee_estimate_usd'],pack_sha256=sha256(args.output.read_bytes()).hexdigest())))
        return
    if not args.allow_live or not args.authorization or not args.pack or not args.credentials_file or not args.proxy:
        parser.error('需要本轮授权/allow-live/明确凭据与代理；尚未读取凭据')
    pack=load(args.pack);digest=sha256(args.pack.read_bytes()).hexdigest()
    permission=json.loads(args.authorization.read_text(encoding='utf-8'));validate_authorization(pack,permission,digest)
    claim=ROOT/'reports/memory_v07/claims'/(sha256((permission['user_confirmation_reference']+digest).encode()).hexdigest()+'.json')
    journal=args.output.with_suffix('.jsonl')
    if claim.exists() or journal.exists():raise FileExistsError('授权已被启动，不自动重试或复用')
    # 本地配置预检已经完成，之后才读取显式凭据。
    settings=DeepSeekSettings.from_credentials_file(args.credentials_file,model='deepseek-flash',reasoning_effort='high',
        max_requests=3,max_output_tokens=4096,timeout_seconds=180.0)
    write(claim,dict(pack_sha256=digest,user_confirmation_reference=permission['user_confirmation_reference']))
    from min_agent.llm.deepseek import DeepSeekResponsesModel
    with journal.open('x',encoding='utf-8') as stream:
        def record(event):stream.write(json.dumps(event,ensure_ascii=False)+'\n');stream.flush()
        ledger=CallLedger(max_requests=12,max_cost_usd=.5,journal=record)
        result=run_trial(pack,factory=lambda group:DeepSeekResponsesModel(settings,proxy=args.proxy),ledger=ledger,backend='REAL')
        result.update(pack_sha256=digest,source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip())
        record(dict(event='finished',**{k:v for k,v in result.items() if k!='cells'}))
    write(args.output,result)
    print(json.dumps({k:v for k,v in result.items() if k not in {'cells'}}))


if __name__=='__main__':main()
