"""B0-R1 新授权生命周期：pending 绑定、定向恢复、旧记录与 journal 边界。"""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest

from min_agent.application import HarnessApplication
from min_agent.budget import BudgetLimits
from min_agent.budget_persistence import SessionBudgets
from min_agent.checkpoints import CheckpointPolicy
from min_agent.models import ModelResponse, ToolCall
from min_agent.runtime import Runtime
from min_agent.sessions import ExecutionContext, SessionSnapshot, SessionStore
from min_agent.storage import SQLiteStorage, _SESSION_COMMIT, VersionConflict
from min_agent.task_state import (ActionSpec, AuthorizationRequired, GoalProposal, GoalSpec,
                                  TaskController, WorkState, WorkStateTransition)
from min_agent.tools.todo import TodoService
from min_agent.tools.todo_tools import todo_registry
from tests.fakes.model import ScriptedModel


def goal(title="synthetic"):
    return GoalSpec(objective="合成目标", completion_criteria=("记录指定待办",),
                    actions=(ActionSpec(operation="todo.add",
                                        parameters={"title": title, "due_date": None}),))


def approve(controller, context, title="synthetic"):
    proposal = controller.propose(context, goal(title))
    controller.confirm(context, proposal.proposal_id)
    controller.select_mode(context, "build")
    return proposal


def registry(store, context):
    return todo_registry(TodoService(store), TaskController(store), context)


def call(identity="add", title="synthetic"):
    return ToolCall(id=identity, name="todo_add", arguments={"title": title, "due_date": None})


def raw(path):
    with sqlite3.connect(str(path)) as connection:
        rows = connection.execute("SELECT snapshot_json FROM sessions ORDER BY session_id").fetchall()
        todos = connection.execute("SELECT item_json FROM todos ORDER BY session_id, position").fetchall()
        return {"sessions": [json.loads(row[0]) for row in rows],
                "todos": [json.loads(row[0]) for row in todos]}


def test_pending_binds_first_begin_and_completed_grant_is_not_inherited(tmp_path):
    path = tmp_path / "pending.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        store = SessionStore(storage=storage)
        context = store.create(uuid4())
        controller = TaskController(store)
        proposal = approve(controller, context)
        grant = controller.snapshot(context).grant
        assert grant.status == "pending_first_start"
        first = store.begin(context)
        assert controller.snapshot(context).grant.status == "bound"
        store.finish(context, first.task_id, status="completed")
        state = controller.snapshot(context)
        assert state.grant.status == "closed"
        assert state.grant.grant_id == grant.grant_id
        second = store.begin(context)
        assert second.logical_task_id != first.logical_task_id
        with pytest.raises(AuthorizationRequired) as captured:
            registry(store, context).execute(call("second"))
        assert captured.value.code == "logical_task_mismatch"
        store.finish(context, second.task_id, status="waiting_user")
        final = raw(path)
        assert len(final["todos"]) == 0
        assert final["sessions"][0]["task"]["status"] == "waiting_user"
        assert final["sessions"][0]["work_state"]["grant"]["status"] == "closed"
        assert final["sessions"][0]["work_state"]["proposal"]["proposal_id"] == str(proposal.proposal_id)


def test_no_grant_tasks_begin_finish_and_readonly_resume():
    store = SessionStore()
    context = store.create(uuid4())
    first = store.begin(context)
    store.finish(context, first.task_id, status="completed")
    second = store.begin(context)
    store.finish(context, second.task_id, status="waiting_user")
    third = store.begin(context)
    store.finish(context, third.task_id, status="failed")
    assert store.snapshot(context).work_state.grant is None
    resumed = store.begin(context, resume_task_id=third.task_id)
    assert resumed.logical_task_id == third.logical_task_id
    assert resumed.task_id != third.task_id
    store.finish(context, resumed.task_id, status="completed")
    assert store.snapshot(context).work_state.grant is None


def test_running_propose_revokes_grant_and_task_can_finish():
    store = SessionStore()
    context = store.create(uuid4())
    controller = TaskController(store)
    approve(controller, context)
    task = store.begin(context)
    assert controller.snapshot(context).grant.status == "bound"
    replacement = controller.propose(context, goal("new"))
    assert controller.snapshot(context).grant is None
    assert controller.snapshot(context).proposal.proposal_id == replacement.proposal_id
    store.finish(context, task.task_id, status="waiting_user")
    assert store.snapshot(context).task.status == "waiting_user"
    assert store.snapshot(context).work_state.grant is None


def test_paused_confirm_requires_explicit_resume_then_writes():
    store = SessionStore()
    context = store.create(uuid4())
    controller = TaskController(store)
    proposal = approve(controller, context)
    first = store.begin(context)
    store.finish(context, first.task_id, status="partial")
    assert controller.snapshot(context).grant.status == "paused"
    controller.confirm(context, proposal.proposal_id)
    paused = controller.snapshot(context).grant
    assert paused.status == "paused" and paused.attempt_id == first.task_id
    resumed = store.begin(context, resume_task_id=first.task_id)
    assert resumed.logical_task_id == first.logical_task_id
    assert resumed.task_id != first.task_id
    assert controller.snapshot(context).grant.status == "bound"
    assert registry(store, context).execute(call("resumed"))["ok"] is True
    store.finish(context, resumed.task_id, status="completed")
    assert controller.snapshot(context).grant.status == "closed"


def test_new_task_does_not_transfer_paused_grant_and_can_finish():
    store = SessionStore()
    context = store.create(uuid4())
    controller = TaskController(store)
    approve(controller, context)
    first = store.begin(context)
    store.finish(context, first.task_id, status="partial")
    paused = controller.snapshot(context).grant
    assert paused.status == "paused"
    normal = store.begin(context)
    assert normal.logical_task_id != first.logical_task_id
    with pytest.raises(AuthorizationRequired) as captured:
        registry(store, context).execute(call("normal"))
    assert captured.value.code == "logical_task_mismatch"
    store.finish(context, normal.task_id, status="waiting_user")
    state = controller.snapshot(context)
    assert store.snapshot(context).task.status == "waiting_user"
    assert state.grant == paused


def test_confirmation_after_completed_can_start_new_logical_task():
    store = SessionStore()
    context = store.create(uuid4())
    controller = TaskController(store)
    proposal = approve(controller, context)
    first = store.begin(context)
    store.finish(context, first.task_id, status="completed")
    assert controller.snapshot(context).grant.status == "closed"
    controller.confirm(context, proposal.proposal_id)
    assert controller.snapshot(context).grant.status == "pending_first_start"
    second = store.begin(context)
    assert second.logical_task_id != first.logical_task_id
    assert controller.snapshot(context).grant.status == "bound"
    assert registry(store, context).execute(call())["ok"] is True
    store.finish(context, second.task_id, status="completed")


def test_two_instances_race_pending_first_bind_has_one_winner(tmp_path):
    path = tmp_path / "race.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        first = SessionStore(storage=storage)
        context = first.create(uuid4())
        approve(TaskController(first), context)
        second = SessionStore(storage=SQLiteStorage.open(path))
        try:
            barrier = Barrier(2)

            def bind(store):
                barrier.wait(timeout=10)
                try:
                    task = store.begin(context)
                    return ("ok", task)
                except Exception as error:
                    return ("error", error)

            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = [future.result(timeout=10) for future in
                            [pool.submit(bind, first), pool.submit(bind, second)]]
            successes = [value for status, value in outcomes if status == "ok"]
            errors = [value for status, value in outcomes if status == "error"]
            assert len(successes) == 1
            assert len(errors) == 1 and isinstance(errors[0], VersionConflict)
            final = raw(path)
            assert len(final["sessions"]) == 1
            grant = final["sessions"][0]["work_state"]["grant"]
            assert grant["status"] == "bound"
            assert grant["attempt_id"] == str(successes[0].task_id)
        finally:
            second._storage.close()


def test_old_pending_grant_cannot_be_replayed_after_binding(tmp_path):
    path = tmp_path / "replay.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        store = SessionStore(storage=storage)
        context = store.create(uuid4())
        controller = TaskController(store)
        propose = controller.propose(context, goal())
        controller.confirm(context, propose.proposal_id)
        old = storage.load(context).snapshot
        task = store.begin(context)
        store.finish(context, task.task_id, status="completed")
        current = storage.load(context)
        forged = current.snapshot.model_copy(deep=True)
        forged.work_state = old.work_state
        forged.revision = current.snapshot.revision
        before = raw(path)
        with pytest.raises(Exception):
            storage.save(context, forged, current.todos,
                         _session_commit=_SESSION_COMMIT,
                         _work_state_transition=WorkStateTransition(
                             kind="bind_first_start",
                             grant_id=old.work_state.grant.grant_id,
                             logical_task_id=uuid4(), attempt_id=uuid4(), budget_id=None))
        assert raw(path) == before


def test_resume_rejects_both_targets_and_wrong_target(tmp_path):
    store = SessionStore()
    context = store.create(uuid4())
    task = store.begin(context)
    store.finish(context, task.task_id, status="failed")
    with pytest.raises(ValueError):
        store.begin(context, checkpoint_id=uuid4(), resume_task_id=task.task_id)
    with pytest.raises(PermissionError):
        store.begin(context, resume_task_id=uuid4())
    app = HarnessApplication(tmp_path / "app.sqlite3", memory_mode="off")
    try:
        sid = app.create_session()["session_id"]
        with pytest.raises(ValueError):
            app.resume(sid, checkpoint_id=str(uuid4()), task_id=str(uuid4()))
    finally:
        app.close()


def test_existing_unknown_without_receipt_is_not_replayed_by_new_grant(tmp_path, monkeypatch):
    path = tmp_path / "unknown.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        store = SessionStore(storage=storage)
        context = store.create(uuid4())
        controller = TaskController(store)
        approve(controller, context)
        task = store.begin(context)
        request = call("unknown-request")
        signature = json.dumps([request.name, request.arguments], sort_keys=True, ensure_ascii=False)
        store.set_extension(context, "operations", {"unknown-request": {
            "signature": signature, "status": "unknown"}})
        todo = registry(store, context)
        monkeypatch.setattr(todo, "_execute", lambda *args, **kwargs: pytest.fail("不应重放"))
        with pytest.raises(RuntimeError, match="核对"):
            todo.execute(request, operation_id="unknown-request")
        assert not TodoService(store).list_items(context)
        assert store.extension(context, "operations")["unknown-request"]["status"] == "unknown"
        store.finish(context, task.task_id, status="failed")


def test_committed_receipt_reconciles_without_handler(tmp_path, monkeypatch):
    path = tmp_path / "receipt.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        store = SessionStore(storage=storage)
        context = store.create(uuid4())
        controller = TaskController(store)
        approve(controller, context)
        task = store.begin(context)
        todo = registry(store, context)
        request = call("committed")
        first = todo.execute(request, operation_id="committed")
        journal = store.extension(context, "operations")
        journal["committed"]["status"] = "unknown"
        journal["committed"].pop("outcome", None)
        store.set_extension(context, "operations", journal)
        monkeypatch.setattr(todo, "_execute", lambda *args, **kwargs: pytest.fail("不应重调 handler"))
        result = todo.execute(request, operation_id="committed")
        assert json.loads(result["result"]) == json.loads(first["result"])
        assert len(TodoService(store).list_items(context)) == 1
        store.finish(context, task.task_id, status="completed")


def test_fresh_admission_rejection_cleans_only_new_journal(tmp_path):
    path = tmp_path / "cleanup.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        store = SessionStore(storage=storage)
        context = store.create(uuid4())
        controller = TaskController(store)
        proposal = approve(controller, context)
        first = store.begin(context)
        store.finish(context, first.task_id, status="completed")
        second = store.begin(context)
        store.set_extension(context, "operations", {
            "history": {"status": "unknown", "signature": "old"}})
        with pytest.raises(AuthorizationRequired) as captured:
            registry(store, context).execute(call(), operation_id="fresh")
        assert captured.value.code == "logical_task_mismatch"
        assert store.extension(context, "operations") == {
            "history": {"status": "unknown", "signature": "old"}}
        assert not store.extension(context, "action_receipts", {})
        controller.confirm(context, proposal.proposal_id)
        assert registry(store, context).execute(call(), operation_id="fresh")["ok"] is True
        assert len(TodoService(store).list_items(context)) == 1
        store.finish(context, second.task_id, status="completed")


def test_operation_id_reuse_with_changed_signature_is_rejected(tmp_path):
    path = tmp_path / "reuse.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        store = SessionStore(storage=storage)
        context = store.create(uuid4())
        controller = TaskController(store)
        approve(controller, context)
        task = store.begin(context)
        todo = registry(store, context)
        todo.execute(call("same"), operation_id="same")
        with pytest.raises(ValueError, match="不能修改参数"):
            todo.execute(ToolCall(id="same", name="todo_add",
                                  arguments={"title": "other", "due_date": None}),
                         operation_id="same")
        controller.propose(context, goal("another"))
        with pytest.raises(ValueError, match="跨确认实例"):
            todo.execute(call("same", title="synthetic"), operation_id="same")
        assert len(TodoService(store).list_items(context)) == 1
        store.finish(context, task.task_id, status="completed")


def test_normal_ui_confirmation_then_submit_writes_real_todo(tmp_path):
    model = ScriptedModel([ModelResponse(finish_reason="tool_calls", tool_calls=[call("ui")]),
                           ModelResponse(finish_reason="stop", content="已记录")])
    app = HarnessApplication(tmp_path / "ui.sqlite3", model=model, memory_mode="off")
    try:
        sid = app.create_session()["session_id"]
        context = app.context(sid)
        proposal = app.propose(context, {
            "objective": "记录合成待办", "constraints": [],
            "completion_criteria": ["记录一条待办"],
            "actions": [{"operation": "todo.add", "parameters": {"title": "synthetic", "due_date": None}}],
        })
        app.confirm(sid, proposal.proposal_id)
        app.set_mode(sid, "build")
        assert app.sessions.snapshot(context).task is None
        assert app.sessions.snapshot(context).work_state.grant.status == "pending_first_start"
        app.submit(sid, "记录")
        app.wait(sid)
        state = app.snapshot(sid)
        assert state["task"]["status"] == "completed"
        assert len(state["todos"]) == 1
        assert app.sessions.snapshot(context).work_state.grant.status == "closed"
    finally:
        app.close()


def test_legacy_confirmed_without_grant_is_readable_but_write_rejected_until_reconfirm():
    store = SessionStore()
    context = store.create(uuid4())
    controller = TaskController(store)
    proposal = controller.propose(context, goal())
    task = store.begin(context)
    legacy = WorkState(mode="build", status="ready", proposal=proposal,
                       confirmed_goal_id=proposal.proposal_id)
    store._sessions[context.session_id].work_state = legacy
    with pytest.raises(AuthorizationRequired) as captured:
        registry(store, context).execute(call())
    assert captured.value.code == "confirmation_grant_missing"
    assert controller.authorize(context, ActionSpec(operation="todo.list")).allowed is True
    controller.confirm(context, proposal.proposal_id)
    assert controller.snapshot(context).grant.status == "bound"
    assert registry(store, context).execute(call())["ok"] is True
    store.finish(context, task.task_id, status="completed")


def test_legacy_persisted_confirmation_without_grant_is_readable(tmp_path):
    path = tmp_path / "legacy-persisted.sqlite3"
    owner, sid = uuid4(), uuid4()
    context = ExecutionContext(owner_id=owner, session_id=sid)
    proposal = GoalProposal(goal=goal(), authorization_version=2)
    legacy = WorkState(mode="build", status="ready", proposal=proposal,
                       confirmed_goal_id=proposal.proposal_id)
    snapshot = SessionSnapshot(owner_id=owner, session_id=sid, work_state=legacy)
    with SQLiteStorage.create_new(path) as storage:
        storage._connection.execute(
            "INSERT INTO sessions (session_id, owner_id, snapshot_json) VALUES (?, ?, ?)",
            (str(sid), str(owner), snapshot.model_dump_json()))
    with SQLiteStorage.open(path) as storage:
        store = SessionStore(storage=storage)
        controller = TaskController(store)
        assert controller.authorize(context, ActionSpec(operation="todo.list")).allowed is True
        task = store.begin(context)
        with pytest.raises(AuthorizationRequired) as captured:
            registry(store, context).execute(call())
        assert captured.value.code == "confirmation_grant_missing"
        controller.confirm(context, proposal.proposal_id)
        assert controller.snapshot(context).grant.status == "bound"
        assert registry(store, context).execute(call())["ok"] is True
        store.finish(context, task.task_id, status="completed")


def test_pending_across_restart_binds_once(tmp_path):
    path = tmp_path / "restart-pending.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        store = SessionStore(storage=storage)
        context = store.create(uuid4())
        approve(TaskController(store), context)
    with SQLiteStorage.open(path) as storage:
        store = SessionStore(storage=storage)
        task = store.begin(context)
        assert store.snapshot(context).work_state.grant.status == "bound"
        store.finish(context, task.task_id, status="completed")
    with SQLiteStorage.open(path) as storage:
        store = SessionStore(storage=storage)
        assert store.snapshot(context).work_state.grant.status == "closed"
        second = store.begin(context)
        with pytest.raises(AuthorizationRequired) as captured:
            registry(store, context).execute(call("after-restart"))
        assert captured.value.code == "logical_task_mismatch"
        store.finish(context, second.task_id, status="waiting_user")
        assert raw(path)["sessions"][0]["task"]["status"] == "waiting_user"


def test_checkpoint_resume_preserves_logical_and_budget(tmp_path):
    path = tmp_path / "checkpoint-grant.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        store = SessionStore(storage=storage)
        context = store.create(uuid4())
        controller = TaskController(store)
        approve(controller, context)
        budgets = SessionBudgets(store)
        budget_id = budgets.create(context, BudgetLimits(model_calls=4, tool_calls=2))
        ledger = budgets.open(context, budget_id)
        segment = ledger.start_segment(BudgetLimits(model_calls=2, tool_calls=1))
        note = json.dumps({"achievements": ["记录"], "pending": ["继续核对"],
                           "blockers": [], "next_steps": ["定向恢复"]}, ensure_ascii=False)
        model = ScriptedModel([
            ModelResponse(finish_reason="tool_calls", tool_calls=[call("checkpoint-write")]),
            ModelResponse(finish_reason="stop", content=note),
        ])
        result = Runtime(model, registry(store, context), budget=segment,
                         checkpoint_policy=CheckpointPolicy()).run_session(
            "记录并收尾", store=store, context=context)
        assert result.status == "partial" and result.checkpoint is not None
        first = store.snapshot(context)
        assert first.work_state.grant.status == "paused"
        assert store.snapshot(context).task.status == "partial"
        checkpoint_id = first.checkpoint.checkpoint.checkpoint_id
        new_segment = ledger.start_segment(BudgetLimits(model_calls=2, tool_calls=1))
        resumed = store.begin(context, checkpoint_id=checkpoint_id, budget=new_segment)
        assert resumed.logical_task_id == first.task.logical_task_id
        assert resumed.task_id != first.task.task_id
        assert store.snapshot(context).work_state.grant.status == "bound"
        assert store.snapshot(context).work_state.grant.budget_id == budget_id
        store.finish(context, resumed.task_id, status="completed")
        final = store.snapshot(context)
        assert final.work_state.grant.status == "closed"
        assert final.work_state.confirmed_goal_id is not None


def test_bound_grant_rejects_active_budget_change_and_preserves_db(tmp_path):
    path = tmp_path / "bound-budget.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        store = SessionStore(storage=storage)
        context = store.create(uuid4())
        controller = TaskController(store)
        approve(controller, context)
        task = store.begin(context)
        before = raw(path)
        with pytest.raises(Exception):
            SessionBudgets(store).create(context, BudgetLimits(model_calls=2, tool_calls=1))
        assert raw(path) == before
        assert store.snapshot(context).work_state.grant.status == "bound"
        store.finish(context, task.task_id, status="failed")


def test_existing_unknown_is_not_replayed_after_new_confirmation(tmp_path, monkeypatch):
    path = tmp_path / "unknown-confirm.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        store = SessionStore(storage=storage)
        context = store.create(uuid4())
        proposal = approve(TaskController(store), context)
        task = store.begin(context)
        request = call("unknown-existing")
        signature = json.dumps([request.name, request.arguments], sort_keys=True, ensure_ascii=False)
        store.set_extension(context, "operations", {"unknown-existing": {
            "signature": signature, "status": "unknown"}})
        controller = TaskController(store)
        controller.confirm(context, proposal.proposal_id)
        todo = registry(store, context)
        monkeypatch.setattr(todo, "_execute", lambda *args, **kwargs: pytest.fail("不应重放"))
        with pytest.raises(RuntimeError, match="核对"):
            todo.execute(request, operation_id="unknown-existing")
        assert store.extension(context, "operations")["unknown-existing"]["status"] == "unknown"
        store.finish(context, task.task_id, status="failed")


def test_cleanup_keeps_journal_when_recheck_fails(tmp_path, monkeypatch):
    path = tmp_path / "cleanup-cas.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        store = SessionStore(storage=storage)
        context = store.create(uuid4())
        controller = TaskController(store)
        approve(controller, context)
        first = store.begin(context)
        store.finish(context, first.task_id, status="completed")
        second = store.begin(context)
        original = store.assert_current
        calls = []
        def fault(context):
            calls.append(True)
            if len(calls) == 2:
                raise VersionConflict("synthetic recheck failure")
            return original(context)
        monkeypatch.setattr(store, "assert_current", fault)
        with pytest.raises(AuthorizationRequired):
            registry(store, context).execute(call(), operation_id="cleanup-fault")
        monkeypatch.setattr(store, "assert_current", original)
        journal = store.extension(context, "operations", {})
        assert journal["cleanup-fault"]["status"] == "unknown"
        assert not store.extension(context, "action_receipts", {})
        assert len(calls) == 2
        store.finish(context, second.task_id, status="failed")


def test_same_budget_new_task_does_not_transfer_paused_grant(tmp_path):
    path = tmp_path / "same-budget.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        store = SessionStore(storage=storage)
        context = store.create(uuid4())
        controller = TaskController(store)
        approve(controller, context)
        budgets = SessionBudgets(store)
        budget_id = budgets.create(context, BudgetLimits(model_calls=4, tool_calls=2))
        ledger = budgets.open(context, budget_id)
        first_segment = ledger.start_segment(BudgetLimits(model_calls=2, tool_calls=1))
        first = store.begin(context, budget=first_segment)
        store.finish(context, first.task_id, status="partial")
        paused = controller.snapshot(context).grant
        assert paused.status == "paused" and paused.budget_id == budget_id
        second_segment = ledger.start_segment(BudgetLimits(model_calls=2, tool_calls=1))
        second = store.begin(context, budget=second_segment)
        assert second.logical_task_id != first.logical_task_id
        with pytest.raises(AuthorizationRequired) as captured:
            registry(store, context).execute(call("same-budget"))
        assert captured.value.code == "logical_task_mismatch"
        store.finish(context, second.task_id, status="waiting_user")
        state = controller.snapshot(context)
        assert state.grant == paused
        assert state.grant.status == "paused"


def test_trusted_running_confirm_binds_current_attempt_and_allows_one_write():
    store = SessionStore()
    context = store.create(uuid4())
    controller = TaskController(store)
    proposal = approve(controller, context)
    task = store.begin(context)
    first = controller.snapshot(context).grant
    controller.confirm(context, proposal.proposal_id)
    second = controller.snapshot(context).grant
    assert first.status == "bound" and second.status == "bound"
    assert second.grant_id != first.grant_id
    assert second.attempt_id == task.task_id
    assert second.logical_task_id == task.logical_task_id
    assert registry(store, context).execute(call())["ok"] is True
    store.finish(context, task.task_id, status="completed")


def test_storage_rejects_snapshot_with_bound_grant_on_other_logical_task(tmp_path):
    path = tmp_path / "mismatched-bound.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        store = SessionStore(storage=storage)
        context = store.create(uuid4())
        controller = TaskController(store)
        approve(controller, context)
        task = store.begin(context)
        before = raw(path)
        loaded = storage.load(context)
        forged = loaded.snapshot.model_copy(deep=True)
        forged.task = forged.task.model_copy(update={"logical_task_id": uuid4()})
        with pytest.raises(Exception):
            storage.save(context, forged, loaded.todos, _session_commit=_SESSION_COMMIT)
        assert raw(path) == before
        store.finish(context, task.task_id, status="failed")


def test_admission_codes_are_exact_for_mismatched_attempt_confirmation_status_budget():
    def prepared():
        store = SessionStore()
        context = store.create(uuid4())
        controller = TaskController(store)
        approve(controller, context)
        task = store.begin(context)
        return store, context, task

    store, context, task = prepared()
    session = store._sessions[context.session_id]
    session.task = task.model_copy(update={"task_id": uuid4()})
    with pytest.raises(AuthorizationRequired) as captured:
        registry(store, context).execute(call("attempt"))
    assert captured.value.code == "attempt_mismatch"

    store, context, task = prepared()
    session = store._sessions[context.session_id]
    session.work_state = session.work_state.model_copy(update={"confirmed_goal_id": uuid4()})
    with pytest.raises(AuthorizationRequired) as captured:
        registry(store, context).execute(call("confirmation"))
    assert captured.value.code == "confirmation_mismatch"

    store, context, task = prepared()
    session = store._sessions[context.session_id]
    session.work_state = session.work_state.model_copy(update={
        "grant": session.work_state.grant.model_copy(update={"status": "paused"})})
    with pytest.raises(AuthorizationRequired) as captured:
        registry(store, context).execute(call("status"))
    assert captured.value.code == "confirmation_not_bound"

    store, context, task = prepared()
    session = store._sessions[context.session_id]
    session.active_budget_id = uuid4()
    with pytest.raises(AuthorizationRequired) as captured:
        registry(store, context).execute(call("budget"))
    assert captured.value.code == "budget_mismatch"
