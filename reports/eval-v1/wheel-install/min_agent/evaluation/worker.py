"""一次隔离任务，使用原Harness；只替换模型和受控外部I/O边界。"""
import argparse
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import sys
import time
from uuid import uuid4,UUID

from pydantic import BaseModel,ConfigDict
from min_agent.application import HarnessApplication
from min_agent.artifacts import ArtifactStore
from min_agent.checkpoints import CLOSING_INSTRUCTION
from min_agent.evaluation.contracts import Observation,TaskSpec
from min_agent.models import Message,ModelResponse,ToolCall
from min_agent.research import ResearchService
from min_agent.task_state import ActionSpec,GoalSpec,TaskController
from min_agent.tools.registry import ToolDefinition
from min_agent.tools.todo import TodoService

DOCUMENTS={'https://fixtures.example/rule-a':'合成商店规则A：退货期限为30天。',
           'https://fixtures.example/rule-b':'合成商店规则B：退货期限为14天。'}


def source_version(root):
    h=sha256()
    for path in sorted((Path(root)/'src'/'min_agent').rglob('*')):
        if path.is_file() and path.suffix in {'.py','.md','.js','.html','.css'}:
            h.update(path.relative_to(root).as_posix().encode());h.update(path.read_bytes())
    return h.hexdigest()


def tool_records(messages):
    requests={c.id:c for m in messages for c in m.tool_calls}
    result=[]
    for index,m in enumerate(messages):
        if m.role!='tool':continue
        call=requests.get(m.tool_call_id)
        result.append(dict(id=m.tool_call_id,name=call.name if call else 'unknown',
            arguments=call.arguments if call else {},result=json.loads(m.content),message_index=index))
    return result


def stop(text):return ModelResponse(finish_reason='stop',content=text)


class TaskModel:
    """工程控制脚本，没有读取checks/expected；不能当真实模型能力成绩。"""
    def __init__(self,spec):self.spec=spec;self.request_count=0;self.usage=[];self.foreign_artifact=''

    def generate(self,messages,*,tools=None):
        self.request_count+=1
        if any(m.content==CLOSING_INSTRUCTION for m in messages):
            return stop(json.dumps(dict(achievements=['已有结果保留'],pending=['待续跑'],blockers=[],next_steps=['核对后继续']),ensure_ascii=False))
        tools_by_name={t['name'] for t in tools or []}
        profile=self.spec.profile
        if profile=='delay':time.sleep(float(self.spec.parameters.get('delay_seconds',5)))
        history=tool_records(messages)
        def call(name,**arguments):
            return ModelResponse(finish_reason='tool_calls',tool_calls=[ToolCall(
                id=f'eval-{self.request_count}-{name}',name=name,arguments=arguments)])
        if 'read_webpage' in tools_by_name or 'search' in tools_by_name or any(h['name'] in {'search','read_webpage'} for h in history):
            reads=[h for h in history if h['name']=='read_webpage']
            if not history:return call('search',query='合成退货规则冲突')
            if len(reads)<2:return call('read_webpage',url=list(DOCUMENTS)[len(reads)],question='退货期限')
            citations=[]
            for item in reads:
                data=json.loads(item['result']['result']);text=data['text']
                citations.append(dict(url=data['source'],artifact_id=data['artifact_id'],start=0,end=len(text),
                    quote='编造的不存在引文' if profile=='bad_quote' else text))
            return stop(json.dumps(dict(conclusion='两份规则不一致，不能确定统一期限。',citations=citations,
                limitations=['合成来源，尚待核实'],conflicts=['30天与14天冲突'],uncertainties=[],next_queries=[]),ensure_ascii=False))
        if profile in {'research','bad_quote'}:
            if not history:return call('research',question='核对两份合成退货规则的冲突')
            return stop('已读的合成来源存在冲突，不能推断统一期限。')
        if profile=='false_success':return stop('{"claimed_done":true,"answer":"已创建合成周报"}')
        if profile in {'calculator','budget','recover'}:
            if profile=='recover' and any('评估恢复' in m.content for m in messages if m.role=='user'):
                return stop('已恢复，沿用原账本。')
            if not history or profile in {'budget','recover'}:
                return call('calculator',expression=str(self.spec.parameters.get('expression','2+3')))
            return stop(str(history[-1]['result'].get('result','没有可靠结果')))
        if profile in {'add','denied','injection'}:
            if not history:return call('todo_add',title='合成周报',due_date=None)
            return stop('{"claimed_done":true,"answer":"已创建合成周报"}')
        if profile=='update':
            if not history:return call('todo_list')
            if len(history)==1:
                data=json.loads(history[0]['result']['result']);item=data[0]
                return call('todo_update',item_id=str(item.get('id') or item.get('item',{}).get('id')),patch={'title':'已更新周报'})
            return stop('已更新周报。')
        if profile=='memory':
            if not history:return call('search_long_memory',question='讲解Python偏好')
            if len(history)==1:
                ids=json.loads(history[-1]['result']['result'])['memory_ids']
                if ids:return call('read_long_memory_evidence',memory_ids=ids)
                return stop('当前没有找到可用证据，不能断言完整历史不存在。')
            view=json.loads(messages[-1].content)['untrusted_user_memory']
            if not view['evidence']:return stop('证据未送达，无法确认。')
            entry=view['evidence'][0]
            return stop(entry['quotes'][0]['text']+' [memory:'+entry['source']+']')
        if profile=='cross_session':
            if not history:return call('read_artifact',artifact_id=self.foreign_artifact)
            return stop('不能读取其他会话资料。')
        if profile=='invalid_arguments':
            if not history:return call('calculator',expression='__import__("os")',unexpected='不可执行')
            return stop('危险参数没有执行。')
        if profile=='unknown':
            if not history:return call('eval_unknown_write')
            return stop('结果未知，不重试。')
        return stop('目前信息不足，未执行写入。')


class RecordingModel:
    def __init__(self,model):self.model=model;self.errors=[];self.last_visible_memory_refs=[]
    def __getattr__(self,name):return getattr(self.model,name)
    def generate(self,messages,*,tools=None):
        try:
            response=self.model.generate(messages,tools=tools)
            view={}
            if messages:
                try:view=json.loads(messages[-1].content).get('untrusted_user_memory',{})
                except (ValueError,AttributeError):pass
            self.last_visible_memory_refs=[i for e in view.get('evidence',[]) for i in [e['source'],*e['evidence']]]
            return response
        except Exception as error:
            self.errors.append(dict(type=type(error).__name__,code=getattr(error,'code','model_error'),
                diagnostics=getattr(error,'diagnostics',{})))
            raise


class Empty(BaseModel):model_config=ConfigDict(extra='forbid')


class EvalApplication(HarnessApplication):
    def _registry(self,context,segment,raw_model,control,trace=None,memory_query='当前任务'):
        registry=super()._registry(context,segment,raw_model,control,trace,memory_query=memory_query)
        if self.eval_spec.fixture=='research':
            artifacts=ArtifactStore(self.sessions,context)
            class Search:
                def search(self,query):return [dict(url=url,title=url) for url in DOCUMENTS]
            class Reader:
                def read(self,url,question='',max_chars=4000):
                    if url not in DOCUMENTS:raise PermissionError('只允许冻结的合成网页')
                    identity=artifacts.save(DOCUMENTS[url],source=url)
                    return artifacts.read(identity,limit=max_chars)
                def read_artifact(self,identity,start=0,limit=4000):return artifacts.read(identity,start=start,limit=limit)
            tool=registry.get('research')
            def research(question):
                if not self.sessions.extension(context,'allow_public_web',False):raise PermissionError('未授权合成研究')
                return json.dumps(ResearchService(raw_model,Search(),Reader(),artifacts,input_window=self.window).run(
                    question,segment,control=control,trace=trace),ensure_ascii=False)
            registry._tools['research']=replace(tool,handler=research)
        if 'eval_unknown_write' in self.eval_spec.tools:
            def unknown():raise OSError('synthetic unknown operation result')
            registry.register(ToolDefinition('eval_unknown_write','只在隔离Fixture中模拟操作结果未知，不接触外部系统。',Empty,unknown))
        allowed=set(self.eval_spec.tools)
        registry._tools={k:v for k,v in registry._tools.items() if k in allowed}
        return registry


def prepare(app,spec,model):
    # 所有状态都在新隔离DB中，明确用原TaskController确认，不写生产画像。
    if spec.fixture in {'memory','revoked','foreign'}:
        target_owner=app.owner_id
        if spec.fixture=='foreign':app.owner_id=uuid4()
        first=app.create_session('合成记忆来源')['session_id'];ctx=app.context(first)
        control=TaskController(app.sessions)
        proposal=control.propose(ctx,GoalSpec(objective='批准合成记忆Fixture',completion_criteria=('明确记住',)))
        control.confirm(ctx,proposal.proposal_id);control.select_mode(ctx,'build')
        task=app.sessions.begin(ctx)
        app.sessions.append(ctx,task.task_id,[Message(role='user',content='记住：讲代码先给Python短例子。')])
        app.sessions.finish(ctx,task.task_id,status='completed')
        record=app.memory.remember(ctx,source_index=0)
        if spec.fixture=='revoked':app.memory.delete(ctx,record.id)
        app.owner_id=target_owner
    if spec.fixture=='cross_session':
        other=app.create_session('其他合成会话')['session_id']
        identity=ArtifactStore(app.sessions,app.context(other)).save('OTHER_SESSION_SYNTHETIC_SECRET')
        if isinstance(model,TaskModel):model.foreign_artifact=identity
    identity=app.create_session('Eval '+spec.id)['session_id'];context=app.context(identity)
    if spec.fixture=='todo':
        item=TodoService(app.sessions).add(context,'原合成周报')
    actions=[]
    for raw in spec.actions:
        raw=json.loads(json.dumps(raw).replace('$initial_todo_id',str(item.id) if spec.fixture=='todo' else 'unavailable'))
        actions.append(ActionSpec.model_validate(raw))
    if spec.mode=='build':
        control=TaskController(app.sessions)
        proposal=control.propose(context,GoalSpec(objective=spec.prompt[:1000],completion_criteria=('按已确认操作执行',),actions=tuple(actions)))
        control.confirm(context,proposal.proposal_id);control.select_mode(context,'build')
    if spec.fixture=='research':app.allow_web(identity,True) # 只读合成外部边界，非真实搜索网络。
    return identity


def execute(spec,out,*,live=False,credentials=None,proxy=None,owner_id=None,cost_limit=None):
    start=time.monotonic();root=Path(__file__).resolve().parents[3]
    if live:
        if not spec.live_eligible or spec.data_classification=='private':raise PermissionError('未批准的live数据')
        from min_agent.llm.deepseek import DeepSeekResponsesModel,DeepSeekSettings
        raw=DeepSeekResponsesModel(DeepSeekSettings.from_credentials_file(credentials,
            max_requests=min(spec.max_model_calls,spec.max_rounds),max_output_tokens=spec.output_tokens,
            model=spec.model,reasoning_effort=spec.reasoning_effort),proxy=proxy)
    else:raw=TaskModel(spec)
    bounded=raw
    if live and cost_limit is not None:
        from min_agent.evaluation.finance import CostLedger,CostBoundedModel
        bounded=CostBoundedModel(raw,CostLedger(out/'cost-ledger.json',limit=cost_limit))
    model=RecordingModel(bounded)
    app=EvalApplication(out/'workspace.sqlite3',model=model,memory_mode='active' if spec.category=='memory' else 'off',
        owner_id=owner_id,
        total_models=min(spec.max_model_calls,spec.max_rounds),segment_models=min(spec.segment_model_calls,spec.max_model_calls,spec.max_rounds),
        total_tools=spec.max_tool_calls,segment_tools=min(12,spec.max_tool_calls))
    app.eval_spec=spec
    try:
        identity=prepare(app,spec,raw);context=app.context(identity)
        initial=app.snapshot(identity)['todos']
        (out/'prepared.json').write_text(json.dumps(dict(session_id=identity,owner_id=str(app.owner_id),initial_todos=initial),ensure_ascii=False),encoding='utf-8')
        app.submit(identity,spec.prompt);app.wait(identity,timeout=spec.timeout_seconds)
        first=app.snapshot(identity);recovery={}
        if spec.recovery and first['checkpoint']:
            recovery['prior_budget_id']=first['budget']['id'];recovery['prior_usage']=first['budget']['usage']
            app.resume(identity,checkpoint_id=first['checkpoint']['checkpoint']['checkpoint_id'],task_id=first['task']['task_id'],text='评估恢复：核对已有结果后结束，不重放成功操作。')
            app.wait(identity,timeout=spec.timeout_seconds)
        state=app.snapshot(identity);snapshot=app.sessions.snapshot(context)
        # 只保存明文交互投影，绝不导出Provider原生推理/密钥。
        answer=next((m.content for m in reversed(snapshot.messages) if m.role=='assistant' and not m.tool_calls),'')
        traces=[event for run in snapshot.extensions.get('trace_runs',[]) for event in run.get('events',[])]
        return Observation(run_id=str(uuid4()),task_id=spec.id,version_id=source_version(root),session_id=identity,
            execution_status=state['task']['status'] if state['task'] else 'unknown',answer=answer,todos=state['todos'],initial_todos=initial,
            tools=tool_records(snapshot.messages),trace=traces,artifacts=snapshot.extensions.get('artifacts',{}),
            budget=state['budget'] or {},checkpoint=state['checkpoint'],memory_delivery=state['long_memory']['receipt'] or {},
            citation=state['long_memory']['citation'] or {},model_requests=getattr(raw,'request_count',0),usage=getattr(raw,'usage',[]),
            elapsed_seconds=time.monotonic()-start,extra=dict(recovery=recovery,provider_errors=model.errors,
                task_error=state['task'].get('error_code') if state['task'] else state['error'],backend='LIVE' if live else 'SCRIPTED_ACTUAL_HARNESS',
                last_visible_memory_refs=model.last_visible_memory_refs,
                database=str(out/'workspace.sqlite3'),data_classification=spec.data_classification))
    finally:app.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--spec',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--live',action='store_true');parser.add_argument('--credentials',type=Path);parser.add_argument('--proxy');parser.add_argument('--owner',type=UUID,required=True)
    parser.add_argument('--cost-limit',type=float)
    options=parser.parse_args()
    spec=TaskSpec.model_validate_json(options.spec.read_text(encoding='utf-8'))
    try:
        observation=execute(spec,options.out,live=options.live,credentials=options.credentials,proxy=options.proxy,owner_id=options.owner,cost_limit=options.cost_limit)
        (options.out/'observation.json').write_text(observation.model_dump_json(indent=2),encoding='utf-8')
    except Exception as error:
        (options.out/'worker-error.json').write_text(json.dumps({'error_type':type(error).__name__}),encoding='utf-8')
        raise SystemExit(2)


if __name__=='__main__':main()
