"""Runtime 阶段组件与错误载荷的独立契约测试。

上层行为已由 ``test_runtime_*`` 覆盖；这里直接钉住组件边界，
让「错误处理放在组件里」这件事有可单独验证的入口。
"""

import json
from types import SimpleNamespace
from uuid import uuid4

from min_agent.ledger.budget import BudgetLimits
from min_agent.ledger.checkpoints import CheckpointPolicy
from min_agent.contracts.models import Message, ModelResponse, ToolCall
from min_agent.runtime.errors import (
    blocked, budget_blocked, budget_checkpoint, model_error_code, needs_recovery,
    trace_error_code, waiting_user,
)
from min_agent.runtime.stages import ControlGate, ReserveGate, ResponseRouter
from min_agent.runtime.state import ModelRound, RunResult, RunState
from min_agent.contracts.trace import TraceContext


def make_state(messages=None):
    return RunState(context=TraceContext(), run_id=uuid4(), parent_call_id=uuid4(),
                    messages=[] if messages is None else messages)


def payload(message):
    return json.loads(message.content)


class FakeControl:
    def __init__(self, *, stopped=False, steers=()):
        self.stopped, self.steers, self.polled = stopped, list(steers), []

    def poll(self, task_id):
        self.polled.append(task_id)
        return self.stopped, list(self.steers)


class FakeCloser:
    def __init__(self):
        self.calls = []

    def close(self, *args):
        self.calls.append(args)
        return RunResult(status="partial", error_code="budget_checkpoint")


def error(response, finish_reason):
    return ModelResponse(
        finish_reason=finish_reason, content=response,
        tool_calls=[ToolCall(id="c1", name="calculator", arguments={"expression": "1+1"})]
        if finish_reason != "stop" else [],
    )


def test_tool_error_payloads_keep_state_and_recovery_fields():
    assert payload(blocked("c", code="steer_pending")) == {
        "ok": False, "error": {"code": "steer_pending", "execution_state": "not_started"}}
    assert payload(needs_recovery("c", code="tool_execution_failed", execution_state="unknown")) == {
        "ok": False, "error": {
            "code": "tool_execution_failed", "execution_state": "unknown",
            "recovery": "verify_actual_state_before_retry"}}
    assert payload(waiting_user("c", code="plan_read_only")) == {
        "ok": False, "error": {
            "code": "plan_read_only", "message": "等待用户确认或模式选择；该请求未执行。",
            "execution_state": "not_started", "recovery": "directed_user_confirmation"}}
    assert payload(budget_checkpoint("c")) == {
        "ok": False, "error": {
            "code": "budget_checkpoint", "message": "已到工具收尾边界；该请求未执行。",
            "execution_state": "not_started", "recovery": "review_checkpoint"}}
    assert payload(budget_blocked("c", code="budget_exhausted", scope="task",
                                  recovery="review_task_budget")) == {
        "ok": False, "error": {
            "code": "budget_exhausted", "message": "预算未放行；该请求未执行。",
            "scope": "task", "resource": "tool_calls",
            "execution_state": "not_started", "recovery": "review_task_budget"}}


def test_trace_error_code_maps_admission_and_unknown_codes():
    assert trace_error_code(None) is None
    assert trace_error_code("task_not_active") == "waiting_user"
    assert trace_error_code("budget_exhausted") == "budget_exhausted"
    assert trace_error_code("http_402") == "model_call_failed"


def test_model_error_code_only_accepts_short_identifier_codes():
    assert model_error_code(SimpleNamespace(code="network_error")) == "network_error"
    assert model_error_code(SimpleNamespace(code="http_402")) == "http_402"
    assert model_error_code(SimpleNamespace(code="x" * 41)) == "model_call_failed"
    assert model_error_code(SimpleNamespace(code="http 402")) == "model_call_failed"
    assert model_error_code(SimpleNamespace(code="401")) == "model_call_failed"
    assert model_error_code(SimpleNamespace(code=401)) == "model_call_failed"
    assert model_error_code(SimpleNamespace()) == "model_call_failed"


def test_control_gate_cancels_or_appends_explicit_steers():
    stopped = FakeControl(stopped=True)
    state = make_state()
    assert ControlGate(stopped)(state) == RunResult(status="cancelled", error_code="user_stopped")
    assert state.messages == []

    steered = FakeControl(steers=["先核对来源"])
    state = make_state()
    assert ControlGate(steered)(state) is None
    assert state.messages == [Message(role="user", content="用户显式引导：先核对来源")]


def test_reserve_gate_only_closes_at_threshold():
    closer = FakeCloser()
    gate = ReserveGate(SimpleNamespace(remaining=BudgetLimits(model_calls=3, tool_calls=0)),
                       CheckpointPolicy(reserve_model_calls=2), closer)
    state = make_state()
    assert gate(state) is None and closer.calls == []

    gate = ReserveGate(SimpleNamespace(remaining=BudgetLimits(model_calls=2, tool_calls=0)),
                       CheckpointPolicy(reserve_model_calls=2), closer)
    result = gate(state)
    assert result == RunResult(status="partial", error_code="budget_checkpoint")
    assert closer.calls == [(state.messages, state.context, state.run_id,
                             state.parent_call_id, "model_reserve")]


def test_router_appends_text_before_stopping_and_refuses_tools_without_registry():
    state = make_state()
    state.round = ModelRound(response=ModelResponse(finish_reason="stop", content="回答"),
                             span_call_id=uuid4())
    assert ResponseRouter(control=None, tools=None)(state) == RunResult(
        status="completed", response=state.round.response)
    assert state.messages == [Message(role="assistant", content="回答")]

    state = make_state()
    state.round = ModelRound(response=error("部分", "length"), span_call_id=uuid4())
    assert ResponseRouter(control=None, tools=None)(state) == RunResult(
        status="partial", response=state.round.response, error_code="output_limit")

    state = make_state()
    state.round = ModelRound(response=error(None, "tool_calls"), span_call_id=uuid4())
    router = ResponseRouter(control=None, tools=None)
    response = state.round.response
    assert router(state) == RunResult(
        status="partial", response=response, error_code="tool_execution_not_supported")
    assert state.request is None and state.messages == []
