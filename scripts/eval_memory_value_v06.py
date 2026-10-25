"""v06固定五组取证实验。默认离线；实时入口须新授权、哈希与费用硬限。"""
import argparse
from datetime import timedelta
import gzip
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from eval_memory_quality_v03 import load_inputs
from prepare_memory_value_v05 import synthetic
from min_agent.long_memory.answer_eval import prepare_answer_case,encoded
from min_agent.long_memory.active_evidence import group_messages,visible_ids,validate_answer,run_active
from min_agent.long_memory.evaluation import build_approved_fixture,timestamp,normalized
from min_agent.long_memory.models import Query
from min_agent.long_memory.task_evidence import select_task_evidence
from min_agent.long_memory.evidence_budget import CallLedger,MeteredModel
from min_agent.models import ModelResponse,ToolCall

GROUPS=('A','B0','B1','B2','C')
MODEL='deepseek-flash'
MAX_CALLS=105
FEE_CAP=2


def load(path):return json.loads(gzip.decompress(path.read_bytes()))
def dump(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8',newline='\n') as stream:json.dump(value,stream,ensure_ascii=False,indent=2)
def sources():
    paths=[*ROOT.glob('src/min_agent/**/*.py'),ROOT/'src/min_agent/prompts/system.md',Path(__file__).resolve(),
           ROOT/'tests/fixtures/memory_v06_holdout.json',ROOT/'reports/memory_v06/policy-freeze.json']
    return {p.relative_to(ROOT).as_posix():sha256(p.read_bytes()).hexdigest() for p in paths}
def clean_case(case):return {k:case[k] for k in ('test_id','category','user_question','conversation_histories')}
def fixture(case):
    memory,ctx,_=build_approved_fixture(case['fixture'],approval='approved_fixture')
    at=timestamp(case['as_of']);memory.clock=lambda:at
    return memory,ctx,Query(text=case['question'],as_of=at)


def prepare(book_root,output):
    freeze=json.loads((ROOT/'reports/memory_v06/policy-freeze.json').read_text(encoding='utf-8'))
    assert all(sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in freeze['sha256'].items())
    _,book,_=load_inputs(book_root/'chapter3/user-memory-evaluation')
    previous=load(ROOT/'reports/memory_v05/prepared-pilot.json.gz')
    synth={c['test_id']:c for c in synthetic()};selected=[]
    for old in previous['cases']:
        original=book.get(old['case_id'],synth.get(old['case_id']))
        specs=[old['source_positions'][e['id']] for e in old['oracle_evidence']]
        selected.append((original,specs,old['as_of'],old['gold'],'v05_seen_dev'))
    held=json.loads((ROOT/'tests/fixtures/memory_v06_holdout.json').read_text(encoding='utf-8'))
    for original in held['cases']:
        specs=[dict(conversation_id=s,message_index=n) for s,n in original['oracle_positions']]
        at=original.get('as_of')
        selected.append((original,specs,at,dict(required_facts=original['required_facts']),'new_synthetic_holdout'))
    cases=[]
    for original,specs,at,gold,split in selected:
        clean=clean_case(original)
        old=prepare_answer_case(clean,oracle_spec=specs,as_of=at)
        memory,ctx,info=build_approved_fixture(clean,approval='approved_fixture')
        instant=timestamp(old['as_of']);memory.clock=lambda:instant
        query=Query(text=old['question'],as_of=instant)
        first=select_task_evidence(memory,ctx,query,budget=4096)
        old_c=json.loads(old['groups']['C']['messages'][-1]['content']).get('untrusted_user_memory',{})
        views={'A':{},'B0':old['production_view'],'B1':first['view'],'C':old_c}
        # 先完成全部决策输入；Gold只在随后的本地诊断列中使用。
        requests={g:[m.model_dump(mode='json') for m in group_messages(old['question'],instant,v)] for g,v in views.items()}
        cases.append(dict(case_id=old['case_id'],split=split,question=old['question'],as_of=old['as_of'],
            fixture=clean,views=views,requests=requests,gold=gold,
            source_episodes=old['source_episodes'],source_positions=old['source_positions'],
            b0_trace=old['production_trace'],b1_trace=first['trace'],
            evaluation_audit=next((c['evaluation_audit'] for c in previous['cases'] if c['case_id']==old['case_id']),[])))
    pack=dict(protocol='v06-readonly-five-groups-v1',base_commit='a399c79',
        policy_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        source_hashes=sources(),cases=cases,model=MODEL,reasoning_effort='high',max_output_tokens=4096,
        reference_bytes_limit=4096,request_total_units=32768,b2_total_units=65536,b2_max_model_calls=3,b2_max_tool_calls=4,
        max_requests=len(cases)*7,max_cost_usd=FEE_CAP,external_judge=False,private_imports=0,
        official_pricing_url='https://api-docs.deepseek.com/quick_start/pricing/',peak_rates={'input':.30,'output':1.20},
        conservative_batch_fee_upper_usd=len(cases)*(.30*(4*(32768-4096)+65536-3*4096)+1.20*7*4096)/1_000_000)
    if output.exists():raise FileExistsError('不覆盖冻结包')
    output.write_bytes(gzip.compress(encoded(pack),mtime=0))
    return dict(cases=len(cases),splits={s:sum(c['split']==s for c in cases) for s in {c['split'] for c in cases}},
                pack_sha256=sha256(output.read_bytes()).hexdigest(),max_requests=pack['max_requests'],
                conservative_batch_fee_upper_usd=pack['conservative_batch_fee_upper_usd'])


def surface(text,fact):
    values=fact.get('variants',[]) if isinstance(fact,dict) else fact if isinstance(fact,list) else [fact]
    return any(normalized(value) in normalized(text) for value in values)
def offline(pack):
    rows=[]
    for case in pack['cases']:
        for group in ('A','B0','B1','C'):
            view=case['views'][group];facts=case['gold']['required_facts']
            text=json.dumps(view,ensure_ascii=False)
            matches=[surface(text,f) for f in facts]
            rows.append(dict(case_id=case['case_id'],split=case['split'],group=group,
                visible_fact_surface_matches=matches,visible_fact_surface_proxy=sum(matches)/len(matches) if matches else None,
                reference_bytes=len(encoded(view)) if view else 0,visible_ids=sorted(visible_ids(view)),
                answer='NOT_RUN',answer_support_rate='NOT_RUN',unsupported_assertions='NOT_RUN',
                disambiguation_omissions='NOT_RUN',citation_validity='NOT_RUN',json_validity='NOT_RUN',
                provider_tokens='NOT_RUN',provider_cost='NOT_RUN'))
    controls=[]
    class Script:
        def __init__(self):self.requests=0
        def generate(self,messages,*,tools=None):
            self.requests+=1;results=[m for m in messages if m.role=='tool']
            if not results:
                call=ToolCall(id='search',name='search_long_memory',arguments={'question':next(m.content for m in messages if m.role=='user')})
            elif len(results)==1:
                ids=json.loads(json.loads(results[-1].content)['result'])['memory_ids'][:8]
                if ids:call=ToolCall(id='read',name='read_long_memory_evidence',arguments={'memory_ids':ids})
                else:return ModelResponse(finish_reason='stop',content='{"answer":"SCRIPTED_CONTROL_ONLY","used_evidence_ids":[],"uncertainties":[]}')
            else:
                view=json.loads(messages[-1].content).get('untrusted_user_memory',{})
                return ModelResponse(finish_reason='stop',content=json.dumps(dict(answer='SCRIPTED_CONTROL_ONLY',
                    used_evidence_ids=sorted(visible_ids(view)),uncertainties=[])))
            return ModelResponse(finish_reason='tool_calls',tool_calls=[call])
    for case in pack['cases']:
        memory,ctx,query=fixture(case);row=run_active(memory,ctx,query,model=Script())
        row.update(case_id=case['case_id'],split=case['split'],provider_calls=0,
            nature='SCRIPTED_CONTROL_NOT_MODEL_QUALITY',answer_quality='NOT_RUN',provider_tokens='NOT_RUN',provider_cost='NOT_RUN')
        controls.append(row)
    return dict(rows=rows,b2_scripted_controls=controls,provider_calls=0,real_answer_metrics='NOT_RUN',
                note='visible surface proxy is not semantic correctness or model answer coverage; B2 controls are only wiring checks')


def validate_permission(pack,auth,pack_hash):
    if not isinstance(auth,dict) or auth.get('approved_by_user') is not True:
        raise PermissionError('没有本轮明确授权')
    expected=dict(approved_by_user=True,pack_sha256=pack_hash,model=MODEL,reasoning_effort='high',
        data_scope='public_book_and_synthetic',external_judge=False,max_requests=pack['max_requests'],max_cost_usd=FEE_CAP,
        tools=['search_long_memory','read_long_memory_topic','read_long_memory_evidence'])
    if any(auth.get(k)!=v for k,v in expected.items()) or not auth.get('user_confirmation_reference'):
        raise PermissionError('需要本轮针对固定包、105请求上限/2美元、三个只读工具的新授权')
    if pack['max_requests']>MAX_CALLS or pack['model']!=MODEL or pack['reasoning_effort']!='high':raise PermissionError('不允许扩展冻结配置')
    for key,value in dict(protocol='v06-readonly-five-groups-v1',max_output_tokens=4096,
        reference_bytes_limit=4096,request_total_units=32768,b2_total_units=65536,
        b2_max_model_calls=3,b2_max_tool_calls=4,peak_rates={'input':.30,'output':1.20}).items():
        if pack.get(key)!=value:raise PermissionError('冻结协议配置不匹配')
    if len(pack['cases'])*7!=pack['max_requests']:raise PermissionError('请求数与固定样本不匹配')
    if pack['conservative_batch_fee_upper_usd']>FEE_CAP:raise PermissionError('预留超上限')
    if sources()!=pack['source_hashes']:raise PermissionError('源码或数据已改变；尚未读取凭据')


def live(pack,auth,pack_hash,credentials,proxy,output):
    validate_permission(pack,auth,pack_hash)
    # 同一真实用户确认+请求包只允许一个批次，换输出文件名不能重复计费。
    claim_dir=ROOT/'reports/memory_v06/claims';claim_dir.mkdir(parents=True,exist_ok=True)
    claim=claim_dir/(sha256((auth['user_confirmation_reference']+pack_hash).encode()).hexdigest()+'.json')
    journal=output.with_suffix('.jsonl')
    if output.exists() or claim.exists() or journal.exists():raise FileExistsError('已存在批次，禁止重试')
    dump(claim,dict(pack_sha256=pack_hash,user_confirmation_reference=auth['user_confirmation_reference']))
    cells=[];provider=None
    with journal.open('x',encoding='utf-8') as stream:
        def record(event):stream.write(json.dumps(event,ensure_ascii=False)+'\n');stream.flush()
        ledger=CallLedger(max_requests=pack['max_requests'],max_cost_usd=FEE_CAP,journal=record)
        try:
            from min_agent.llm.deepseek import DeepSeekSettings,DeepSeekResponsesModel
            settings=DeepSeekSettings.from_credentials_file(credentials,model=MODEL,reasoning_effort='high',
                max_output_tokens=4096,max_requests=pack['max_requests'],timeout_seconds=180)
            provider=DeepSeekResponsesModel(settings,proxy=proxy)
            for number,case in enumerate(pack['cases']):
                order=list(GROUPS);offset=number%len(order);order=order[offset:]+order[:offset]
                for group in order:
                    before=len(ledger.rows)
                    model=MeteredModel(provider,ledger,total_units=65536 if group=='B2' else 32768,label=dict(case_id=case['case_id'],group=group))
                    if group=='B2':
                        memory,ctx,query=fixture(case);row=run_active(memory,ctx,query,model=model)
                    else:
                        from min_agent.models import Message
                        response=model.generate([Message.model_validate(m) for m in case['requests'][group]],tools=[])
                        raw=response.content if response.finish_reason=='stop' and not response.tool_calls else None
                        row=dict(raw_answer=raw,runtime_status='completed' if raw is not None else 'incomplete',
                            final_reference=case['views'][group],validation=validate_answer(raw,visible_ids(case['views'][group])),tool_calls=0)
                    used=ledger.rows[before:]
                    row.update(case_id=case['case_id'],split=case['split'],group=group,provider_requests=used,
                        provider_calls=len(used),input_tokens=sum((r['usage'] or {}).get('input_tokens',0) for r in used),
                        output_tokens=sum((r['usage'] or {}).get('output_tokens',0) for r in used),
                        latency_ms=sum(r['latency_ms'] for r in used),cost_peak_usd=sum(r['cost_peak_usd'] or 0 for r in used),
                        semantic_source_review='NOT_RUN')
                    cells.append(row);record(dict(event='cell',**row))
                    if ledger.stop_reason:raise RuntimeError('batch_stop')
        except Exception:
            record(dict(event='batch_stopped',reason=ledger.stop_reason or 'local_or_provider_failure_no_retry'))
        finally:
            if provider is not None:provider.close()
        result=dict(cells=cells,model_calls=ledger.calls,reserved_usd=float(ledger.reserved),cost_peak_usd=float(ledger.cost),
            invoice='NOT_AVAILABLE',automatic_retries=0,stop_reason=ledger.stop_reason,
            pack_sha256=pack_hash,status='COMPLETE' if len(cells)==len(pack['cases'])*5 else 'PARTIAL',
            source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip())
        record(dict(event='finished',**{k:v for k,v in result.items() if k!='cells'}))
    dump(output,result);return dict(status=result['status'],model_calls=result['model_calls'],cost_peak_usd=result['cost_peak_usd'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['prepare','offline','run'])
    parser.add_argument('--pack',type=Path);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--book-root',type=Path);parser.add_argument('--authorization',type=Path)
    parser.add_argument('--allow-live',action='store_true');parser.add_argument('--credentials-file',type=Path);parser.add_argument('--proxy')
    args=parser.parse_args()
    if args.output.exists():parser.error('不覆盖已有产物')
    if args.mode=='prepare':
        if not args.book_root:parser.error('需要book-root')
        result=prepare(args.book_root,args.output)
    else:
        if not args.pack:parser.error('需要冻结pack')
        pack=load(args.pack)
        if args.mode=='offline':result=offline(pack);dump(args.output,result);result=dict(rows=len(result['rows']),controls=len(result['b2_scripted_controls']),provider_calls=0)
        else:
            if not args.allow_live or not args.authorization or not args.credentials_file or not args.proxy:
                parser.error('缺本轮授权/allow-live/显式凭据/代理；尚未读取凭据')
            auth=json.loads(args.authorization.read_text(encoding='utf-8'))
            result=live(pack,auth,sha256(args.pack.read_bytes()).hexdigest(),args.credentials_file,args.proxy,args.output)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
