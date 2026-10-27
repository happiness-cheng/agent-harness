"""从公共预算接口及真实Runtime验证硬上限，不请求模型服务。"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from uuid import uuid4

import pytest
import httpx
from pydantic import ValidationError

from min_agent.budget import BudgetExceeded, BudgetLedger, BudgetLimits
from min_agent.llm.deepseek import DeepSeekResponsesModel, DeepSeekSettings
from min_agent.models import ModelResponse, ToolCall
from min_agent.runtime import Runtime
from min_agent.sessions import SessionStore
from min_agent.task_state import TaskController
from min_agent.tools.calculator import CALCULATOR_TOOL
from min_agent.tools.registry import ToolRegistry
from min_agent.tools.todo import TodoService
from min_agent.tools.todo_tools import todo_registry
from tests.fakes.model import ScriptedModel


def limits(models, tools=0):
    return BudgetLimits(model_calls=models, tool_calls=tools)


@pytest.mark.parametrize("field", ["model_calls", "tool_calls"])
@pytest.mark.parametrize("value", [True, -1, 1.0, "1", None])
def test_budget_limits_reject_non_integer_or_negative_values(field, value):
    with pytest.raises(ValidationError):
        BudgetLimits(**{"model_calls": 1, "tool_calls": 1, field: value})


def test_zero_budget_refuses_before_any_counter_changes():
    ledger = BudgetLedger(limits(0, 0))
    segment = ledger.start_segment(limits(3, 3))
    for resource in ("model_calls", "tool_calls"):
        with pytest.raises(BudgetExceeded) as error:
            segment.consume(resource)
        assert error.value.scope == "task" and error.value.resource == resource
    assert ledger.usage.model_calls == ledger.usage.tool_calls == 0
    assert segment.usage.by_purpose == {}


def test_new_segment_gets_new_local_quota_without_resetting_task_usage():
    ledger = BudgetLedger(limits(2))
    first = ledger.start_segment(limits(1))
    first.consume("model_calls")
    with pytest.raises(BudgetExceeded) as error:
        first.consume("model_calls")
    assert error.value.scope == "segment"
    second = ledger.start_segment(limits(1))
    second.consume("model_calls")
    third = ledger.start_segment(limits(10))
    with pytest.raises(BudgetExceeded) as error:
        third.consume("model_calls")
    assert error.value.scope == "task"
    assert ledger.usage.model_calls == 2
    assert first.usage.model_calls == second.usage.model_calls == 1
    assert third.usage.model_calls == 0


def test_model_and_tool_limits_are_independent_and_rejections_do_not_partly_charge():
    ledger = BudgetLedger(limits(1, 2))
    segment = ledger.start_segment(limits(4, 1))
    segment.consume("model_calls")
    segment.consume("tool_calls")
    with pytest.raises(BudgetExceeded):
        segment.consume("model_calls")
    with pytest.raises(BudgetExceeded):
        segment.consume("tool_calls")
    assert (ledger.usage.model_calls, ledger.usage.tool_calls) == (1, 1)
    assert segment.usage == ledger.usage


def test_child_and_nested_calls_charge_each_parent_and_task_once():
    ledger = BudgetLedger(limits(10, 10))
    parent = ledger.start_segment(limits(2, 3))
    child = parent.child(limits(5, 5), purpose="research")
    nested = child.child(limits(5, 5), purpose="compaction")
    child.consume("model_calls")
    nested.consume("model_calls")
    nested.consume("tool_calls")
    with pytest.raises(BudgetExceeded) as error:
        nested.consume("model_calls")
    assert error.value.scope == "segment"
    assert ledger.usage.model_calls == parent.usage.model_calls == child.usage.model_calls == 2
    assert nested.usage.model_calls == nested.usage.tool_calls == 1
    assert ledger.usage.by_purpose["research"].model_calls == 1
    assert ledger.usage.by_purpose["compaction"].model_calls == 1
    assert ledger.usage.by_purpose["compaction"].tool_calls == 1


def test_retry_and_repair_attempts_use_same_task_quota():
    ledger = BudgetLedger(limits(3))
    segment = ledger.start_segment(limits(3))
    for purpose in ("main", "retry", "repair"):
        segment.consume("model_calls", purpose=purpose)
    with pytest.raises(BudgetExceeded):
        segment.consume("model_calls", purpose="retry")
    assert ledger.usage.model_calls == 3
    assert {name: counts.model_calls for name, counts in ledger.usage.by_purpose.items()} == {
        "main": 1, "retry": 1, "repair": 1,
    }


def test_snapshot_dictionaries_do_not_mutate_live_accounting():
    ledger = BudgetLedger(limits(2))
    segment = ledger.start_segment(limits(2))
    segment.consume("model_calls")
    snapshot = ledger.usage
    with pytest.raises(ValidationError):
        snapshot.model_calls = 99
    snapshot.by_purpose.clear()
    assert ledger.usage.by_purpose["main"].model_calls == 1
    segment.consume("model_calls")
    assert snapshot.model_calls == 1 and ledger.usage.model_calls == 2


def test_limits_are_revalidated_and_copied_at_account_boundary():
    original = limits(1)
    invalid = original.model_copy(update={"model_calls": True})
    with pytest.raises(ValidationError):
        BudgetLedger(invalid)
    ledger = BudgetLedger(original)
    segment = ledger.start_segment(original)
    original.__dict__["model_calls"] = 99
    segment.consume("model_calls")
    with pytest.raises(BudgetExceeded):
        segment.consume("model_calls")
    assert ledger.usage.model_calls == 1


@pytest.mark.parametrize("resource,purpose", [("tokens", "main"), (True, "main"), ("model_calls", "untracked")])
def test_unknown_resource_or_purpose_does_not_charge(resource, purpose):
    ledger = BudgetLedger(limits(1))
    segment = ledger.start_segment(limits(1))
    with pytest.raises(ValueError):
        segment.consume(resource, purpose=purpose)
    assert ledger.usage.model_calls == 0


def test_concurrent_segments_cannot_overspend_shared_task_budget():
    ledger = BudgetLedger(limits(17))

    def attempt(_):
        segment = ledger.start_segment(limits(1))
        try:
            segment.consume("model_calls")
            return True
        except BudgetExceeded:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(80)))
    assert sum(results) == ledger.usage.model_calls == 17


def test_concurrent_children_cannot_overspend_parent_segment():
    ledger = BudgetLedger(limits(100))
    parent = ledger.start_segment(limits(5))
    children = [parent.child(limits(100), purpose="research") for _ in range(2)]

    def attempt(index):
        try:
            children[index % 2].consume("model_calls")
            return True
        except BudgetExceeded:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(40)))
    assert sum(results) == parent.usage.model_calls == ledger.usage.model_calls == 5
    assert sum(child.usage.model_calls for child in children) == 5


def answer(text="0.3"):
    return ModelResponse(finish_reason="stop", content=text)


def calculate(*ids, expression="0.1+0.2"):
    return ModelResponse(finish_reason="tool_calls", tool_calls=[
        ToolCall(id=call_id, name="calculator", arguments={"expression": expression}) for call_id in ids
    ])


def calculator_registry(used, handler=None):
    def tracked(expression):
        used.append(expression)
        return CALCULATOR_TOOL.handler(expression=expression)

    registry = ToolRegistry()
    registry.register(replace(CALCULATOR_TOOL, handler=handler or tracked))
    return registry


def test_runtime_zero_model_quota_never_calls_model_and_traces_not_started():
    ledger = BudgetLedger(limits(0))
    model = ScriptedModel([answer()])
    runtime = Runtime(model, budget=ledger.start_segment(limits(1)))
    result = runtime.run("不能发起调用")
    assert result.status == "partial" and result.error_code == "budget_exhausted"
    assert model.calls == [] and ledger.usage.model_calls == 0
    assert [(event.operation, event.status, event.error_code) for event in runtime.trace.events] == [
        ("model", "partial", "budget_exhausted"), ("run", "partial", "budget_exhausted"),
    ]
    assert runtime.trace.events[0].execution_state == "not_started"


def test_runtime_calculator_exact_budget_completes_with_separate_model_tool_counts():
    used = []
    ledger = BudgetLedger(limits(2, 1))
    segment = ledger.start_segment(limits(2, 1))
    model = ScriptedModel([calculate("calc"), answer()])
    result = Runtime(model, calculator_registry(used), budget=segment).run("计算")
    assert result.status == "completed" and used == ["0.1+0.2"]
    assert (ledger.usage.model_calls, ledger.usage.tool_calls) == (2, 1)
    assert json.loads(model.calls[1][-1].content) == {"ok": True, "result": "0.3"}


def test_runtime_new_fragment_reuses_task_budget_and_does_not_repeat_finished_tool():
    sessions = SessionStore()
    context = sessions.create(uuid4())
    used = []
    registry = calculator_registry(used)
    ledger = BudgetLedger(limits(2, 1))
    first_model = ScriptedModel([calculate("calc"), answer()])
    first = Runtime(first_model, registry, budget=ledger.start_segment(limits(1, 1)))
    result = first.run_session("计算", store=sessions, context=context)
    assert result.status == "partial" and result.error_code == "budget_exhausted"
    assert used == ["0.1+0.2"] and len(first_model.calls) == 1
    second_model = ScriptedModel([answer()])
    second = Runtime(second_model, registry, budget=ledger.start_segment(limits(1, 0)))
    result = second.run_session("根据已有工具结果继续回答", store=sessions, context=context)
    assert result.status == "completed" and used == ["0.1+0.2"]
    assert any(message.role == "tool" for message in second_model.calls[0])
    assert (ledger.usage.model_calls, ledger.usage.tool_calls) == (2, 1)
    refused_model = ScriptedModel([answer()])
    refused = Runtime(refused_model, registry, budget=ledger.start_segment(limits(5, 5)))
    assert refused.run_session("再次请求", store=sessions, context=context).error_code == "budget_exhausted"
    assert refused_model.calls == []


def test_runtime_partial_tool_batch_pairs_not_started_and_preserves_success():
    sessions = SessionStore()
    context = sessions.create(uuid4())
    used = []
    ledger = BudgetLedger(limits(5, 5))
    model = ScriptedModel([calculate("one", "two", "three"), answer()])
    runtime = Runtime(model, calculator_registry(used), budget=ledger.start_segment(limits(5, 1)))
    result = runtime.run_session("只够一个工具", store=sessions, context=context)
    assert result.status == "partial" and result.error_code == "budget_exhausted"
    assert used == ["0.1+0.2"] and len(model.calls) == 1
    snapshot = sessions.snapshot(context)
    outcomes = [message for message in snapshot.messages if message.role == "tool"]
    assert [message.tool_call_id for message in outcomes] == ["one", "two", "three"]
    assert json.loads(outcomes[0].content) == {"ok": True, "result": "0.3"}
    for message in outcomes[1:]:
        error = json.loads(message.content)["error"]
        assert error["code"] == "budget_exhausted" and error["execution_state"] == "not_started"
        assert error["scope"] == "segment" and error["resource"] == "tool_calls"
    assert snapshot.task.status == "partial"
    assert runtime.trace.events[-2].execution_state == "not_started"
    followup = Runtime(ScriptedModel([answer()]), budget=ledger.start_segment(limits(1, 0)))
    assert followup.run_session("查看先前结果", store=sessions, context=context).status == "completed"
    assert used == ["0.1+0.2"] and ledger.usage.tool_calls == 1


def test_runtime_zero_tool_quota_blocks_handler_before_side_effect():
    sessions = SessionStore()
    context = sessions.create(uuid4())
    used = []
    ledger = BudgetLedger(limits(2, 0))
    model = ScriptedModel([calculate("calc"), answer()])
    runtime = Runtime(model, calculator_registry(used), budget=ledger.start_segment(limits(2, 1)))
    assert runtime.run_session("计算", store=sessions, context=context).error_code == "budget_exhausted"
    assert used == [] and len(model.calls) == 1
    assert (ledger.usage.model_calls, ledger.usage.tool_calls) == (1, 0)
    outcome = next(message for message in sessions.snapshot(context).messages if message.role == "tool")
    error = json.loads(outcome.content)["error"]
    assert error["scope"] == "task" and error["recovery"] == "review_task_budget"


def test_runtime_model_failure_keeps_attempt_charged_and_has_no_automatic_retry():
    ledger = BudgetLedger(limits(2))
    segment = ledger.start_segment(limits(1))
    model = ScriptedModel([RuntimeError("private-error"), answer()])
    runtime = Runtime(model, budget=segment)
    assert runtime.run("失败").error_code == "model_call_failed"
    assert runtime.run("再调用").error_code == "budget_exhausted"
    assert len(model.calls) == 1 and ledger.usage.model_calls == 1
    assert "private-error" not in runtime.trace.to_json()
    retry = ledger.start_segment(limits(1), purpose="retry")
    assert Runtime(ScriptedModel([answer()]), budget=retry).run("显式新片段").status == "completed"
    assert ledger.usage.model_calls == 2 and ledger.usage.by_purpose["retry"].model_calls == 1


def test_runtime_tool_parameter_repair_is_charged_in_same_budget():
    used = []
    ledger = BudgetLedger(limits(3, 2))
    model = ScriptedModel([calculate("bad", expression=123), calculate("fixed"), answer()])
    result = Runtime(model, calculator_registry(used), budget=ledger.start_segment(limits(3, 2))).run("纠错")
    assert result.status == "completed" and used == ["0.1+0.2"]
    assert (ledger.usage.model_calls, ledger.usage.tool_calls) == (3, 2)
    assert ledger.usage.by_purpose["main"].model_calls == 1
    assert ledger.usage.by_purpose["main"].tool_calls == 1
    assert ledger.usage.by_purpose["repair"].model_calls == 2
    assert ledger.usage.by_purpose["repair"].tool_calls == 1


def test_runtime_child_model_cannot_bypass_parent_segment_limit():
    ledger = BudgetLedger(limits(10))
    parent = ledger.start_segment(limits(1))
    child = parent.child(limits(5), purpose="research")
    assert Runtime(ScriptedModel([answer()]), budget=child).run("子调用").status == "completed"
    refused = ScriptedModel([answer()])
    assert Runtime(refused, budget=parent).run("主调用").error_code == "budget_exhausted"
    assert refused.calls == [] and ledger.usage.by_purpose["research"].model_calls == 1


def test_budget_exception_from_started_handler_is_not_misreported_as_not_started():
    def started(expression):
        raise BudgetExceeded("task", "model_calls")

    ledger = BudgetLedger(limits(2, 2))
    runtime = Runtime(ScriptedModel([calculate("calc")]), calculator_registry([], handler=started),
                      budget=ledger.start_segment(limits(2, 2)))
    result = runtime.run("内部异常")
    assert result.error_code == "tool_execution_failed"
    tool_event = next(event for event in runtime.trace.events if event.operation == "tool")
    assert tool_event.execution_state == "unknown" and ledger.usage.tool_calls == 1


def test_budget_does_not_grant_plan_write_permission():
    sessions = SessionStore()
    context = sessions.create(uuid4())
    todos = TodoService(sessions)
    registry = todo_registry(todos, TaskController(sessions), context)
    model = ScriptedModel([ModelResponse(finish_reason="tool_calls", tool_calls=[
        ToolCall(id="add", name="todo_add", arguments={"title": "不能写入"}),
    ])])
    ledger = BudgetLedger(limits(2, 2))
    result = Runtime(model, registry, budget=ledger.start_segment(limits(2, 2))).run_session(
        "请求写入", store=sessions, context=context,
    )
    assert result.status == "waiting_user" and result.error_code == "plan_read_only"
    assert todos.list_items(context) == [] and ledger.usage.tool_calls == 1


def test_runtime_rejects_untyped_budget_before_model():
    model = ScriptedModel([answer()])
    with pytest.raises(TypeError):
        Runtime(model, budget={"model_calls": 10})
    assert model.calls == []


def test_new_http_adapter_cannot_reset_shared_task_budget():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={
            "status": "completed", "model": "deepseek-flash", "output": [{
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "合成回答"}],
            }],
        })

    ledger = BudgetLedger(limits(1))
    for expected in ("completed", "partial"):
        with DeepSeekResponsesModel(DeepSeekSettings(api_key="unit-budget-token", max_requests=10),
                                   transport=httpx.MockTransport(handle)) as model:
            result = Runtime(model, budget=ledger.start_segment(limits(10))).run("固定离线输入")
            assert result.status == expected
            assert model.request_count == (1 if expected == "completed" else 0)
    assert len(requests) == ledger.usage.model_calls == 1
