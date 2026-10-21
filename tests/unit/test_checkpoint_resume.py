import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from min_agent.budget import BudgetLedger, BudgetLimits
from min_agent.budget_persistence import SessionBudgets
from min_agent.checkpoints import CheckpointNote, CheckpointPolicy
from min_agent.models import ModelResponse, ToolCall
from min_agent.runtime import Runtime
from min_agent.sessions import SessionStore
from min_agent.storage import SQLiteStorage
from min_agent.task_state import ActionSpec, GoalSpec, TaskController
from min_agent.tools.calculator import CALCULATOR_TOOL
from min_agent.tools.registry import ToolRegistry
from min_agent.tools.todo import TodoService
from min_agent.tools.todo_tools import todo_registry
from tests.fakes.model import ScriptedModel


def limits(models=3, tools=2):
    return BudgetLimits(model_calls=models, tool_calls=tools)


def registry():
    tools = ToolRegistry()
    tools.register(CALCULATOR_TOOL)
    return tools


def note_response():
    return ModelResponse(finish_reason="stop", content=json.dumps({
        "achievements": ["已有计算结果"], "pending": ["还需核对任务目标"],
        "blockers": [], "next_steps": ["用户确认后依据原结果继续"],
    }, ensure_ascii=False))


def calculation(*ids):
    return ModelResponse(finish_reason="tool_calls", tool_calls=[
        ToolCall(id=call_id, name="calculator", arguments={"expression": "0.1+0.2"})
        for call_id in ids
    ])


def test_remaining_obeys_tightest_task_parent_and_segment_limits():
    ledger = BudgetLedger(limits(4, 5))
    parent = ledger.start_segment(limits(3, 2))
    child = parent.child(limits(10, 10))
    child.consume("model_calls")
    ledger.start_segment(limits()).consume("model_calls")
    assert child.remaining == limits(2, 2)
    assert parent.remaining == limits(2, 2)
    assert ledger.usage.model_calls == 2
    child.consume("tool_calls")
    assert child.remaining == limits(2, 1)


@pytest.mark.parametrize("options", [
    {"reserve_model_calls": 0}, {"reserve_model_calls": -1},
    {"reserve_model_calls": True}, {"reserve_model_calls": 1.5},
    {"reserve_tool_calls": -1}, {"reserve_tool_calls": False}, {"extra": 1},
])
def test_policy_rejects_invalid_reservations(options):
    with pytest.raises(ValidationError):
        CheckpointPolicy(**options)


def test_policy_requires_explicit_budget():
    with pytest.raises(ValueError):
        Runtime(ScriptedModel([]), checkpoint_policy=CheckpointPolicy())


def test_soft_limit_keeps_one_model_call_for_tool_free_checkpoint():
    ledger = BudgetLedger(limits())
    segment = ledger.start_segment(limits(2, 2))
    model = ScriptedModel([calculation("done"), note_response()])
    result = Runtime(model, registry(), budget=segment, checkpoint_policy=CheckpointPolicy()).run("计算后继续核对")
    assert result.status == "partial" and result.error_code == "budget_checkpoint"
    checkpoint = result.checkpoint
    assert checkpoint.reason == "model_reserve" and checkpoint.closing_status == "returned"
    assert checkpoint.model_note.pending == ("还需核对任务目标",)
    assert checkpoint.segment_id == segment.segment_id
    assert checkpoint.task_usage.model_calls == 2 and checkpoint.task_usage.tool_calls == 1
    assert len(model.calls) == 2 and model.tool_schemas[0] and model.tool_schemas[1] == []
    assert "achievements" in model.calls[1][-1].content
    assert "仓促" in model.calls[1][-1].content
    evidence = checkpoint.tool_results[0]
    assert evidence.call_id == "done" and evidence.execution_state == "returned"
    assert evidence.ok is True and evidence.message_index >= 0
    assert json.loads(model.calls[1][evidence.message_index].content)["result"] == "0.3"


def test_soft_limit_checks_cumulative_quota_even_with_large_fresh_segment():
    ledger = BudgetLedger(limits(2, 0))
    ledger.start_segment(limits()).consume("model_calls", purpose="research")
    model = ScriptedModel([note_response()])
    result = Runtime(model, budget=ledger.start_segment(limits(99, 99)),
                     checkpoint_policy=CheckpointPolicy()).run("继续")
    assert result.status == "partial" and result.checkpoint.closing_status == "returned"
    assert len(model.calls) == 1 and ledger.usage.model_calls == 2
    assert ledger.usage.by_purpose["research"].model_calls == 1


def test_no_budget_returns_factual_checkpoint_without_calling_model():
    ledger = BudgetLedger(limits(0, 0))
    model = ScriptedModel([])
    result = Runtime(model, budget=ledger.start_segment(limits()),
                     checkpoint_policy=CheckpointPolicy()).run("还有任务")
    assert result.status == "partial" and result.error_code == "budget_exhausted"
    assert result.checkpoint.closing_status == "not_started"
    assert result.checkpoint.model_note is None
    assert result.checkpoint.closing_error == "budget_exhausted" and model.calls == []
    assert ledger.usage.model_calls == 0


@pytest.mark.parametrize("closing", [
    RuntimeError("private-provider-message"),
    ModelResponse(finish_reason="stop", content="全部完成了"),
    ModelResponse(finish_reason="length", content="{截断"),
    calculation("forbidden"),
    ModelResponse(finish_reason="stop", content='{"achievements":[],"pending":[],"blockers":[],"next_steps":[],"extra":1}'),
])
def test_closing_failure_keeps_facts_and_never_executes_more_tools(closing):
    ledger = BudgetLedger(limits(2, 1))
    model = ScriptedModel([calculation("saved"), closing])
    runtime = Runtime(model, registry(), budget=ledger.start_segment(limits()), checkpoint_policy=CheckpointPolicy())
    result = runtime.run("计算")
    assert result.status == "partial" and result.checkpoint.model_note is None
    assert result.checkpoint.closing_status in {"failed", "invalid_response"}
    assert result.checkpoint.tool_results[0].ok is True
    assert ledger.usage.model_calls == 2 and ledger.usage.tool_calls == 1
    assert len(model.calls) == 2
    assert "private-provider-message" not in runtime.trace.to_json()
    assert "private-provider-message" not in result.checkpoint.model_dump_json()


def test_tool_reservation_stops_batch_pairs_unstarted_requests_then_closes():
    ledger = BudgetLedger(limits(4, 2))
    model = ScriptedModel([calculation("first", "second", "third"), note_response()])
    result = Runtime(model, registry(), budget=ledger.start_segment(limits()),
                     checkpoint_policy=CheckpointPolicy(reserve_tool_calls=1)).run("批量计算")
    assert result.checkpoint.reason == "tool_reserve" and result.status == "partial"
    evidence = result.checkpoint.tool_results
    assert [item.call_id for item in evidence] == ["first", "second", "third"]
    assert [item.execution_state for item in evidence] == ["returned", "not_started", "not_started"]
    assert ledger.usage.tool_calls == 1 and ledger.usage.model_calls == 2
    for item in evidence[1:]:
        error = json.loads(model.calls[1][item.message_index].content)["error"]
        assert error["code"] == "budget_checkpoint" and error["execution_state"] == "not_started"


def test_session_history_keeps_complete_pairs_and_returned_checkpoint_is_isolated():
    sessions = SessionStore()
    context = sessions.create(uuid4())
    model = ScriptedModel([calculation("once", "blocked"), note_response()])
    ledger = BudgetLedger(limits(2, 1))
    result = Runtime(model, registry(), budget=ledger.start_segment(limits()),
                     checkpoint_policy=CheckpointPolicy()).run_session("计算", store=sessions, context=context)
    saved = sessions.snapshot(context)
    assert saved.task.status == "partial" and SessionStore._pending(saved.messages) == set()
    assert result.checkpoint.session_id == context.session_id
    assert result.checkpoint.history_length == len(saved.messages)
    assert saved.messages[-1].role == "assistant"
    assert result.checkpoint.model_note.achievements == ("已有计算结果",)
    assert len(model.calls) == 2


def test_normal_reply_before_threshold_is_completed_without_checkpoint():
    model = ScriptedModel([ModelResponse(finish_reason="stop", content="直接答复")])
    result = Runtime(model, budget=BudgetLedger(limits()).start_segment(limits()),
                     checkpoint_policy=CheckpointPolicy()).run("问答")
    assert result.status == "completed" and result.checkpoint is None and len(model.calls) == 1


def test_legacy_without_policy_still_uses_last_call_for_normal_answer():
    model = ScriptedModel([ModelResponse(finish_reason="stop", content="原行为")])
    result = Runtime(model, budget=BudgetLedger(limits(1)).start_segment(limits(1))).run("问答")
    assert result.status == "completed" and result.checkpoint is None


def test_note_requires_all_four_sections_and_rejects_oversized_text():
    with pytest.raises(ValidationError):
        CheckpointNote(achievements=())
    with pytest.raises(ValidationError):
        CheckpointNote(achievements=("x" * 1001,), pending=(), blockers=(), next_steps=())


def test_persistent_todo_write_and_closing_usage_are_saved_but_checkpoint_is_return_only(tmp_path):
    path = tmp_path / "checkpoint.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        sessions = SessionStore(storage=storage)
        context = sessions.create(uuid4())
        controller = TaskController(sessions)
        proposal = controller.propose(context, GoalSpec(
            objective="记录周报并继续核对", completion_criteria=("记录真实保存且核对完成",),
            actions=(ActionSpec(operation="todo.add", parameters={"title": "周报", "due_date": None}),),
        ))
        controller.confirm(context, proposal.proposal_id)
        controller.select_mode(context, "build")
        budgets = SessionBudgets(sessions)
        budget_id = budgets.create(context, limits(3, 1))
        ledger = budgets.open(context, budget_id)
        model = ScriptedModel([ModelResponse(finish_reason="tool_calls", tool_calls=[
            ToolCall(id="written", name="todo_add", arguments={"title": "周报"}),
        ]), note_response()])
        todos = TodoService(sessions)
        result = Runtime(model, todo_registry(todos, controller, context),
                         budget=ledger.start_segment(limits(2, 1)),
                         checkpoint_policy=CheckpointPolicy()).run_session("开始", store=sessions, context=context)
        assert result.status == "partial" and result.checkpoint.budget_id == budget_id
        assert result.checkpoint.tool_results[0].call_id == "written"
        assert result.checkpoint.tool_results[0].ok is True
        saved_id = todos.list_items(context)[0].id
        assert len(todos.list_items(context)) == 1
        assert ledger.usage.model_calls == 2 and ledger.usage.tool_calls == 1
        assert len(model.calls) == 2 and model.tool_schemas[-1] == []
        closing_state = json.loads(model.calls[-1][1].content)["application_state"]
        assert closing_state["confirmed_goal_id"] == str(proposal.proposal_id)
        assert closing_state["proposal"]["goal"]["objective"] == "记录周报并继续核对"
    with SQLiteStorage.open(path) as storage:
        reopened = SessionStore(storage=storage)
        assert [item.id for item in TodoService(reopened).list_items(context)] == [saved_id]
        assert SessionBudgets(reopened).open(context, budget_id).usage.model_calls == 2
        saved = reopened.snapshot(context)
        assert saved.task.status == "partial" and not SessionStore._pending(saved.messages)
        assert result.checkpoint.history_length == len(saved.messages)


def test_closing_reservation_storage_failure_never_calls_model_or_retries():
    attempts = []

    def persist(snapshot):
        if snapshot.usage.model_calls:
            attempts.append(snapshot)
            raise OSError("private-disk-error")

    ledger = BudgetLedger(limits(1), on_change=persist)
    model = ScriptedModel([])
    runtime = Runtime(model, budget=ledger.start_segment(limits()), checkpoint_policy=CheckpointPolicy())
    for _ in range(2):
        result = runtime.run("收尾")
        assert result.status == "failed" and result.error_code == "budget_persistence_failed"
        assert result.checkpoint.closing_status == "not_started"
        assert result.checkpoint.model_note is None
    assert len(attempts) == 1 and model.calls == [] and ledger.usage.model_calls == 0
    assert "private-disk-error" not in runtime.trace.to_json()


def test_parent_quota_and_custom_reservation_trigger_early_without_spending_all():
    ledger = BudgetLedger(limits(10))
    parent = ledger.start_segment(limits(3))
    child = parent.child(limits(20), purpose="research")
    child.consume("model_calls")
    model = ScriptedModel([note_response()])
    result = Runtime(model, budget=child, checkpoint_policy=CheckpointPolicy(reserve_model_calls=2)).run("收尾")
    assert result.status == "partial" and len(model.calls) == 1
    assert child.remaining.model_calls == 1
    assert ledger.usage.by_purpose["research"].model_calls == 2


def test_model_cannot_turn_checkpoint_into_verified_completion():
    claims = ModelResponse(finish_reason="stop", content=json.dumps({
        "achievements": ["我宣称所有工作都完成"], "pending": [], "blockers": [], "next_steps": [],
    }, ensure_ascii=False))
    result = Runtime(ScriptedModel([claims]), budget=BudgetLedger(limits(1)).start_segment(limits()),
                     checkpoint_policy=CheckpointPolicy()).run("收尾")
    assert result.status == "partial" and result.error_code == "budget_checkpoint"
    assert result.checkpoint.tool_results == ()


@pytest.mark.parametrize("response", [None, "not-a-response", 123])
def test_non_response_closing_is_invalid_without_fake_success(response):
    class Invalid:
        def generate(self, *args, **kwargs):
            return response

    result = Runtime(Invalid(), budget=BudgetLedger(limits(1)).start_segment(limits()),
                     checkpoint_policy=CheckpointPolicy()).run("收尾")
    assert result.status == "partial" and result.checkpoint.model_note is None
    assert result.checkpoint.closing_error == "checkpoint_invalid_response"


def test_policy_and_note_are_revalidated_at_public_entry():
    with pytest.raises(TypeError):
        Runtime(ScriptedModel([]), budget=BudgetLedger(limits()).start_segment(limits()), checkpoint_policy={})
    with pytest.raises(ValidationError):
        Runtime(ScriptedModel([]), budget=BudgetLedger(limits()).start_segment(limits()),
                checkpoint_policy=CheckpointPolicy.model_construct(reserve_model_calls=True, reserve_tool_calls=0))
