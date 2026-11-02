"""R3 生命周期映射：当前 attempt 严格映射，历史 grant 完整保留。"""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from min_agent.sessions import SessionStore
from min_agent.task_state import GoalSpec, TaskController


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("audit_state_bar_r3_lifecycle", ROOT / "scripts" / "audit_state_bar.py")
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


def goal():
    return GoalSpec(objective="synthetic", completion_criteria=("synthetic",), actions=())


def states():
    store = SessionStore()
    context = store.create(uuid4())
    controller = TaskController(store)
    proposal = controller.propose(context, goal())
    controller.confirm(context, proposal.proposal_id)
    controller.select_mode(context, "build")
    original = controller.snapshot(context)
    task = store.begin(context)
    return store, context, controller, original, task


@pytest.mark.parametrize("task_status", ["running", "completed", "waiting_user", "partial", "failed", "cancelled"])
@pytest.mark.parametrize("grant_status", ["bound", "paused", "closed"])
def test_current_attempt_exact_mapping(task_status, grant_status):
    store, context, controller, original, task = states()
    if task_status != "running":
        store.finish(context, task.task_id, status=task_status)
    snapshot = store.snapshot(context)
    current = snapshot.work_state.model_copy(
        update={"grant": snapshot.work_state.grant.model_copy(update={"status": grant_status})})
    expected = {
        "running": "bound", "completed": "closed",
        "waiting_user": "paused", "partial": "paused",
        "failed": "paused", "cancelled": "paused",
    }[task_status]
    assert PROBE.grant_lifecycle_valid(
        original, current, snapshot.task, snapshot.active_budget_id) is (grant_status == expected)


def test_historical_grant_must_be_identical_forged_attempt_is_rejected():
    store, context, controller, original, task = states()
    first = task
    store.finish(context, first.task_id, status="completed")
    original = controller.snapshot(context)
    second = store.begin(context)
    store.finish(context, second.task_id, status="completed")
    snapshot = store.snapshot(context)
    assert PROBE.grant_lifecycle_valid(original, snapshot.work_state, snapshot.task,
                                       snapshot.active_budget_id) is True
    forged = snapshot.work_state.model_copy(update={
        "grant": snapshot.work_state.grant.model_copy(update={"attempt_id": uuid4()})})
    assert PROBE.grant_lifecycle_valid(original, forged, snapshot.task, snapshot.active_budget_id) is False


def test_current_completed_budget_must_match_trusted_active_budget():
    store, context, controller, original, task = states()
    store.finish(context, task.task_id, status="completed")
    snapshot = store.snapshot(context)
    forged = snapshot.work_state.model_copy(update={
        "grant": snapshot.work_state.grant.model_copy(update={"budget_id": uuid4()})})
    assert PROBE.grant_lifecycle_valid(original, forged, snapshot.task, None) is False
    assert PROBE.grant_lifecycle_valid(original, snapshot.work_state, snapshot.task, None) is True
    budget = uuid4()
    matching = snapshot.work_state.model_copy(update={
        "grant": snapshot.work_state.grant.model_copy(update={"budget_id": budget})})
    assert PROBE.grant_lifecycle_valid(original, matching, snapshot.task, budget) is True
    assert PROBE.grant_lifecycle_valid(original, matching, snapshot.task, None) is False


def test_identity_revision_logical_budget_and_closed_reopen_are_rejected():
    store, context, controller, original, task = states()
    snapshot = store.snapshot(context)
    grant = snapshot.work_state.grant
    for field, value in (("grant_id", uuid4()), ("issued_revision", grant.issued_revision + 1),
                         ("logical_task_id", uuid4()), ("budget_id", uuid4())):
        changed = snapshot.work_state.model_copy(update={
            "grant": grant.model_copy(update={field: value})})
        assert PROBE.grant_lifecycle_valid(original, changed, snapshot.task,
                                           snapshot.active_budget_id) is False
    store.finish(context, task.task_id, status="completed")
    closed = store.snapshot(context)
    reopened = closed.work_state.model_copy(update={
        "grant": closed.work_state.grant.model_copy(update={"status": "paused"})})
    assert PROBE.grant_lifecycle_valid(closed.work_state, reopened, closed.task,
                                       closed.active_budget_id) is False


def test_unknown_task_status_is_rejected_not_defaulted_to_paused():
    store, context, controller, original, task = states()
    store.finish(context, task.task_id, status="completed")
    snapshot = store.snapshot(context)
    future_task = SimpleNamespace(task_id=snapshot.task.task_id,
                                  logical_task_id=snapshot.task.logical_task_id,
                                  status="future_status")
    assert PROBE.grant_lifecycle_valid(original, snapshot.work_state, future_task,
                                       snapshot.active_budget_id) is False
