"""显式授权后的固定公开/合成验收；报告排除凭据与原生推理正文。"""

import argparse
from datetime import UTC, datetime
from pathlib import Path
import json
import tempfile
from time import perf_counter
from uuid import uuid4

from min_agent.application import HarnessApplication
from min_agent.artifacts import ArtifactStore
from min_agent.budget import BudgetLedger, BudgetLimits
from min_agent.checkpoints import CheckpointPolicy
from min_agent.compaction import Compactor
from min_agent.context import request_bound
from min_agent.llm.client import LLMError
from min_agent.llm.deepseek import DeepSeekResponsesModel, DeepSeekSettings
from min_agent.models import Message
from min_agent.research import ResearchService
from min_agent.runtime import Runtime
from min_agent.sessions import SessionStore
from min_agent.tools.calculator import CALCULATOR_TOOL
from min_agent.tools.read_webpage import PublicFetcher, WebReader
from min_agent.tools.registry import ToolRegistry
from min_agent.tools.search import SearchService


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--allow-live', action='store_true')
    parser.add_argument('--credentials-file', required=True)
    parser.add_argument('--proxy')
    parser.add_argument('--report', required=True)
    args = parser.parse_args()
    if not args.allow_live: raise PermissionError('未读取凭据，需显式授权')
    report_path = Path(args.report)
    if report_path.exists(): raise FileExistsError('报告已存在，禁止隐式重跑同批额度')
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {'started_at': datetime.now(UTC).isoformat(), 'provider': 'deepseek.responses',
        'model': 'deepseek-flash', 'reasoning_effort': 'high', 'max_requests': 32,
        'max_output_tokens': 4096, 'cases': {}, 'calls': [], 'requests': 0}
    model = DeepSeekResponsesModel(DeepSeekSettings.from_credentials_file(args.credentials_file,
        max_requests=32, max_output_tokens=4096), proxy=args.proxy)
    def save():
        report['requests'] = model.request_count; report['usage'] = model.usage
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    class Observed:
        def generate(self, messages, *, tools=None):
            start=perf_counter()
            record={'tools':[tool['name'] for tool in tools or []], 'request_bound':request_bound(messages, tools)}
            try:
                response=model.generate(messages, tools=tools)
                record.update(finish_reason=response.finish_reason, call_names=[call.name for call in response.tool_calls])
                return response
            except LLMError as error:
                record.update(error=error.code, diagnostics=error.diagnostics)
                raise
            finally:
                record['seconds']=round(perf_counter()-start,3); report['calls'].append(record);save()
    observed=Observed(); save()
    def limits(models, tools): return BudgetLimits(model_calls=models, tool_calls=tools)
    def case(name, work):
        before=model.request_count;start=perf_counter()
        try: result=work()
        except Exception as error: result={'ok':False,'error_category':type(error).__name__}
        report['cases'][name]={**result,'requests':model.request_count-before,'seconds':round(perf_counter()-start,3)}
        save();print(json.dumps({'case':name,**report['cases'][name]},ensure_ascii=False),flush=True)
        if any(item.get('error') in {'network_error','http_429','http_401','http_403'} for item in report['calls'][-1:]):
            raise RuntimeError('真实传输/额度失败，停止本批，不自动重试')
    try:
        def calculator():
            tools=ToolRegistry();tools.register(CALCULATOR_TOOL)
            ledger=BudgetLedger(limits(3,1))
            result=Runtime(observed,tools,budget=ledger.start_segment(limits(3,1))).run('请使用calculator计算0.1+0.2，根据真实工具结果回答。')
            return {'ok':result.status=='completed' and ledger.usage.tool_calls==1 and '0.3' in result.response.content,
                    'status':result.status,'answer':result.response.content if result.response else None}
        case('calculator',calculator)
        def checkpoint():
            ledger=BudgetLedger(limits(2,0))
            result=Runtime(observed,budget=ledger.start_segment(limits(1,0)),checkpoint_policy=CheckpointPolicy()).run(
                '合成任务：核对2026-10-22截止的事项，尚有未核实内容，没有进行任何保存；请如实整理，不能宣称完成。')
            cp=result.checkpoint
            return {'ok':result.status=='partial' and cp is not None and cp.closing_status=='returned',
                    'status':result.status,'checkpoint':cp.model_dump(mode='json') if cp else None}
        case('checkpoint',checkpoint)
        def compaction():
            sessions=SessionStore();context=sessions.create(uuid4());ledger=BudgetLedger(limits(2,0))
            compactor=Compactor(observed,sessions,context,ledger.start_segment(limits(2,0)))
            source=[Message(role='user',content='合成事实：甲的截止日期改为2026-10-22，不是2026-10-21；不要删除原文；乙的12.30元尚未支付。'),
                    Message(role='assistant',content='保留用户纠正、否定与未支付状态；尚未执行写操作。')]
            state={'version':1,'projections':{},'summary':None,'covered':0,'covered_hashes':[]}
            candidate=compactor.compact_history(state,source,['first','second'],2)
            summary=candidate['summary']; rendered=json.dumps(summary,ensure_ascii=False)
            return {'ok':'2026-10-22' in rendered and '2026-10-21' in rendered and '12.30' in rendered,
                    'summary':summary,'semantic_review':'pending'}
        case('compaction',compaction)
        def research():
            sessions=SessionStore();context=sessions.create(uuid4());artifacts=ArtifactStore(sessions,context)
            fetcher=PublicFetcher(max_requests=4)
            try:
                service=ResearchService(observed,SearchService(proxy=args.proxy,max_queries=3),WebReader(fetcher,artifacts),artifacts)
                ledger=BudgetLedger(limits(6,7))
                result=service.run('核查SQLite同一数据库是否允许多个同时写事务，优先官方来源。只需一条短的逐字原文引用，说明限制与不确定性。',ledger.start_segment(limits(6,7)))
                return {'ok':result['status']=='completed' and bool(result.get('evidence',{}).get('citations')),
                        **result,'read_requests':fetcher.requests,'semantic_review':'pending'}
            finally:fetcher.close()
        case('research',research)
        def application():
            path=Path(tempfile.mkdtemp(prefix='agent-harness-live-app-'))/'verification.sqlite3'
            harness=HarnessApplication(path,model=observed)
            try:
                identity=harness.create_session('实网合成验收')['session_id']
                harness.submit(identity,'请先用propose_goal提案：新增一条“验收合成待办”，没有截止日期，不设置提醒。等待用户在应用中确认范围后再写入。')
                harness.wait(identity,timeout=180)
                first=harness.snapshot(identity);proposal=first['work_state']['proposal']
                if proposal is None:return {'ok':False,'phase':'proposal','status':first['task'],'error':first['error']}
                harness.set_mode(identity,'build');harness.confirm(identity,proposal['proposal_id'])
                harness.resume(identity,task_id=first['task']['task_id'],text='用户在应用中已定向确认该提案，按确认范围执行并根据真实结果回答。')
                harness.wait(identity,timeout=180)
                final=harness.snapshot(identity)
                return {'ok':final['task']['status']=='completed' and len(final['todos'])==1 and not final['reminders'],
                        'status':final['task']['status'],'todo_count':len(final['todos']),
                        'answer':next((message['content'] for message in reversed(final['messages']) if message['role']=='assistant' and message['content']),None)}
            finally:harness.close()
        case('application',application)
    finally:
        report['finished_at']=datetime.now(UTC).isoformat();save();model.close()
    return 0 if all(item['ok'] for item in report['cases'].values()) and len(report['cases'])==5 else 1


if __name__=='__main__': raise SystemExit(main())
