from uuid import uuid4

import pytest

from min_agent.memory_state import SessionStateTable
from min_agent.models import ToolCall
from min_agent.sessions import SessionStore
from min_agent.task_state import ActionSpec, GoalSpec, TaskController
from min_agent.tools.todo import TodoService
from min_agent.tools.todo_tools import todo_registry


def approve(controller, context, action):
    proposal = controller.propose(context, GoalSpec(objective="整理周报",
        constraints=("不删除原始材料",), completion_criteria=("日期符合用户确认",), actions=(action,)))
    controller.select_mode(context, "build")
    controller.confirm(context, proposal.proposal_id)


def test_state_reads_latest_business_value_and_consumed_action_without_rebuilding_from_chat():
    store = SessionStore(); context = store.create(uuid4()); todos = TodoService(store)
    item = todos.add(context, "周报", due_date="2026-10-22")
    controller = TaskController(store)
    approve(controller, context, ActionSpec(operation="todo.update",
        parameters={"item_id": str(item.id), "due_date": "2026-10-23"}))
    table = SessionStateTable(store, context, todos)
    old = table.read()
    result = todo_registry(todos, controller, context).execute(ToolCall(id="change",
        name="todo_update", arguments={"item_id": str(item.id), "patch": {"due_date": "2026-10-23"}}))
    assert result["ok"]
    current = table.read()
    assert old["todos"][0]["item"]["due_date"] == "2026-10-22"
    assert current["todos"][0]["item"]["due_date"] == "2026-10-23"
    assert current["work_state"]["proposal"]["goal"]["constraints"] == ["不删除原始材料"]
    assert current["committed_actions"][0]["operation"] == "todo.update"
    assert current["committed_actions"][0]["resource_id"] == str(item.id)
    assert current["source_kind"] == "runtime_state"


def test_same_owner_sessions_have_independent_state_and_deleted_todo_is_not_current():
    store = SessionStore(); first = store.create(uuid4()); second = store.create(first.owner_id)
    todos = TodoService(store)
    item = todos.add(first, "仅窗口一"); todos.add(second, "仅窗口二")
    a, b = SessionStateTable(store, first, todos), SessionStateTable(store, second, todos)
    assert [row["item"]["title"] for row in a.read()["todos"]] == ["仅窗口一"]
    assert [row["item"]["title"] for row in b.read()["todos"]] == ["仅窗口二"]
    todos.delete(first, item.id)
    assert a.read()["todos"] == []
    assert len(b.read()["todos"]) == 1


def test_state_rejects_foreign_owner_and_mismatched_service():
    from min_agent.sessions import ExecutionContext
    store = SessionStore(); context = store.create(uuid4())
    with pytest.raises(PermissionError):
        SessionStateTable(store, ExecutionContext(owner_id=uuid4(), session_id=context.session_id), TodoService(store))
    with pytest.raises(ValueError):
        SessionStateTable(store, context, TodoService(SessionStore()))


def test_mutating_returned_state_does_not_change_business_state_or_confirm_a_goal():
    store = SessionStore(); context = store.create(uuid4()); todos = TodoService(store)
    todos.add(context, "原始标题")
    table = SessionStateTable(store, context, todos)
    copy = table.read()
    copy["todos"][0]["item"]["title"] = "伪造标题"
    copy["work_state"]["mode"] = "build"
    assert table.read()["todos"][0]["item"]["title"] == "原始标题"
    assert table.read()["work_state"]["mode"] == "plan"
