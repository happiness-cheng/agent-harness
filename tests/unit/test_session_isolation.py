from uuid import uuid4
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

import json

from min_agent.models import Message, ModelResponse, ToolCall
from min_agent.runtime import Runtime
from min_agent.sessions import ExecutionContext, SessionStore
from min_agent.tools.calculator import CALCULATOR_TOOL
from min_agent.tools.registry import ToolRegistry
from tests.fakes.model import ScriptedModel


def test_sessions_keep_messages_and_current_task_state_independent():
    store = SessionStore()
    owner = uuid4()
    a, b = store.create(owner), store.create(owner)

    task = store.begin(a)
    store.append(a, task.task_id, [Message(role="user", content="仅属于A")])
    run_id = uuid4()
    store.finish(a, task.task_id, status="completed", run_id=run_id)

    assert a.owner_id == b.owner_id == owner
    assert a.session_id != b.session_id
    assert store.snapshot(a).messages == [Message(role="user", content="仅属于A")]
    assert store.snapshot(a).task.status == "completed"
    assert store.snapshot(a).task.run_id == run_id
    assert store.snapshot(b).messages == []
    assert store.snapshot(b).task is None


def test_unknown_or_foreign_session_cannot_be_read_or_claimed():
    store = SessionStore()
    a = store.create(uuid4())
    foreign = ExecutionContext(owner_id=uuid4(), session_id=a.session_id)
    missing = ExecutionContext(owner_id=a.owner_id, session_id=uuid4())

    for context in (foreign, missing):
        with pytest.raises(PermissionError):
            store.snapshot(context)
        with pytest.raises(PermissionError):
            store.begin(context)

    assert store.snapshot(a).task is None


def test_other_session_task_cannot_append_or_finish_current_task():
    store = SessionStore()
    owner = uuid4()
    a, b = store.create(owner), store.create(owner)
    task_a, task_b = store.begin(a), store.begin(b)

    with pytest.raises(PermissionError):
        store.append(a, task_b.task_id, [Message(role="user", content="错误归属")])
    with pytest.raises(PermissionError):
        store.finish(a, task_b.task_id, status="completed")

    assert store.snapshot(a).messages == store.snapshot(b).messages == []
    assert store.snapshot(a).task.task_id == task_a.task_id
    assert store.snapshot(b).task.status == "running"


def test_runtime_interleaves_sessions_with_isolated_plain_and_tool_followups():
    store = SessionStore()
    owner = uuid4()
    a, b = store.create(owner), store.create(owner)
    registry = ToolRegistry()
    registry.register(CALCULATOR_TOOL)
    model = ScriptedModel([
        ModelResponse(finish_reason="stop", content="A第一次回答"),
        ModelResponse(finish_reason="stop", content="B第一次回答"),
        ModelResponse(finish_reason="tool_calls", tool_calls=[
            ToolCall(id="calc-a", name="calculator", arguments={"expression": "0.1+0.2"}),
        ]),
        ModelResponse(finish_reason="stop", content="A计算0.3"),
        ModelResponse(finish_reason="stop", content="B继续回答"),
    ])
    runtime = Runtime(model, tools=registry)

    first_a = runtime.run_session("A第一次", store=store, context=a)
    runtime.run_session("B第一次", store=store, context=b)
    second_a = runtime.run_session("A计算", store=store, context=a)
    second_b = runtime.run_session("B继续", store=store, context=b)

    assert first_a.status == second_a.status == second_b.status == "completed"
    assert model.calls[2] == [
        Message(role="user", content="A第一次"), Message(role="assistant", content="A第一次回答"),
        Message(role="user", content="A计算"),
    ]
    assert json.loads(model.calls[3][-1].content) == {"ok": True, "result": "0.3"}
    assert model.calls[4] == [
        Message(role="user", content="B第一次"), Message(role="assistant", content="B第一次回答"),
        Message(role="user", content="B继续"),
    ]
    snapshot_a, snapshot_b = store.snapshot(a), store.snapshot(b)
    assert [m.role for m in snapshot_a.messages] == ["user", "assistant", "user", "assistant", "tool", "assistant"]
    assert [m.content for m in snapshot_b.messages] == ["B第一次", "B第一次回答", "B继续", "B继续回答"]
    assert snapshot_a.task.run_id == second_a.run_id
    assert snapshot_b.task.run_id == second_b.run_id
    assert snapshot_a.task.task_id != snapshot_b.task.task_id
    events = [event for event in runtime.trace.events if event.run_id == second_a.run_id]
    assert {event.session_id for event in events} == {a.session_id}
    assert {event.task_id for event in events} == {snapshot_a.task.task_id}


def test_runtime_rejects_foreign_owner_before_model_trace_or_task_change():
    store = SessionStore()
    valid = store.create(uuid4())
    forged = ExecutionContext(owner_id=uuid4(), session_id=valid.session_id)
    model = ScriptedModel([])
    runtime = Runtime(model)

    with pytest.raises(PermissionError):
        runtime.run_session("越权输入", store=store, context=forged)

    assert model.calls == []
    assert runtime.trace.events == ()
    assert store.snapshot(valid).task is None


def test_message_input_and_nested_snapshot_mutation_do_not_change_storage():
    store = SessionStore()
    context = store.create(uuid4())
    task = store.begin(context)
    request = Message(role="assistant", content="", tool_calls=[
        ToolCall(id="c1", name="example", arguments={"items": [1]}),
    ])
    store.append(context, task.task_id, [request, Message(role="tool", content="结果", tool_call_id="c1")])
    request.tool_calls[0].arguments["items"].append(2)
    snapshot = store.snapshot(context)
    snapshot.messages[0].tool_calls[0].arguments["items"].append(3)
    snapshot.messages.clear()
    snapshot.owner_id = uuid4()

    saved = store.snapshot(context)
    assert saved.messages[0].tool_calls[0].arguments == {"items": [1]}
    assert len(saved.messages) == 2
    assert saved.owner_id == context.owner_id


def test_same_session_has_only_one_task_writer_under_competing_claims():
    store = SessionStore()
    context = store.create(uuid4())
    barrier = Barrier(2)

    def claim():
        barrier.wait(timeout=5)
        try:
            return store.begin(context)
        except RuntimeError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(claim) for _ in range(2)]
        results = [future.result(timeout=5) for future in futures]

    winners = [task for task in results if task is not None]
    assert len(winners) == 1
    assert store.snapshot(context).task.task_id == winners[0].task_id


def test_running_session_does_not_block_other_sessions_and_busy_runtime_does_not_call_model():
    store = SessionStore()
    owner = uuid4()
    a, b = store.create(owner), store.create(owner)
    active = store.begin(a)
    model = ScriptedModel([ModelResponse(finish_reason="stop", content="B正常回答")])
    runtime = Runtime(model)

    with pytest.raises(RuntimeError):
        runtime.run_session("A重复启动", store=store, context=a)
    result = runtime.run_session("B可以执行", store=store, context=b)

    assert result.status == "completed"
    assert len(model.calls) == 1
    assert store.snapshot(a).task.task_id == active.task_id
    assert store.snapshot(a).messages == []


def test_finished_and_stale_task_ids_cannot_write_to_new_task():
    store = SessionStore()
    context = store.create(uuid4())
    old = store.begin(context)
    store.finish(context, old.task_id, status="completed")
    with pytest.raises(RuntimeError):
        store.append(context, old.task_id, [Message(role="user", content="任务已结束")])
    current = store.begin(context)
    with pytest.raises(PermissionError):
        store.append(context, old.task_id, [Message(role="user", content="旧任务")])
    with pytest.raises(PermissionError):
        store.finish(context, old.task_id, status="failed")

    assert store.snapshot(context).task.task_id == current.task_id
    assert store.snapshot(context).messages == []


@pytest.mark.parametrize("problem", ["unmatched", "wrong_id", "duplicate_result", "user_before_result"])
def test_invalid_message_pairing_is_rejected_atomically(problem):
    store = SessionStore()
    context = store.create(uuid4())
    task = store.begin(context)
    request = Message(role="assistant", content="", tool_calls=[
        ToolCall(id="c1", name="calculator", arguments={"expression": "1+2"}),
    ])
    result = Message(role="tool", content="3", tool_call_id="c1")
    batches = {
        "unmatched": [Message(role="user", content="不应半写"), result],
        "wrong_id": [request, Message(role="tool", content="3", tool_call_id="other")],
        "duplicate_result": [request, result, result],
        "user_before_result": [request, Message(role="user", content="提前进入下一轮")],
    }

    with pytest.raises(ValueError):
        store.append(context, task.task_id, batches[problem])

    assert store.snapshot(context).messages == []


def test_invalid_message_field_does_not_partially_append_valid_prefix():
    store = SessionStore()
    context = store.create(uuid4())
    task = store.begin(context)
    invalid = Message(role="user", content="原来有效")
    invalid.content = 123

    with pytest.raises(ValueError):
        store.append(context, task.task_id, [Message(role="user", content="前缀"), invalid])

    assert store.snapshot(context).messages == []


@pytest.mark.parametrize("status", ["running", "unknown"])
def test_finish_rejects_nonterminal_or_invalid_status_without_changing_state(status):
    store = SessionStore()
    context = store.create(uuid4())
    task = store.begin(context)

    with pytest.raises(ValueError):
        store.finish(context, task.task_id, status=status)

    assert store.snapshot(context).task.status == "running"


def test_pending_tool_requests_prevent_completed_state_and_next_task():
    store = SessionStore()
    context = store.create(uuid4())
    task = store.begin(context)
    store.append(context, task.task_id, [Message(role="assistant", content="", tool_calls=[
        ToolCall(id="pending", name="calculator", arguments={"expression": "1+2"}),
    ])])

    with pytest.raises(ValueError):
        store.finish(context, task.task_id, status="completed")
    store.finish(context, task.task_id, status="failed", error_code="tool_execution_failed")
    with pytest.raises(RuntimeError):
        store.begin(context)

    assert store.snapshot(context).task.status == "failed"
    assert len(store.snapshot(context).messages) == 1


def test_results_can_be_appended_in_later_batch_before_completion():
    store = SessionStore()
    context = store.create(uuid4())
    task = store.begin(context)
    store.append(context, task.task_id, [Message(role="assistant", content="", tool_calls=[
        ToolCall(id="c1", name="calculator", arguments={"expression": "1+2"}),
    ])])
    store.append(context, task.task_id, [Message(role="tool", content="3", tool_call_id="c1")])
    store.finish(context, task.task_id, status="completed")

    assert store.snapshot(context).task.status == "completed"


def test_runtime_failed_tool_batch_is_preserved_and_not_replayed_by_new_input():
    store = SessionStore()
    context = store.create(uuid4())
    registry = ToolRegistry()
    registry.register(CALCULATOR_TOOL)
    model = ScriptedModel([ModelResponse(finish_reason="tool_calls", tool_calls=[
        ToolCall(id="ok", name="calculator", arguments={"expression": "1+2"}),
        ToolCall(id="fail", name="calculator", arguments={"expression": "1/0"}),
    ])])
    runtime = Runtime(model, tools=registry)

    result = runtime.run_session("两个计算", store=store, context=context)
    snapshot = store.snapshot(context)

    assert result.status == snapshot.task.status == "failed"
    assert snapshot.task.error_code == "tool_execution_failed"
    assert snapshot.task.run_id == result.run_id
    assert [m.role for m in snapshot.messages] == ["user", "assistant", "tool"]
    assert snapshot.messages[-1].tool_call_id == "ok"
    with pytest.raises(RuntimeError):
        runtime.run_session("不要重放", store=store, context=context)
    assert len(model.calls) == 1
    assert store.snapshot(context).task.task_id == snapshot.task.task_id


def test_model_failure_keeps_user_input_and_next_task_uses_saved_history():
    store = SessionStore()
    context = store.create(uuid4())
    model = ScriptedModel([TimeoutError("仅测试的异常正文"), ModelResponse(finish_reason="stop", content="恢复回答")])
    runtime = Runtime(model)

    first = runtime.run_session("第一次", store=store, context=context)
    failed = store.snapshot(context)
    second = runtime.run_session("新一轮", store=store, context=context)

    assert first.status == failed.task.status == "failed"
    assert failed.messages == [Message(role="user", content="第一次")]
    assert second.status == "completed"
    assert model.calls[1] == [Message(role="user", content="第一次"), Message(role="user", content="新一轮")]
    assert "仅测试的异常正文" not in repr(store.snapshot(context))


@pytest.mark.parametrize("where", ["model", "tool"])
def test_interrupt_saves_cancelled_task_and_actual_history_before_propagating(where):
    def interrupt(expression):
        raise KeyboardInterrupt()

    store = SessionStore()
    context = store.create(uuid4())
    registry = ToolRegistry()
    registry.register(replace(CALCULATOR_TOOL, handler=interrupt))
    response = KeyboardInterrupt() if where == "model" else ModelResponse(
        finish_reason="tool_calls", tool_calls=[ToolCall(id="c1", name="calculator", arguments={"expression": "1+2"})],
    )
    runtime = Runtime(ScriptedModel([response]), tools=registry)

    with pytest.raises(KeyboardInterrupt):
        runtime.run_session("输入", store=store, context=context)

    snapshot = store.snapshot(context)
    assert snapshot.task.status == "cancelled"
    assert snapshot.task.error_code == "interrupted"
    assert snapshot.task.run_id == runtime.trace.events[-1].run_id
    assert [m.role for m in snapshot.messages] == (["user"] if where == "model" else ["user", "assistant"])


@pytest.mark.parametrize("reason", ["length", "refusal", "error"])
def test_non_tool_terminal_history_preserves_text_without_unexecuted_tool_requests(reason):
    store = SessionStore()
    context = store.create(uuid4())
    response = ModelResponse(finish_reason=reason, content="部分正文", tool_calls=[
        ToolCall(id="not-executed", name="calculator", arguments={"expression": "1+2"}),
    ])
    runtime = Runtime(ScriptedModel([response]))

    result = runtime.run_session("输入", store=store, context=context)

    assert result.status != "completed"
    assert store.snapshot(context).messages == [Message(role="user", content="输入"), Message(role="assistant", content="部分正文")]
    assert result.response.tool_calls == response.tool_calls


@pytest.mark.parametrize("text", [" ", 123])
def test_invalid_session_input_does_not_claim_task_or_change_history(text):
    store = SessionStore()
    context = store.create(uuid4())
    model = ScriptedModel([])
    runtime = Runtime(model)

    with pytest.raises((TypeError, ValueError)):
        runtime.run_session(text, store=store, context=context)

    assert store.snapshot(context).task is None
    assert store.snapshot(context).messages == []
    assert model.calls == []


@pytest.mark.parametrize("operation", ["append", "finish"])
def test_foreign_owner_cannot_mutate_even_with_known_current_task_id(operation):
    store = SessionStore()
    valid = store.create(uuid4())
    active = store.begin(valid)
    forged = ExecutionContext(owner_id=uuid4(), session_id=valid.session_id)

    with pytest.raises(PermissionError):
        if operation == "append":
            store.append(forged, active.task_id, [Message(role="user", content="错误归属")])
        else:
            store.finish(forged, active.task_id, status="completed")

    assert store.snapshot(valid).messages == []
    assert store.snapshot(valid).task.status == "running"


@pytest.mark.parametrize("problem", ["wrong_type", "bypass_validation"])
def test_invalid_execution_context_is_revalidated_before_session_claim(problem):
    store = SessionStore()
    valid = store.create(uuid4())
    invalid = "context" if problem == "wrong_type" else ExecutionContext.model_construct(
        owner_id="not-a-uuid", session_id=valid.session_id,
    )
    model = ScriptedModel([])
    runtime = Runtime(model)

    with pytest.raises((TypeError, ValueError)):
        runtime.run_session("输入", store=store, context=invalid)

    assert model.calls == []
    assert runtime.trace.events == ()
    assert store.snapshot(valid).task is None


def test_context_from_one_store_does_not_find_state_in_another_store():
    first, second = SessionStore(), SessionStore()
    context = first.create(uuid4())

    with pytest.raises(PermissionError):
        second.snapshot(context)

    assert first.snapshot(context).messages == []
