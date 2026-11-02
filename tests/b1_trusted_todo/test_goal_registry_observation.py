"""B1：目标冻结、SQLite 观察、授权/所有权/重启边界。"""
import sqlite3
from uuid import UUID, uuid4

import pytest

from min_agent.models import ToolCall
from min_agent.public_feedback import PublicGoal
from min_agent.public_goal_adapter import PublicGoalAdapter
from min_agent.public_goal_registry import PublicGoalRegistry
from min_agent.sessions import SessionStore
from min_agent.storage import SQLiteStorage
from min_agent.task_state import ActionSpec, GoalSpec, TaskController
from min_agent.todo_observation import TodoObserver
from min_agent.tools.todo import TodoService
from min_agent.tools.todo_tools import todo_registry


def goal(expected=1, identity="goal-001", source="explicit-user-goal"):
    return PublicGoal(public_goal_id=identity, kind="TODO_COUNT",
                      source_type="USER_REQUEST", source_ref=source,
                      expected_count=expected)


def spec_goal(title="第一项"):
    return GoalSpec(objective="记录合成待办", completion_criteria=("记录一条",),
                    actions=(ActionSpec(operation="todo.add",
                                        parameters={"title": title, "due_date": None}),))


def setup(tmp_path):
    path = tmp_path / "b1.sqlite3"
    storage = SQLiteStorage.create_new(path)
    store = SessionStore(storage=storage)
    context = store.create(uuid4())
    controller = TaskController(store)
    proposal = controller.propose(context, spec_goal())
    controller.confirm(context, proposal.proposal_id)
    controller.select_mode(context, "build")
    registry = PublicGoalRegistry(store)
    registry.register(context, proposal.proposal_id, [goal()])
    task = store.begin(context)
    binding = registry.freeze(context, task)
    return path, storage, store, context, controller, registry, task, binding


def write_todo(store, context, controller, identity="op-1"):
    todo = todo_registry(TodoService(store), controller, context)
    return todo.execute(ToolCall(id=identity, name="todo_add",
                                arguments={"title": "第一项", "due_date": None}),
                        operation_id=identity)


def test_sqlite_observation_met_and_not_met(tmp_path):
    root = tmp_path / "case"
    root.mkdir(exist_ok=True)
    path, storage, store, ctx, controller, registry, task, binding = setup(root)
    try:
        assert binding is not None
        write_todo(store, ctx, controller)
        store.finish(ctx, task.task_id, status="completed")
        result = TodoObserver(storage).observe(ctx, binding)
        assert result.status == "MET", result
        assert result.goals[0].observed_count == 1
    finally:
        storage.close()

    (root / "not-met").mkdir(exist_ok=True)
    path, storage, store, ctx, controller, registry, task, binding = setup(root / "not-met")
    try:
        store.finish(ctx, task.task_id, status="completed")
        result = TodoObserver(storage).observe(ctx, binding)
        assert result.status == "NOT_MET"
    finally:
        storage.close()


def test_running_is_unknown_and_read_failure_is_withheld(tmp_path):
    root = tmp_path / "running"
    root.mkdir(exist_ok=True)
    path, storage, store, ctx, controller, registry, task, binding = setup(root)
    try:
        result = TodoObserver(storage).observe(ctx, binding)
        assert result.status == "UNKNOWN"
        storage.close()
        failed = TodoObserver(storage).observe(ctx, binding)
        assert failed.status == "WITHHELD"
    finally:
        if store._storage is not None:
            pass


def test_initial_and_deleted_records_do_not_count(tmp_path):
    root = tmp_path / "records"
    root.mkdir(exist_ok=True)
    path, storage, store, ctx, controller, registry, task, binding = setup(root)
    try:
        old = TodoService(store).add(ctx, "旧任务记录")
        write_todo(store, ctx, controller)
        target = TodoService(store).list_items(ctx)[-1]
        TodoService(store).delete(ctx, target.id)
        store.finish(ctx, task.task_id, status="completed")
        result = TodoObserver(storage).observe(ctx, binding)
        assert result.status == "NOT_MET"
        assert result.goals[0].observed_count == 0
    finally:
        storage.close()


def test_registration_idempotent_conflict_and_post_execution_rejected(tmp_path):
    storage = SQLiteStorage.create_new(tmp_path / "registry.sqlite3")
    store = SessionStore(storage=storage)
    ctx = store.create(uuid4())
    controller = TaskController(store)
    proposal = controller.propose(ctx, spec_goal())
    controller.confirm(ctx, proposal.proposal_id)
    controller.select_mode(ctx, "build")
    registry = PublicGoalRegistry(store)
    registry.register(ctx, proposal.proposal_id, [goal(1)])
    registry.register(ctx, proposal.proposal_id, [goal(1)])
    with pytest.raises(PermissionError):
        registry.register(ctx, proposal.proposal_id, [goal(2)])
    task = store.begin(ctx)
    first = registry.freeze(ctx, task)
    again = registry.freeze(ctx, task)
    assert again is not None and again.binding_id == first.binding_id
    store.finish(ctx, task.task_id, status="completed")
    registry.register(ctx, proposal.proposal_id, [goal(1)])
    with pytest.raises(PermissionError):
        registry.freeze(ctx, task)
    storage.close()


def test_resume_gets_new_binding_not_old_attempt(tmp_path):
    root = tmp_path / "resume"
    root.mkdir()
    path, storage, store, ctx, controller, registry, task, first = setup(root)
    try:
        write_todo(store, ctx, controller)
        store.finish(ctx, task.task_id, status="waiting_user")
        registry.register(ctx, first.proposal_id, [goal(1)])
        resumed = store.begin(ctx, resume_task_id=task.task_id)
        second = registry.freeze(ctx, resumed)
        assert second.binding_id != first.binding_id
        assert second.attempt_id == str(resumed.task_id)
        assert second.logical_task_id == first.logical_task_id
        store.finish(ctx, resumed.task_id, status="completed")
        result = TodoObserver(storage).observe(ctx, second)
        assert result.status == "MET"
    finally:
        storage.close()


def test_restart_reloads_binding_and_observation(tmp_path):
    root = tmp_path / "restart"
    root.mkdir()
    path, storage, store, ctx, controller, registry, task, binding = setup(root)
    try:
        write_todo(store, ctx, controller)
        store.finish(ctx, task.task_id, status="completed")
    finally:
        storage.close()
    with SQLiteStorage.open(path) as reopened:
        store2 = SessionStore(storage=reopened)
        registry2 = PublicGoalRegistry(store2)
        binding2 = registry2.current_binding(ctx)
        assert binding2 is not None and binding2.binding_id == binding.binding_id
        result = TodoObserver(reopened).observe(ctx, binding2)
        assert result.status == "MET"


def test_two_stores_stale_extension_write_conflicts(tmp_path):
    storage = SQLiteStorage.create_new(tmp_path / "stale.sqlite3")
    first = SessionStore(storage=storage)
    ctx = first.create(uuid4())
    second = SessionStore(storage=storage)
    # second 装载了当前快照；first 先提交后，second 的旧 revision 不能覆盖。
    first.set_extension(ctx, "stale_probe", 1)
    from min_agent.storage import VersionConflict
    with pytest.raises(VersionConflict):
        second.set_extension(ctx, "stale_probe", 2)
    storage.close()


def test_other_owner_and_old_task_records_are_not_counted(tmp_path):
    root = tmp_path / "cross"
    root.mkdir()
    path, storage, store, ctx, controller, registry, task, binding = setup(root)
    try:
        other = store.create(uuid4())
        assert TodoObserver(storage).observe(other, binding).status == "WITHHELD"
        store.finish(ctx, task.task_id, status="completed")
        second_goal = registry.register(ctx, binding.proposal_id, [goal(1)])
        second = store.begin(ctx)
        controller.confirm(ctx, UUID(binding.proposal_id))
        second_binding = registry.freeze(ctx, second)
        assert second_goal is not None
        write_todo(store, ctx, controller, identity="second-op")
        store.finish(ctx, second.task_id, status="completed")
        result = TodoObserver(storage).observe(ctx, second_binding)
        assert result.status == "MET" and result.goals[0].observed_count == 1
    finally:
        storage.close()


def test_late_freeze_is_rejected_without_rewriting_state(tmp_path):
    root = tmp_path / "late"
    root.mkdir()
    storage = SQLiteStorage.create_new(root / "late.sqlite3")
    store = SessionStore(storage=storage)
    ctx = store.create(uuid4())
    controller = TaskController(store)
    proposal = controller.propose(ctx, spec_goal())
    controller.confirm(ctx, proposal.proposal_id)
    controller.select_mode(ctx, "build")
    registry = PublicGoalRegistry(store)
    registry.register(ctx, proposal.proposal_id, [goal(1)])
    task = store.begin(ctx)
    write_todo(store, ctx, controller, identity="first")
    before = storage.load(ctx).model_dump(mode="json")
    with pytest.raises(PermissionError, match="执行"):
        registry.freeze(ctx, task)
    after = storage.load(ctx).model_dump(mode="json")
    assert after == before
    store.finish(ctx, task.task_id, status="completed")
    storage.close()


@pytest.mark.parametrize("goals,reason", [
    ([PublicGoal(public_goal_id="goal-001", kind="RESEARCH_EVIDENCE",
                 source_type="USER_REQUEST", source_ref="r")],
     "unsupported_public_goal_kind"),
    ([goal(1), goal(2, "goal-002", "other-source")], "unsupported_public_goal_set"),
    ([goal(1), goal(1, "goal-002", "explicit-user-goal")],
     "duplicate_public_goal_predicate"),
])
def test_unsupported_and_duplicate_sets_are_rejected_before_state(tmp_path, goals, reason):
    storage = SQLiteStorage.create_new(tmp_path / "reject.sqlite3")
    store = SessionStore(storage=storage)
    ctx = store.create(uuid4())
    controller = TaskController(store)
    proposal = controller.propose(ctx, spec_goal())
    controller.confirm(ctx, proposal.proposal_id)
    registry = PublicGoalRegistry(store)
    before = storage.load(ctx).model_dump(mode="json")
    with pytest.raises(ValueError, match=reason):
        registry.register(ctx, proposal.proposal_id, goals)
    assert storage.load(ctx).model_dump(mode="json") == before
    storage.close()


def test_revocation_changes_feedback_to_withheld_and_unknown_score_interval(tmp_path):
    root = tmp_path / "withheld"
    root.mkdir()
    path, storage, store, ctx, controller, registry, task, binding = setup(root)
    try:
        adapter = PublicGoalAdapter(registry, TodoObserver(storage))
        running = adapter.score(ctx)
        assert running.score_status == "AVAILABLE"
        assert running.progress.lower_numerator == 0 and running.progress.upper_numerator == 100
        controller.propose(ctx, spec_goal("新提案"))
        feedback = adapter.feedback(ctx)
        assert feedback.feedback_status == "WITHHELD"
        assert feedback.goal_progress is None
        score = adapter.score(ctx)
        assert score.score_status == "WITHHELD" and score.progress is None
    finally:
        store.finish(ctx, task.task_id, status="failed")
        storage.close()
