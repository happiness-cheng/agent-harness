from uuid import uuid4
import pytest

from min_agent.artifacts import ArtifactStore
from min_agent.events import SessionEvents
from min_agent.models import ModelResponse, ToolCall
from min_agent.runtime import Runtime
from min_agent.sessions import SessionStore
from min_agent.tools.registry import ToolDefinition, ToolRegistry
from min_agent.tools.calculator import CalculatorArguments


def test_steer_received_during_model_blocks_old_tool_before_new_decision():
    store = SessionStore(); context = store.create(uuid4()); events = SessionEvents(store, context)
    executed = []; registry = ToolRegistry()
    registry.register(ToolDefinition('calculator','test',CalculatorArguments,lambda expression: executed.append(expression) or '2'))
    class Model:
        count=0
        def generate(self,messages,**kwargs):
            self.count+=1
            if self.count==1:
                events.steer(store.snapshot(context).task.task_id,'先不执行工具',scope_changed=False)
                return ModelResponse(finish_reason='tool_calls',tool_calls=[ToolCall(id='old',name='calculator',arguments={'expression':'1+1'})])
            assert '先不执行工具' in messages[-1].content
            return ModelResponse(finish_reason='stop',content='已经采用新要求')
    result=Runtime(Model(),registry,control=events).run_session('执行',store=store,context=context)
    assert result.status=='completed' and executed==[]
    assert not store._pending(store.snapshot(context).messages)


def test_stop_with_direct_reply_keeps_reply_but_cancels_task():
    store=SessionStore();context=store.create(uuid4());events=SessionEvents(store,context)
    class Model:
        def generate(self,*args,**kwargs):
            events.stop();return ModelResponse(finish_reason='stop',content='停止前已经返回的文字')
    result=Runtime(Model(),control=events).run_session('问题',store=store,context=context)
    assert result.status=='cancelled'
    assert store.snapshot(context).messages[-1].content=='停止前已经返回的文字'


def test_wrong_control_store_never_starts_session_task():
    store=SessionStore();context=store.create(uuid4());other=store.create(context.owner_id)
    events=SessionEvents(store,other)
    with pytest.raises(PermissionError): Runtime(object(),control=events).run_session('问题',store=store,context=context)
    assert store.snapshot(context).task is None


def test_raw_metadata_cannot_bypass_artifact_total_budget():
    store=SessionStore();context=store.create(uuid4());artifacts=ArtifactStore(store,context,max_bytes=1000)
    with pytest.raises(ValueError): artifacts.save('small',metadata={'raw_html':'x'*2000})


def test_invalid_return_is_paired_and_failed_task_can_be_resumed_without_replay():
    from tests.fakes.model import ScriptedModel
    store = SessionStore(); context = store.create(uuid4()); executed = []
    registry = ToolRegistry()
    registry.register(ToolDefinition("calculator", "测试返回损坏", CalculatorArguments,
        lambda expression: executed.append(expression)))
    model = ScriptedModel([ModelResponse(finish_reason="tool_calls", tool_calls=[
        ToolCall(id="invalid", name="calculator", arguments={"expression":"1+1"}),
        ToolCall(id="later", name="calculator", arguments={"expression":"2+2"}),
    ])])
    result = Runtime(model, registry, recover_unknown_writes=True).run_session("执行", store=store, context=context)
    assert result.error_code == "invalid_tool_result" and executed == ["1+1"]
    assert not store._pending(store.snapshot(context).messages)
    resumed = Runtime(ScriptedModel([ModelResponse(finish_reason="stop", content="保留已执行结果，不自动重试")]))
    assert resumed.run_session("仅核对", store=store, context=context).status == "completed"


def test_stop_during_context_preparation_prevents_the_following_model_request():
    from min_agent.context import ManagedModel
    from tests.fakes.model import ScriptedModel
    store=SessionStore();context=store.create(uuid4());events=SessionEvents(store,context)
    class Preparation:
        def prepare(self,messages,tools):
            events.stop()
            return messages
    model=ScriptedModel([ModelResponse(finish_reason="stop",content="不应启动的新请求")])
    result=Runtime(ManagedModel(model,Preparation(),control=events),control=events).run_session(
        "任务",store=store,context=context)
    assert result.status=="cancelled" and result.error_code=="user_stopped"
    assert model.calls==[]
