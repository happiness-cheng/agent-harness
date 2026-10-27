import json
import sqlite3
import subprocess
import sys
from uuid import UUID, uuid4

import pytest

from min_agent.budget import BudgetExceeded, BudgetLedger, BudgetLimits, BudgetPersistenceError
from min_agent.budget_persistence import SessionBudgets
from min_agent.models import ModelResponse, ToolCall
from min_agent.runtime import Runtime
from min_agent.sessions import ExecutionContext, SessionStore
from min_agent.storage import SQLiteStorage
from min_agent.task_state import ActionSpec, GoalSpec, TaskController
from min_agent.tools.todo import TodoService
from min_agent.tools.todo_tools import todo_registry
from tests.fakes.model import ScriptedModel


def limits(models=3, tools=3):
    return BudgetLimits(model_calls=models, tool_calls=tools)


def setup(storage, total=None):
    sessions = SessionStore(storage=storage)
    context = sessions.create(uuid4())
    budgets = SessionBudgets(sessions)
    budget_id = budgets.create(context, total or limits())
    return sessions, context, budgets, budget_id


def answer():
    return ModelResponse(finish_reason="stop", content="根据已有真实结果回答")


def test_restart_restores_total_and_existing_parent_child_segments(tmp_path):
    path = tmp_path / "budget.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        sessions, context, budgets, budget_id = setup(storage)
        ledger = budgets.open(context, budget_id)
        parent = ledger.start_segment(limits(1))
        child = parent.child(limits(), purpose="research")
        child.consume("model_calls")
        expected = ledger.snapshot()
        assert storage.load(context).snapshot.budgets[budget_id].state == expected
    with SQLiteStorage.open(path) as storage:
        sessions = SessionStore(storage=storage)
        ledger = SessionBudgets(sessions).open(context, budget_id)
        assert ledger.snapshot() == expected
        with pytest.raises(BudgetExceeded):
            ledger.segment(child.segment_id).consume("model_calls")
        ledger.start_segment(limits()).consume("model_calls")
        assert ledger.usage.model_calls == 2


def test_restart_runtime_uses_saved_tool_result_without_replaying_write(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        sessions, context, budgets, budget_id = setup(storage, limits(2, 1))
        controller = TaskController(sessions)
        proposal = controller.propose(context, GoalSpec(
            objective="记录周报", completion_criteria=("真实记录一条",),
            actions=(ActionSpec(operation="todo.add", parameters={"title": "周报", "due_date": None}),),
        ))
        controller.confirm(context, proposal.proposal_id)
        controller.select_mode(context, "build")
        todos = TodoService(sessions)
        model = ScriptedModel([ModelResponse(finish_reason="tool_calls", tool_calls=[
            ToolCall(id="add", name="todo_add", arguments={"title": "周报"}),
        ])])
        segment = budgets.open(context, budget_id).start_segment(limits(1, 1))
        result = Runtime(model, todo_registry(todos, controller, context), budget=segment).run_session(
            "记录", store=sessions, context=context,
        )
        assert result.error_code == "budget_exhausted" and len(todos.list_items(context)) == 1
        item_id = todos.list_items(context)[0].id
    with SQLiteStorage.open(path) as storage:
        sessions = SessionStore(storage=storage)
        ledger = SessionBudgets(sessions).open(context, budget_id)
        model = ScriptedModel([answer()])
        result = Runtime(model, budget=ledger.start_segment(limits(1, 0))).run_session(
            "依据已有结果继续", store=sessions, context=context,
        )
        assert result.status == "completed"
        assert any(message.role == "tool" for message in model.calls[0])
        assert [item.id for item in TodoService(sessions).list_items(context)] == [item_id]
        assert ledger.usage.model_calls == 2 and ledger.usage.tool_calls == 1


def test_services_share_one_live_ledger_and_do_not_reset_task_id_on_new_run(tmp_path):
    with SQLiteStorage.create_new(tmp_path / "shared.sqlite3") as storage:
        sessions, context, budgets, budget_id = setup(storage)
        first = budgets.open(context, budget_id)
        assert SessionBudgets(sessions).open(context, budget_id) is first
        segment = first.start_segment(limits())
        tasks = []
        for _ in range(2):
            assert Runtime(ScriptedModel([answer()]), budget=segment).run_session("问答", store=sessions, context=context).status == "completed"
            tasks.append(sessions.snapshot(context).task.task_id)
        assert tasks[0] != tasks[1]
        assert list(sessions.snapshot(context).budgets) == [budget_id]
        assert first.usage.model_calls == 2


@pytest.mark.parametrize("kind", ["omitted", "memory", "forged_binding", "other_session", "other_store", "no_context"])
def test_persistent_budget_binding_cannot_be_omitted_or_misbound(tmp_path, kind):
    path = tmp_path / "binding.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        sessions, context, budgets, budget_id = setup(storage)
        segment = budgets.open(context, budget_id).start_segment(limits())
        model = ScriptedModel([answer()])
        target_store, target_context = sessions, context
        if kind == "omitted":
            segment = None
        elif kind == "memory":
            segment = BudgetLedger(limits()).start_segment(limits())
        elif kind == "forged_binding":
            segment = BudgetLedger(limits(99), binding=segment.binding).start_segment(limits(99))
        elif kind == "other_session":
            target_context = sessions.create(context.owner_id)
        elif kind == "other_store":
            target_store = SessionStore(storage=storage)
        runtime = Runtime(model, budget=segment)
        with pytest.raises(PermissionError):
            if kind == "no_context":
                runtime.run("无上下文")
            else:
                runtime.run_session("错误绑定", store=target_store, context=target_context)
        assert model.calls == [] and sessions.snapshot(context).task is None


def test_other_owner_cannot_open_or_select_budget(tmp_path):
    with SQLiteStorage.create_new(tmp_path / "owner.sqlite3") as storage:
        sessions, context, budgets, budget_id = setup(storage)
        forged = ExecutionContext(owner_id=uuid4(), session_id=context.session_id)
        for operation in (budgets.open, budgets.select):
            with pytest.raises(PermissionError):
                operation(forged, budget_id)


def test_old_identical_reservation_is_rejected_by_explicit_revision_cas(tmp_path):
    path = tmp_path / "cas.sqlite3"
    with SQLiteStorage.create_new(path) as first_storage:
        first_store, context, budgets, budget_id = setup(first_storage)
        first = budgets.open(context, budget_id).start_segment(limits())
        with SQLiteStorage.open(path) as second_storage:
            second_store = SessionStore(storage=second_storage)
            old = SessionBudgets(second_store).open(context, budget_id).segment(first.segment_id)
            first.consume("model_calls")
            with pytest.raises(BudgetPersistenceError):
                old.consume("model_calls")
            assert old.usage.model_calls == 0
            assert first_storage.load(context).snapshot.budgets[budget_id].state.usage.model_calls == 1
            with pytest.raises(ValueError):
                second_store.begin(context)


@pytest.mark.parametrize("resource", ["model_calls", "tool_calls"])
def test_reservation_sql_failure_never_starts_model_or_tool(tmp_path, resource):
    path = tmp_path / "failure.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        sessions, context, budgets, budget_id = setup(storage)
        ledger = budgets.open(context, budget_id)
        segment = ledger.start_segment(limits())
        original = storage.load(context).snapshot.budgets[budget_id]
        json_path = f'$.budgets."{budget_id}".state.usage.{resource}'
        with sqlite3.connect(path) as connection:
            connection.execute(
                f"CREATE TRIGGER fail_budget BEFORE UPDATE ON sessions WHEN json_extract(NEW.snapshot_json, '{json_path}') > 0 "
                "BEGIN SELECT RAISE(ABORT, 'private-budget-failure'); END"
            )
        script = [answer()] if resource == "model_calls" else [ModelResponse(finish_reason="tool_calls", tool_calls=[
            ToolCall(id="write", name="todo_add", arguments={"title": "不应写入"}),
        ])]
        model = ScriptedModel(script)
        todos = TodoService(sessions)
        runtime = Runtime(model, todo_registry(todos, TaskController(sessions), context), budget=segment)
        result = runtime.run_session("拒绝派发", store=sessions, context=context)
        assert result.status == "failed" and result.error_code == "budget_persistence_failed"
        assert len(model.calls) == (0 if resource == "model_calls" else 1)
        assert todos.list_items(context) == []
        assert ledger.usage.tool_calls == 0
        assert ledger.usage.model_calls == (0 if resource == "model_calls" else 1)
        assert "private-budget-failure" not in runtime.trace.to_json()
        stored = storage.load(context).snapshot.budgets[budget_id]
        if resource == "model_calls":
            assert stored == original
        else:
            assert stored.state.usage.model_calls == 1 and stored.state.usage.tool_calls == 0


def test_ordinary_snapshot_save_cannot_erase_or_rewind_budget(tmp_path):
    with SQLiteStorage.create_new(tmp_path / "erase.sqlite3") as storage:
        sessions, context, budgets, budget_id = setup(storage)
        budgets.open(context, budget_id).start_segment(limits()).consume("model_calls")
        original = storage.load(context)
        candidate = original.snapshot.model_copy(deep=True)
        candidate.budgets.clear()
        candidate.active_budget_id = None
        with pytest.raises(ValueError):
            storage.save(context, candidate, original.todos)
        assert storage.load(context) == original


def test_new_task_and_selecting_old_task_preserve_old_exhausted_total(tmp_path):
    with SQLiteStorage.create_new(tmp_path / "tasks.sqlite3") as storage:
        sessions, context, budgets, first_id = setup(storage, limits(1))
        first = budgets.open(context, first_id)
        segment = first.start_segment(limits(1))
        segment.consume("model_calls")
        second_id = budgets.create(context, limits(2))
        assert first_id != second_id
        with pytest.raises(PermissionError):
            Runtime(ScriptedModel([answer()]), budget=segment).run_session("旧任务", store=sessions, context=context)
        budgets.select(context, first_id)
        model = ScriptedModel([answer()])
        result = Runtime(model, budget=first.start_segment(limits())).run_session("旧任务继续", store=sessions, context=context)
        assert result.error_code == "budget_exhausted" and model.calls == []
        assert set(sessions.snapshot(context).budgets) == {first_id, second_id}


@pytest.mark.parametrize("corruption", ["owner", "usage", "active"])
def test_corrupt_persistent_budget_refuses_startup_instead_of_reset(tmp_path, corruption):
    path = tmp_path / "corrupt.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        sessions, context, budgets, budget_id = setup(storage)
        budgets.open(context, budget_id).start_segment(limits()).consume("model_calls")
    with sqlite3.connect(path) as connection:
        data = json.loads(connection.execute("SELECT snapshot_json FROM sessions").fetchone()[0])
        if corruption == "owner":
            data["budgets"][str(budget_id)]["owner_id"] = str(uuid4())
        elif corruption == "usage":
            data["budgets"][str(budget_id)]["state"]["usage"]["model_calls"] = 0
        else:
            data["active_budget_id"] = None
        connection.execute("UPDATE sessions SET snapshot_json=?", (json.dumps(data),))
    with SQLiteStorage.open(path) as storage:
        with pytest.raises(ValueError):
            SessionStore(storage=storage)


def test_separate_process_recovers_exhausted_budget_without_new_model_call(tmp_path):
    path = tmp_path / "process.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        _, context, budgets, budget_id = setup(storage, limits(1))
        budgets.open(context, budget_id).start_segment(limits(1)).consume("model_calls")
    script = """
import sys
from uuid import UUID
from min_agent.budget import BudgetLimits
from min_agent.budget_persistence import SessionBudgets
from min_agent.sessions import ExecutionContext, SessionStore
from min_agent.storage import SQLiteStorage
from min_agent.runtime import Runtime
class Never:
    def generate(self, *args, **kwargs):
        raise AssertionError('预算不足不应启动模型')
with SQLiteStorage.open(sys.argv[1]) as storage:
    store = SessionStore(storage=storage)
    context = ExecutionContext(owner_id=UUID(sys.argv[2]),session_id=UUID(sys.argv[3]))
    ledger = SessionBudgets(store).open(context,UUID(sys.argv[4]))
    result = Runtime(Never(),budget=ledger.start_segment(BudgetLimits(model_calls=10,tool_calls=0))).run_session('继续',store=store,context=context)
    assert result.error_code == 'budget_exhausted' and ledger.usage.model_calls == 1
"""
    result = subprocess.run([sys.executable, "-X", "utf8", "-I", "-c", script, str(path), str(context.owner_id), str(context.session_id), str(budget_id)],
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr


def test_old_snapshot_without_budget_fields_remains_compatible(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        context = SessionStore(storage=storage).create(uuid4())
    with sqlite3.connect(path) as connection:
        data = json.loads(connection.execute("SELECT snapshot_json FROM sessions").fetchone()[0])
        data.pop("budgets")
        data.pop("active_budget_id")
        connection.execute("UPDATE sessions SET snapshot_json=?", (json.dumps(data),))
    with SQLiteStorage.open(path) as storage:
        sessions = SessionStore(storage=storage)
        assert sessions.snapshot(context).budgets == {}
        assert Runtime(ScriptedModel([answer()])).run_session("旧会话", store=sessions, context=context).status == "completed"
