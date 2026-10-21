from uuid import uuid4

import pytest

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from pydantic import ValidationError

from min_agent.sessions import ExecutionContext, SessionStore
from min_agent.task_state import ActionSpec, TaskController
import min_agent.task_state as task_state


def goal_for(*actions):
    return task_state.GoalSpec(
        objective="整理周报待办", constraints=("仅当前会话",),
        completion_criteria=("待办内容符合确认清单",), actions=actions,
    )


def setup_controller():
    sessions = SessionStore()
    context = sessions.create(uuid4())
    return sessions, context, TaskController(sessions)


def test_default_plan_allows_query_and_denies_write_without_changing_mode():
    sessions = SessionStore()
    context = sessions.create(uuid4())
    controller = TaskController(sessions)

    assert controller.authorize(context, ActionSpec(operation="todo.list")).allowed is True
    decision = controller.authorize(
        context, ActionSpec(operation="todo.add", parameters={"title": "写周报"}),
    )
    assert decision.allowed is False
    assert decision.code == "plan_read_only"
    assert controller.snapshot(context).mode == "plan"
    assert sessions.snapshot(context).messages == []


def test_build_selection_does_not_replace_goal_confirmation():
    _, context, controller = setup_controller()
    controller.select_mode(context, "build")
    state = controller.snapshot(context)
    assert state.mode == "build"
    assert state.status == "waiting_user"
    assert controller.authorize(context, ActionSpec(operation="todo.list")).allowed is True
    decision = controller.authorize(context, ActionSpec(operation="todo.add", parameters={"title": "周报"}))
    assert decision.allowed is False
    assert decision.code == "goal_unconfirmed"


def test_confirmation_in_plan_does_not_automatically_upgrade_to_build():
    _, context, controller = setup_controller()
    action = ActionSpec(operation="todo.add", parameters={"title": "周报"})
    proposal = controller.propose(context, goal_for(action))
    assert controller.snapshot(context).status == "waiting_user"
    confirmed = controller.confirm(context, proposal.proposal_id)
    assert confirmed.mode == "plan"
    assert confirmed.status == "ready"
    assert confirmed.confirmed_goal_id == proposal.proposal_id
    assert controller.authorize(context, action).code == "plan_read_only"
    controller.select_mode(context, "build")
    assert controller.authorize(context, action).allowed is True


@pytest.mark.parametrize("operation", ["todo.add", "todo.update", "todo.complete", "todo.delete"])
def test_plan_blocks_each_write_even_with_matching_confirmed_scope(operation):
    _, context, controller = setup_controller()
    action = ActionSpec(operation=operation, parameters={"item_id": str(uuid4())})
    proposal = controller.propose(context, goal_for(action))
    controller.confirm(context, proposal.proposal_id)
    assert controller.authorize(context, action).code == "plan_read_only"
    assert controller.snapshot(context).mode == "plan"


@pytest.mark.parametrize("change", ["operation", "resource", "field", "extra_field"])
def test_confirmed_scope_does_not_allow_changed_operation_resource_or_payload(change):
    _, context, controller = setup_controller()
    parameters = {"item_id": str(uuid4()), "title": "已确认", "due_date": "2026-10-21"}
    action = ActionSpec(operation="todo.update", parameters=parameters)
    controller.select_mode(context, "build")
    proposal = controller.propose(context, goal_for(action))
    controller.confirm(context, proposal.proposal_id)
    changed_parameters = parameters.copy()
    operation = "todo.update"
    if change == "operation":
        operation = "todo.delete"
    elif change == "resource":
        changed_parameters["item_id"] = str(uuid4())
    elif change == "field":
        changed_parameters["due_date"] = "2026-10-22"
    else:
        changed_parameters["deleted"] = True
    changed = ActionSpec(operation=operation, parameters=changed_parameters)
    assert controller.authorize(context, changed).code == "scope_not_confirmed"
    assert controller.snapshot(context).proposal == proposal


@pytest.mark.parametrize("confirmed,requested", [(True, 1), (1, True), (1, 1.0), (None, "null")])
def test_json_type_differences_do_not_match_as_equal_scope(confirmed, requested):
    _, context, controller = setup_controller()
    action = ActionSpec(operation="todo.update", parameters={"value": confirmed})
    proposal = controller.propose(context, goal_for(action))
    controller.confirm(context, proposal.proposal_id)
    controller.select_mode(context, "build")
    assert controller.authorize(
        context, ActionSpec(operation="todo.update", parameters={"value": requested}),
    ).code == "scope_not_confirmed"


def test_parameter_key_order_does_not_change_matching_scope():
    _, context, controller = setup_controller()
    original = ActionSpec(operation="todo.update", parameters={"a": {"x": 1, "y": 2}, "b": "值"})
    reordered = ActionSpec(operation="todo.update", parameters={"b": "值", "a": {"y": 2, "x": 1}})
    proposal = controller.propose(context, goal_for(original))
    controller.confirm(context, proposal.proposal_id)
    controller.select_mode(context, "build")
    assert controller.authorize(context, reordered).allowed is True


def test_mode_switch_cannot_clear_scope_waiting_or_implicitly_expand_scope():
    _, context, controller = setup_controller()
    allowed = ActionSpec(operation="todo.add", parameters={"title": "原范围"})
    expanded = ActionSpec(operation="todo.add", parameters={"title": "越界"})
    proposal = controller.propose(context, goal_for(allowed))
    controller.confirm(context, proposal.proposal_id)
    controller.select_mode(context, "build")
    assert controller.authorize(context, expanded).code == "scope_not_confirmed"
    controller.select_mode(context, "plan")
    controller.select_mode(context, "build")
    assert controller.authorize(context, allowed).code == "waiting_user"
    # 定向确认原范围可恢复原计划，并未放行扩大请求。
    controller.confirm(context, proposal.proposal_id)
    assert controller.authorize(context, allowed).allowed is True
    assert controller.authorize(context, expanded).allowed is False


def test_new_proposal_even_with_same_content_requires_new_confirmation():
    _, context, controller = setup_controller()
    goal = goal_for(ActionSpec(operation="todo.add", parameters={"title": "周报"}))
    first = controller.propose(context, goal)
    controller.confirm(context, first.proposal_id)
    second = controller.propose(context, goal)
    assert first.proposal_id != second.proposal_id
    assert controller.snapshot(context).status == "waiting_user"
    with pytest.raises(PermissionError):
        controller.confirm(context, first.proposal_id)
    assert controller.snapshot(context).confirmed_goal_id is None


def test_foreign_missing_and_cross_session_confirmation_cannot_change_state():
    sessions, first, controller = setup_controller()
    second = sessions.create(first.owner_id)
    third = sessions.create(uuid4())
    action = ActionSpec(operation="todo.add", parameters={"title": "私有"})
    proposal = controller.propose(first, goal_for(action))
    foreign = ExecutionContext(owner_id=third.owner_id, session_id=first.session_id)
    missing = ExecutionContext(owner_id=first.owner_id, session_id=uuid4())
    before = controller.snapshot(first)
    for context in (foreign, missing):
        for invoke in (
            lambda: controller.snapshot(context),
            lambda: controller.select_mode(context, "build"),
            lambda: controller.propose(context, goal_for(action)),
            lambda: controller.confirm(context, proposal.proposal_id),
            lambda: controller.authorize(context, ActionSpec(operation="todo.list")),
        ):
            with pytest.raises(PermissionError):
                invoke()
    with pytest.raises(PermissionError):
        controller.confirm(second, proposal.proposal_id)
    assert controller.snapshot(first) == before
    assert controller.snapshot(second).proposal is None
    assert controller.snapshot(third).proposal is None


def test_goal_input_and_returned_nested_payloads_cannot_mutate_confirmed_state():
    _, context, controller = setup_controller()
    action = ActionSpec(operation="todo.update", parameters={"nested": {"values": ["原值"]}})
    goal = goal_for(action)
    proposal = controller.propose(context, goal)
    expected = controller.snapshot(context)
    goal.actions[0].parameters["nested"]["values"].append("输入篡改")
    proposal.goal.actions[0].parameters.clear()
    returned = controller.snapshot(context)
    returned.proposal.goal.actions[0].parameters.clear()
    assert controller.snapshot(context) == expected
    confirmed = controller.confirm(context, expected.proposal.proposal_id)
    confirmed.proposal.goal.actions[0].parameters.clear()
    assert controller.snapshot(context).proposal == expected.proposal


def test_run_busy_state_and_chat_messages_do_not_confirm_work_or_change_mode():
    from min_agent.models import Message

    sessions, context, controller = setup_controller()
    controller.select_mode(context, "build")
    action = ActionSpec(operation="todo.add", parameters={"title": "周报"})
    proposal = controller.propose(context, goal_for(action))
    task = sessions.begin(context)
    sessions.append(context, task.task_id, [Message(role="user", content="同意")])
    assert controller.snapshot(context).status == "waiting_user"
    assert controller.snapshot(context).confirmed_goal_id is None
    assert controller.authorize(context, action).code == "goal_unconfirmed"
    controller.confirm(context, proposal.proposal_id)
    assert sessions.snapshot(context).task.status == "running"
    sessions.finish(context, task.task_id, status="completed")
    assert controller.snapshot(context).mode == "build"
    assert controller.snapshot(context).confirmed_goal_id == proposal.proposal_id


def test_unknown_or_invalid_confirmation_leaves_state_unchanged():
    _, context, controller = setup_controller()
    before = controller.snapshot(context)
    with pytest.raises(PermissionError):
        controller.confirm(context, uuid4())
    with pytest.raises(TypeError):
        controller.confirm(context, str(uuid4()))
    with pytest.raises(ValidationError):
        controller.select_mode(context, "BUILD")
    with pytest.raises(TypeError):
        controller.propose(context, {"objective": "伪造"})
    with pytest.raises(TypeError):
        controller.authorize(context, {"operation": "todo.list"})
    assert controller.snapshot(context) == before


@pytest.mark.parametrize("payload", [
    {"operation": "shell", "parameters": {}},
    {"operation": "todo.list", "parameters": {"operation": "todo.delete"}},
    {"operation": "todo.add", "mode": "build"},
    {"operation": "todo.add", "owner_id": "forged"},
    {"operation": "todo.add", "parameters": {"value": float("nan")}},
    {"operation": "todo.add", "parameters": {"value": float("inf")}},
])
def test_unknown_operation_fake_authority_and_non_json_values_are_rejected(payload):
    with pytest.raises(ValidationError):
        ActionSpec(**payload)


@pytest.mark.parametrize("changes", [
    {"objective": "   "}, {"completion_criteria": ()},
    {"constraints": (" ",)}, {"completion_criteria": ("",)},
    {"confirmed": True},
])
def test_goal_requires_clear_objective_and_completion_criteria_without_self_approval(changes):
    data = {"objective": "写周报", "completion_criteria": ("记录存在",)}
    with pytest.raises(ValidationError):
        task_state.GoalSpec(**{**data, **changes})


def test_forged_model_instances_are_revalidated_before_publishing():
    _, context, controller = setup_controller()
    before = controller.snapshot(context)
    fake_action = ActionSpec.model_construct(operation="todo.list", parameters={"write": True})
    with pytest.raises(ValidationError):
        controller.authorize(context, fake_action)
    fake_goal = task_state.GoalSpec.model_construct(
        objective="", constraints=(), completion_criteria=("有效",), actions=(),
    )
    with pytest.raises(ValidationError):
        controller.propose(context, fake_goal)
    assert controller.snapshot(context) == before


def test_concurrent_new_proposals_publish_one_version_and_reject_stale_confirmation():
    _, context, controller = setup_controller()
    barrier = Barrier(2)

    def propose(title):
        barrier.wait(timeout=5)
        return controller.propose(context, goal_for(ActionSpec(operation="todo.add", parameters={"title": title})))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(propose, title) for title in ("A", "B")]
        proposals = [future.result(timeout=10) for future in futures]
    current = controller.snapshot(context).proposal
    stale = next(proposal for proposal in proposals if proposal.proposal_id != current.proposal_id)
    with pytest.raises(PermissionError):
        controller.confirm(context, stale.proposal_id)
    assert controller.snapshot(context).confirmed_goal_id is None
    controller.confirm(context, current.proposal_id)
    assert controller.snapshot(context).confirmed_goal_id == current.proposal_id


def test_confirmed_build_scope_allows_normal_steps_without_reconfirming():
    _, context, controller = setup_controller()
    action = ActionSpec(operation="todo.update", parameters={"item_id": str(uuid4()), "title": "周报"})
    controller.select_mode(context, "build")
    proposal = controller.propose(context, goal_for(action))
    assert controller.authorize(context, action).allowed is False
    controller.confirm(context, proposal.proposal_id)
    for _ in range(2):
        assert controller.authorize(context, action).allowed is True
        assert controller.snapshot(context).status == "ready"


def test_scope_expansion_waits_for_new_matching_confirmation_before_resuming():
    _, context, controller = setup_controller()
    first = ActionSpec(operation="todo.add", parameters={"title": "周报"})
    expanded = ActionSpec(operation="todo.add", parameters={"title": "另一个目标"})
    controller.select_mode(context, "build")
    old = controller.propose(context, goal_for(first))
    controller.confirm(context, old.proposal_id)
    decision = controller.authorize(context, expanded)
    assert decision.allowed is False
    assert decision.code == "scope_not_confirmed"
    assert controller.snapshot(context).status == "waiting_user"
    assert controller.snapshot(context).confirmed_goal_id == old.proposal_id
    assert controller.snapshot(context).proposal.goal.actions == (first,)
    assert controller.authorize(context, first).code == "waiting_user"
    assert controller.authorize(context, ActionSpec(operation="todo.list")).allowed is True

    new = controller.propose(context, goal_for(first, expanded))
    assert new.proposal_id != old.proposal_id
    assert controller.snapshot(context).confirmed_goal_id is None
    with pytest.raises(PermissionError):
        controller.confirm(context, old.proposal_id)
    assert controller.authorize(context, expanded).allowed is False
    controller.confirm(context, new.proposal_id)
    assert controller.authorize(context, expanded).allowed is True


def test_returning_to_plan_blocks_confirmed_writes_until_explicit_build_selection():
    _, context, controller = setup_controller()
    action = ActionSpec(operation="todo.delete", parameters={"item_id": str(uuid4())})
    proposal = controller.propose(context, goal_for(action))
    controller.confirm(context, proposal.proposal_id)
    controller.select_mode(context, "build")
    assert controller.authorize(context, action).allowed is True
    controller.select_mode(context, "plan")
    assert controller.authorize(context, action).allowed is False
    assert controller.snapshot(context).confirmed_goal_id == proposal.proposal_id
    controller.select_mode(context, "build")
    assert controller.authorize(context, action).allowed is True
