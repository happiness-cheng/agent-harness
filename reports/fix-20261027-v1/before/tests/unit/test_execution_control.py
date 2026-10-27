from uuid import uuid4

from min_agent.events import SessionEvents
from min_agent.progress_guard import ProgressGuard
from min_agent.models import ModelResponse, ToolCall
from min_agent.runtime import Runtime
from min_agent.sessions import SessionStore
from min_agent.tools.calculator import CALCULATOR_TOOL
from min_agent.tools.registry import ToolRegistry
from tests.fakes.model import ScriptedModel


def test_same_result_three_times_allows_one_correction_then_pauses():
    guard = ProgressGuard()
    call = ToolCall(id="one", name="search", arguments={"query": "fact"})
    assert guard.observe(call, {"ok": True, "result": "same", "retrieved_at": "1"}) == "continue"
    assert guard.observe(call, {"ok": True, "result": "same", "retrieved_at": "2"}) == "continue"
    assert guard.observe(call, {"ok": True, "result": "same", "retrieved_at": "3"}) == "correct"
    assert guard.observe(call, {"ok": True, "result": "same", "retrieved_at": "4"}) == "pause"
    assert guard.observe(call, {"ok": True, "result": "new evidence"}) == "continue"


def test_queued_text_is_not_consumed_by_active_task_or_waiting_user():
    store = SessionStore(); context = store.create(uuid4())
    events = SessionEvents(store, context)
    events.enqueue("下一件事")
    events.started(uuid4())
    assert events.next_input() is None
    events.finished("waiting_user")
    assert events.next_input() is None
    events.finished("completed")
    assert events.next_input() == "下一件事"


def test_stop_during_model_prevents_tool_batch_and_retains_queue():
    store = SessionStore(); context = store.create(uuid4()); events = SessionEvents(store, context)
    events.enqueue("保留")
    tools = ToolRegistry(); tools.register(CALCULATOR_TOOL)
    class StopModel:
        def generate(self, messages, **kwargs):
            events.stop()
            return ModelResponse(finish_reason="tool_calls", tool_calls=[
                ToolCall(id="no-start", name="calculator", arguments={"expression": "1+1"}),
            ])
    result = Runtime(StopModel(), tools, control=events).run_session("执行", store=store, context=context)
    assert result.status == "cancelled"
    assert events.next_input() is None
    assert not SessionStore._pending(store.snapshot(context).messages)


def test_steer_is_explicit_and_old_task_target_is_rejected():
    store = SessionStore(); context = store.create(uuid4()); events = SessionEvents(store, context)
    task_id = uuid4(); events.started(task_id)
    events.steer(task_id, "先核对来源", scope_changed=False)
    assert events.poll(task_id)[1] == ["先核对来源"]
    assert events.poll(uuid4())[1] == []
    events.stop(); events.finished("cancelled")
    events.resume_dispatch()
    assert events.next_input() is None
