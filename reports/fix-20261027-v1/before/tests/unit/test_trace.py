import json
from dataclasses import replace
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from min_agent.trace import TraceContext, TraceEvent, TraceRecorder
from min_agent.models import ModelResponse, ToolCall
from min_agent.runtime import Runtime
from min_agent.tools.calculator import CALCULATOR_TOOL
from min_agent.tools.registry import ToolRegistry
from tests.fakes.model import ScriptedModel


class TickClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        self.value += 0.125
        return self.value


def test_nested_spans_preserve_context_parent_ids_duration_and_json():
    context = TraceContext(session_id=UUID(int=1), task_id=UUID(int=2))
    recorder = TraceRecorder(clock=TickClock())
    run_id = uuid4()

    with recorder.span("run", run_id=run_id, context=context) as root:
        with recorder.span("model", run_id=run_id, context=context, parent_call_id=root.call_id) as model:
            model.finish("completed")
        root.finish("completed")

    child, parent = recorder.events
    assert child.operation == "model"
    assert parent.operation == "run"
    assert child.parent_call_id == parent.call_id == root.call_id
    assert child.call_id == model.call_id
    assert parent.parent_call_id is None
    assert child.run_id == parent.run_id == run_id
    assert child.session_id == parent.session_id == context.session_id
    assert child.task_id == parent.task_id == context.task_id
    assert child.duration_ms == 125.0
    assert parent.duration_ms == 375.0
    assert [event.sequence for event in recorder.events] == [1, 2]
    assert child.status == parent.status == "completed"
    assert json.loads(recorder.to_json())[0]["session_id"] == str(context.session_id)


def test_event_snapshots_are_frozen_and_recorders_do_not_share_storage():
    recorder = TraceRecorder(clock=TickClock())
    with recorder.span("run", run_id=uuid4(), context=TraceContext()) as span:
        span.finish("completed")
    snapshot = recorder.events

    assert isinstance(snapshot, tuple)
    with pytest.raises(ValidationError):
        snapshot[0].status = "failed"
    with recorder.span("run", run_id=uuid4(), context=TraceContext()) as span:
        span.finish("completed")

    assert len(snapshot) == 1
    assert len(recorder.events) == 2
    assert TraceRecorder().events == ()


@pytest.mark.parametrize("changes", [
    {"session_id": "private-input"}, {"task_id": 123}, {"api_key": "test-value"},
])
def test_trace_context_rejects_untyped_or_extra_metadata(changes):
    with pytest.raises(ValidationError):
        TraceContext(**changes)


def test_runtime_reply_records_child_and_terminal_state_with_exact_timing():
    recorder = TraceRecorder(clock=TickClock())
    context = TraceContext(session_id=UUID(int=11), task_id=UUID(int=12))
    model = ScriptedModel([ModelResponse(finish_reason="stop", content="回答")])

    result = Runtime(model, trace=recorder).run("输入", trace_context=context)

    child, run = recorder.events
    assert result.run_id == child.run_id == run.run_id
    assert child.parent_call_id == run.call_id
    assert child.operation == "model"
    assert run.operation == "run"
    assert child.status == run.status == "completed"
    assert child.duration_ms == 125.0
    assert run.duration_ms == 375.0
    assert run.session_id == context.session_id
    assert run.task_id == context.task_id
    assert run.error_code is None


def test_calculator_trace_links_tool_to_requesting_model_and_models_to_run():
    recorder = TraceRecorder(clock=TickClock())
    registry = ToolRegistry()
    registry.register(CALCULATOR_TOOL)
    model = ScriptedModel([
        ModelResponse(finish_reason="tool_calls", tool_calls=[
            ToolCall(id="calc-1", name="calculator", arguments={"expression": "0.1+0.2"}),
        ]),
        ModelResponse(finish_reason="stop", content="0.3"),
    ])

    result = Runtime(model, tools=registry, trace=recorder).run("计算")

    first, tool, second, run = recorder.events
    assert [event.operation for event in recorder.events] == ["model", "tool", "model", "run"]
    assert first.parent_call_id == second.parent_call_id == run.call_id
    assert tool.parent_call_id == first.call_id
    assert {event.run_id for event in recorder.events} == {result.run_id}
    assert len({event.call_id for event in recorder.events}) == 4
    assert tool.status == "completed"
    assert tool.execution_state == "returned"
    assert tool.duration_ms == 125.0
    assert run.duration_ms == 875.0
    assert json.loads(model.calls[1][-1].content) == {"ok": True, "result": "0.3"}


@pytest.mark.parametrize(("reason", "status", "code"), [
    ("length", "partial", "output_limit"), ("refusal", "failed", "model_refusal"),
    ("error", "failed", "model_error"),
])
def test_non_success_model_response_records_matching_model_and_run_state(reason, status, code):
    runtime = Runtime(ScriptedModel([ModelResponse(finish_reason=reason, content="部分输出")]))

    result = runtime.run("输入")

    assert result.status == status
    assert [(e.status, e.error_code) for e in runtime.trace.events] == [(status, code), (status, code)]


def test_model_exception_and_invalid_return_have_safe_locatable_trace():
    private = "仅测试的异常私密正文"
    for response, code in [(RuntimeError(private), "model_call_failed"), (None, "invalid_model_response")]:
        class BadModel:
            def generate(self, messages):
                if isinstance(response, Exception):
                    raise response
                return response

        runtime = Runtime(BadModel())
        result = runtime.run("输入")

        assert result.status == "failed"
        assert [event.error_code for event in runtime.trace.events] == [code, code]
        assert runtime.trace.events[0].parent_call_id == runtime.trace.events[1].call_id
        assert private not in runtime.trace.to_json()


def test_tool_request_errors_record_not_started_and_do_not_overwrite_successful_run():
    registry = ToolRegistry()
    registry.register(CALCULATOR_TOOL)
    requests = [
        ToolCall(id="unknown", name="missing", arguments={}),
        ToolCall(id="invalid", name="calculator", arguments={"expression": 123}),
        ToolCall(id="fixed", name="calculator", arguments={"expression": "1+2"}),
    ]
    model = ScriptedModel([
        *[ModelResponse(finish_reason="tool_calls", tool_calls=[call]) for call in requests],
        ModelResponse(finish_reason="stop", content="3"),
    ])
    runtime = Runtime(model, tools=registry)

    result = runtime.run("输入")

    events = [event for event in runtime.trace.events if event.operation == "tool"]
    assert [(e.status, e.error_code, e.execution_state) for e in events] == [
        ("failed", "unknown_tool", "not_started"),
        ("failed", "invalid_arguments", "not_started"),
        ("completed", None, "returned"),
    ]
    assert result.status == runtime.trace.events[-1].status == "completed"


@pytest.mark.parametrize(("failure", "code", "execution_state"), [
    ("raise", "tool_execution_failed", "unknown"),
    ("bad_result", "invalid_tool_result", "returned"),
])
def test_tool_execution_failure_preserves_stage_without_exception_or_output_text(failure, code, execution_state):
    private = "仅测试的工具私密正文"

    def handler(expression):
        if failure == "raise":
            raise RuntimeError(private)
        return {"secret": private}

    registry = ToolRegistry()
    registry.register(replace(CALCULATOR_TOOL, handler=handler))
    model = ScriptedModel([ModelResponse(finish_reason="tool_calls", tool_calls=[
        ToolCall(id="c1", name="calculator", arguments={"expression": "1+2"}),
    ])])
    runtime = Runtime(model, tools=registry)

    result = runtime.run("输入")

    model_event, tool, run = runtime.trace.events
    assert result.error_code == tool.error_code == run.error_code == code
    assert tool.status == run.status == "failed"
    assert tool.execution_state == execution_state
    assert tool.parent_call_id == model_event.call_id
    assert private not in runtime.trace.to_json()


def test_payloads_raw_tool_identifiers_and_names_never_enter_trace():
    private = "仅测试的敏感标记"
    raw_id, raw_name = private + "-id", private + "-name"
    registry = ToolRegistry()
    registry.register(replace(CALCULATOR_TOOL, name=raw_name, handler=lambda **kwargs: private))
    model = ScriptedModel([
        ModelResponse(finish_reason="tool_calls", reasoning_summary=private, tool_calls=[
            ToolCall(id=raw_id, name=raw_name, arguments={"expression": private}),
        ]),
        ModelResponse(finish_reason="stop", content=private, reasoning_summary=private),
    ])
    runtime = Runtime(model, tools=registry)

    runtime.run(private)

    text = runtime.trace.to_json()
    assert private not in text
    tool = next(event for event in runtime.trace.events if event.operation == "tool")
    assert tool.source_call_ref == sha256(raw_id.encode("utf-8")).hexdigest()
    assert tool.tool_ref == sha256(raw_name.encode("utf-8")).hexdigest()
    for event in runtime.trace.events:
        assert not {"content", "arguments", "result", "message", "api_key", "reasoning_summary"} & event.model_dump().keys()


@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, SystemExit])
def test_model_interrupt_is_recorded_and_still_propagates(exception_type):
    runtime = Runtime(ScriptedModel([exception_type("仅测试的中断正文")]))

    with pytest.raises(exception_type):
        runtime.run("输入")

    assert [(e.status, e.error_code) for e in runtime.trace.events] == [
        ("cancelled", "interrupted"), ("cancelled", "interrupted"),
    ]
    assert "仅测试的中断正文" not in runtime.trace.to_json()


def test_tool_interrupt_records_unknown_execution_state_and_propagates():
    def handler(expression):
        raise KeyboardInterrupt("仅测试的中断正文")

    registry = ToolRegistry()
    registry.register(replace(CALCULATOR_TOOL, handler=handler))
    model = ScriptedModel([ModelResponse(finish_reason="tool_calls", tool_calls=[
        ToolCall(id="c1", name="calculator", arguments={"expression": "1+2"}),
    ])])
    runtime = Runtime(model, tools=registry)

    with pytest.raises(KeyboardInterrupt):
        runtime.run("输入")

    assert runtime.trace.events[1].status == runtime.trace.events[2].status == "cancelled"
    assert runtime.trace.events[1].execution_state == "unknown"


def test_unexpected_span_exception_uses_fixed_code_and_propagates():
    recorder = TraceRecorder(clock=TickClock())

    with pytest.raises(RuntimeError):
        with recorder.span("run", run_id=uuid4(), context=TraceContext()):
            raise RuntimeError("仅测试的内部异常正文")

    assert recorder.events[0].status == "failed"
    assert recorder.events[0].error_code == "unexpected_error"
    assert "仅测试的内部异常正文" not in recorder.to_json()


@pytest.mark.parametrize("context", ["private-context", TraceContext.model_construct(session_id="private-id")])
def test_invalid_runtime_context_is_rejected_before_creating_events_or_model_calls(context):
    model = ScriptedModel([])
    runtime = Runtime(model)

    with pytest.raises((TypeError, ValidationError)):
        runtime.run("输入", trace_context=context)

    assert model.calls == []
    assert runtime.trace.events == ()


@pytest.mark.parametrize("changes", [
    {"duration_ms": -1.0}, {"duration_ms": float("inf")}, {"duration_ms": float("nan")},
    {"error_code": "untrusted exception text"}, {"payload": {"api_key": "test-only"}},
])
def test_trace_event_rejects_invalid_durations_and_arbitrary_data(changes):
    payload = dict(sequence=1, run_id=uuid4(), call_id=uuid4(), operation="run", status="completed", duration_ms=1.0)
    payload.update(changes)

    with pytest.raises(ValidationError):
        TraceEvent(**payload)


def test_shared_recorder_keeps_run_and_context_tags_separate():
    recorder = TraceRecorder(clock=TickClock())
    model = ScriptedModel([ModelResponse(finish_reason="stop", content="回答")] * 2)
    runtime = Runtime(model, trace=recorder)
    first_context = TraceContext(session_id=UUID(int=1), task_id=UUID(int=2))
    second_context = TraceContext(session_id=UUID(int=3), task_id=UUID(int=4))

    first = runtime.run("第一项", trace_context=first_context)
    second = runtime.run("第二项", trace_context=second_context)

    assert first.run_id != second.run_id
    assert {event.session_id for event in recorder.events[:2]} == {first_context.session_id}
    assert {event.task_id for event in recorder.events[2:]} == {second_context.task_id}
    assert {event.run_id for event in recorder.events[:2]} == {first.run_id}
    assert {event.run_id for event in recorder.events[2:]} == {second.run_id}
    assert [event.sequence for event in recorder.events] == [1, 2, 3, 4]
    assert len(model.calls[1]) == 1


@pytest.mark.parametrize("text", [" ", 123])
def test_invalid_input_creates_no_trace_or_model_call(text):
    model = ScriptedModel([])
    runtime = Runtime(model)

    with pytest.raises((TypeError, ValueError)):
        runtime.run(text)

    assert runtime.trace.events == ()
    assert model.calls == []


@pytest.mark.parametrize("stop_kind", ["repair_limit", "unrepaired_stop", "duplicate_id"])
def test_runtime_terminal_error_is_distinct_from_successful_model_decision(stop_kind):
    registry = ToolRegistry()
    registry.register(CALCULATOR_TOOL)
    good = ModelResponse(finish_reason="tool_calls", tool_calls=[
        ToolCall(id="same", name="calculator", arguments={"expression": "1+2"}),
    ])
    bad = ModelResponse(finish_reason="tool_calls", tool_calls=[
        ToolCall(id="bad", name="missing", arguments={}),
    ])
    scripts = {
        "repair_limit": [bad], "unrepaired_stop": [bad, ModelResponse(finish_reason="stop", content="完成")],
        "duplicate_id": [good, good],
    }
    runtime = Runtime(ScriptedModel(scripts[stop_kind]), tools=registry,
                      max_tool_error_rounds=0 if stop_kind == "repair_limit" else 2)

    result = runtime.run("输入")

    expected = {"repair_limit": "tool_repair_limit", "unrepaired_stop": "tool_request_failed",
                "duplicate_id": "duplicate_tool_call_id"}[stop_kind]
    assert result.error_code == runtime.trace.events[-1].error_code == expected
    assert runtime.trace.events[-1].status == result.status
    assert all(event.status == "completed" for event in runtime.trace.events if event.operation == "model")
    assert len([event for event in runtime.trace.events if event.operation == "tool"]) == 1


@pytest.mark.parametrize("reason", ["length", "refusal", "error"])
def test_non_tool_terminal_response_creates_no_tool_span_for_embedded_requests(reason):
    registry = ToolRegistry()
    registry.register(CALCULATOR_TOOL)
    model = ScriptedModel([ModelResponse(finish_reason=reason, tool_calls=[
        ToolCall(id="not-executed", name="calculator", arguments={"expression": "1+2"}),
    ])])
    runtime = Runtime(model, tools=registry)

    runtime.run("输入")

    assert [event.operation for event in runtime.trace.events] == ["model", "run"]
