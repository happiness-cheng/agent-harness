"""延续首批32次额度：保留14次历史，不重复已通过的写入场景。"""
import argparse
from pathlib import Path
from tempfile import mkdtemp
from time import perf_counter
from uuid import uuid4
import json

from min_agent.application import HarnessApplication
from min_agent.artifacts import ArtifactStore
from min_agent.budget import BudgetLedger,BudgetLimits
from min_agent.compaction import Compactor
from min_agent.context import request_bound
from min_agent.llm.client import LLMError
from min_agent.llm.deepseek import DeepSeekResponsesModel,DeepSeekSettings
from min_agent.models import Message
from min_agent.research import ResearchService
from min_agent.sessions import SessionStore
from min_agent.tools.search import SearchService
from min_agent.tools.read_webpage import PublicFetcher,WebReader

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--allow-live',action='store_true')
    parser.add_argument('--credentials-file',required=True);parser.add_argument('--proxy');args=parser.parse_args()
    if not args.allow_live:raise PermissionError('未读取凭据')
    previous=json.loads(Path('docs/acceptance/live-20261020.json').read_text(encoding='utf-8'))
    used=previous['requests'];assert used==14
    path=Path('docs/acceptance/live-followup-20261020.json')
    if path.exists():raise FileExistsError('不重复运行同批请求')
    report={'previous_requests':used,'batch_cap':32,'remaining_cap':32-used,'cases':{},'calls':[]}
    model=DeepSeekResponsesModel(DeepSeekSettings.from_credentials_file(args.credentials_file,max_requests=32-used,max_output_tokens=4096),proxy=args.proxy)
    def save():
        report.update(requests=model.request_count,combined_requests=used+model.request_count,usage=model.usage)
        path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    class Observed:
        def generate(self,messages,*,tools=None):
            record={'tools':[tool['name'] for tool in tools or []],'request_bound':request_bound(messages,tools)}
            record['tool_results']=[message.content[:600] for message in messages[-5:] if message.role=='tool']
            started=perf_counter()
            try:
                result=model.generate(messages,tools=tools);record.update(finish_reason=result.finish_reason,
                    calls=[{'name':call.name,'arguments':call.arguments} for call in result.tool_calls])
                return result
            except LLMError as error:
                record.update(error=error.code,diagnostics=error.diagnostics);raise
            finally:
                record['seconds']=round(perf_counter()-started,3);report['calls'].append(record);save()
    observed=Observed();save()
    def case(name,work):
        count=model.request_count;started=perf_counter()
        try:result=work()
        except Exception as error:result={'ok':False,'error_category':type(error).__name__}
        report['cases'][name]={**result,'requests':model.request_count-count,'seconds':round(perf_counter()-started,3)}
        save();print(json.dumps({'case':name,**report['cases'][name]},ensure_ascii=False),flush=True)
    try:
        def research():
            store=SessionStore();context=store.create(uuid4());artifacts=ArtifactStore(store,context)
            fetcher=PublicFetcher(max_requests=3)
            try:
                ledger=BudgetLedger(BudgetLimits(model_calls=5,tool_calls=6))
                result=ResearchService(observed,SearchService(proxy=args.proxy,max_queries=2),WebReader(fetcher,artifacts),artifacts).run(
                    '只核查SQLite同一数据库能否有多个同时写事务。做一次公开搜索，选返回的官方来源阅读；找到说明即可停止，只引用一条很短的逐字原文并给出限制，不做背景扩展。',
                    ledger.start_segment(BudgetLimits(model_calls=5,tool_calls=6)))
                return {'ok':result['status']=='completed' and bool((result.get('evidence') or {}).get('citations')),**result,'read_requests':fetcher.requests,'semantic_review':'pending'}
            finally:fetcher.close()
        case('research_after_reserve_fix',research)
        def compaction():
            store=SessionStore();context=store.create(uuid4());ledger=BudgetLedger(BudgetLimits(model_calls=1,tool_calls=0))
            source=[Message(role='user',content='甲的截止日期是2026-10-22，不是2026-10-21；不要删除原文；乙的12.30元尚未支付。本任务仅核对，不要求写入，没有待办事项。')]
            result=Compactor(observed,store,context,ledger.start_segment(BudgetLimits(model_calls=1,tool_calls=0))).compact_history(
                {'version':1,'projections':{},'summary':None,'covered':0,'covered_hashes':[]},source,['source'],1)
            return {'ok':result['summary']['open_questions']==[],'summary':result['summary'],'semantic_review':'pending'}
        case('compaction_no_invented_work',compaction)
        def reminders():
            harness=HarnessApplication(Path(mkdtemp(prefix='agent-harness-live-reminder-'))/'verification.sqlite3',model=observed)
            try:
                identity=harness.create_session('合成提醒验收')['session_id']
                harness.submit(identity,'请先提案创建单次提醒“合成提醒验收”，明确时刻2026-10-21T09:00:00+08:00，时区Asia/Shanghai，不关联todo。等用户在应用确认范围后再创建。')
                harness.wait(identity,timeout=180);first=harness.snapshot(identity);proposal=first['work_state']['proposal']
                if proposal is None:return {'ok':False,'phase':'proposal','state':first['task']}
                harness.set_mode(identity,'build');harness.confirm(identity,proposal['proposal_id'])
                harness.resume(identity,task_id=first['task']['task_id'],text='应用用户已定向确认此提案，请按真实工具结果执行并核对。')
                harness.wait(identity,timeout=180);final=harness.snapshot(identity)
                return {'ok':final['task']['status']=='completed' and len(final['reminders'])==1 and not final['todos'],
                    'status':final['task']['status'],'reminders':final['reminders'],
                    'answer':next((m['content'] for m in reversed(final['messages']) if m['role']=='assistant' and m['content']),None)}
            finally:harness.close()
        case('application_reminder',reminders)
    finally:save();model.close()
    return 0 if len(report['cases'])==3 and all(case['ok'] for case in report['cases'].values()) else 1

if __name__=='__main__':raise SystemExit(main())
