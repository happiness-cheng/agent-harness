from threading import Event
from uuid import uuid4
import json

from min_agent.application import HarnessApplication
from min_agent.budget import BudgetLimits
from min_agent.budget_persistence import SessionBudgets
from min_agent.models import Message, ModelResponse, ToolCall
from min_agent.operations import operation_scope
from min_agent.sessions import SessionStore
from min_agent.storage import SQLiteStorage
from min_agent.tools.todo import TodoService
from tests.fakes.model import ScriptedModel
from tests.unit.test_checkpoint_resume import note_response


def test_application_checkpoint_restart_preserves_write_and_total(tmp_path):
    path = tmp_path/'restart.sqlite3'
    initial = ScriptedModel([ModelResponse(finish_reason='tool_calls',tool_calls=[
        ToolCall(id='once',name='todo_add',arguments={'title':'只写一次'}),
    ]), note_response()])
    harness=HarnessApplication(path,model=initial,segment_models=2,total_models=5)
    identity=harness.create_session('续跑')['session_id'];context=harness.context(identity)
    proposal=harness.propose(context,{'objective':'记录并核对','completion_criteria':['写入后核对'],
        'actions':[{'operation':'todo.add','parameters':{'title':'只写一次','due_date':None}}]})
    harness.set_mode(identity,'build');harness.confirm(identity,proposal.proposal_id)
    harness.submit(identity,'开始');harness.wait(identity)
    first=harness.snapshot(identity);item_id=first['todos'][0]['item']['id']
    assert first['task']['status']=='partial' and first['budget']['usage']['model_calls']==2
    cp=first['checkpoint']['checkpoint']['checkpoint_id'];harness.close()
    model=ScriptedModel([ModelResponse(finish_reason='stop',content='根据已有真实结果完成核对，无新增写入。')])
    harness=HarnessApplication(path,model=model,segment_models=2,total_models=5)
    try:
        harness.resume(identity,checkpoint_id=cp,text='核对已有记录，不再新增');harness.wait(identity)
        final=harness.snapshot(identity)
        assert final['task']['status']=='completed' and len(final['todos'])==1
        assert final['todos'][0]['item']['id']==item_id
        assert final['budget']['usage']['model_calls']==3 and final['budget']['usage']['tool_calls']==1
        assert any('核对已有记录' in message.content for message in model.calls[0])
    finally:harness.close()


def test_abrupt_running_state_is_unknown_then_read_verified_without_duplicate(tmp_path):
    path=tmp_path/'unknown.sqlite3'
    with SQLiteStorage.create_new(path) as storage:
        store=SessionStore(storage=storage);context=store.create(uuid4());budgets=SessionBudgets(store)
        budget_id=budgets.create(context,BudgetLimits(model_calls=8,tool_calls=4))
        segment=budgets.open(context,budget_id).start_segment(BudgetLimits(model_calls=4,tool_calls=2))
        task=store.begin(context);segment.consume('model_calls');segment.consume('tool_calls')
        store.append(context,task.task_id,[Message(role='user',content='记录合成任务'),Message(role='assistant',content='',tool_calls=[
            ToolCall(id='uncertain',name='todo_add',arguments={'title':'响应丢失'}),
        ])])
        with operation_scope(str(budget_id)+':uncertain'):
            saved=TodoService(store).add(context,'响应丢失')
    model=ScriptedModel([ModelResponse(finish_reason='tool_calls',tool_calls=[ToolCall(id='verify',name='todo_list',arguments={})]),
                         ModelResponse(finish_reason='stop',content='真实查询确认记录已存在，不重复新增。')])
    harness=HarnessApplication(path,model=model,owner_id=context.owner_id)
    try:
        before=harness.snapshot(context.session_id)
        assert before['task']['error_code']=='interrupted' and before['checkpoint']
        assert json.loads(before['messages'][-1]['content'])['error']['execution_state']=='unknown'
        harness.resume(context.session_id,checkpoint_id=before['checkpoint']['checkpoint']['checkpoint_id'])
        harness.wait(context.session_id)
        after=harness.snapshot(context.session_id)
        assert after['task']['status']=='completed'
        assert [view['item']['id'] for view in after['todos']]==[str(saved.id)]
    finally:harness.close()


def test_queue_never_enters_active_model_and_only_dispatches_after_completion(tmp_path):
    entered=Event();release=Event();calls=[]
    class Model:
        def generate(self,messages,**kwargs):
            calls.append(messages)
            if len(calls)==1:
                entered.set();assert release.wait(10)
            return ModelResponse(finish_reason='stop',content='脚本化响应')
    harness=HarnessApplication(tmp_path/'queue.sqlite3',model=Model())
    try:
        identity=harness.create_session()['session_id'];harness.submit(identity,'第一项')
        assert entered.wait(10);harness.submit(identity,'第二项')
        assert all('第二项' not in message.content for message in calls[0])
        release.set();harness.wait(identity)
        assert len(calls)==2
        assert any(message.content=='第二项' for message in calls[1])
    finally:release.set();harness.close()
