from uuid import uuid4
import json
import sqlite3
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from min_agent.models import ModelResponse, ToolCall
from min_agent.clock import Clock
from min_agent.runtime import Runtime
from min_agent.sessions import SessionStore
from min_agent.storage import SQLiteStorage
from min_agent.task_state import ActionSpec, GoalSpec, TaskController
from min_agent.tools.todo import TodoService
from tests.fakes.model import ScriptedModel


def goal_for(*actions):
    return GoalSpec(objective="记录周报", completion_criteria=("记录符合确认内容",), actions=actions)


def registry_for(todos, controller, context):
    from min_agent.tools.todo_tools import todo_registry

    return todo_registry(todos, controller, context)


def approve(controller, context, *actions):
    proposal = controller.propose(context, goal_for(*actions))
    controller.confirm(context, proposal.proposal_id)
    controller.select_mode(context, "build")
    return proposal


def execute(registry, name, **arguments):
    result = registry.execute(ToolCall(id=str(uuid4()), name=name, arguments=arguments))
    assert result["ok"] is True
    return json.loads(result["result"])


def test_controllers_share_one_authoritative_work_state_for_session():
    sessions = SessionStore()
    context = sessions.create(uuid4())
    first, second = TaskController(sessions), TaskController(sessions)
    proposal = first.propose(context, goal_for())
    assert second.snapshot(context).proposal == proposal
    second.confirm(context, proposal.proposal_id)
    assert first.snapshot(context) == second.snapshot(context)


def test_confirmed_work_state_is_saved_and_restored_with_session(tmp_path):
    path = tmp_path / "work.sqlite3"
    action = ActionSpec(operation="todo.add", parameters={"title": "周报", "due_date": None})
    with SQLiteStorage.create_new(path) as storage:
        sessions = SessionStore(storage=storage)
        context = sessions.create(uuid4())
        controller = TaskController(sessions)
        proposal = controller.propose(context, goal_for(action))
        controller.confirm(context, proposal.proposal_id)
        controller.select_mode(context, "build")
        expected = controller.snapshot(context)

    with SQLiteStorage.open(path) as storage:
        restored = SessionStore(storage=storage)
        controller = TaskController(restored)
        assert controller.snapshot(context) == expected
        assert controller.authorize(context, action).allowed is True


def test_real_todo_entry_allows_plan_query_but_blocks_add_before_side_effect():
    sessions = SessionStore()
    context = sessions.create(uuid4())
    todos = TodoService(sessions)
    existing = todos.add(context, "已有")
    controller = TaskController(sessions)
    registry = registry_for(todos, controller, context)
    assert execute(registry, "todo_list")[0]["id"] == str(existing.id)
    with pytest.raises(PermissionError):
        execute(registry, "todo_add", title="不能写入")
    assert todos.list_items(context) == [existing]


def test_real_todo_entry_runs_confirmed_crud_and_preserves_omitted_patch_fields():
    sessions = SessionStore()
    context = sessions.create(uuid4())
    todos = TodoService(sessions)
    controller = TaskController(sessions)
    registry = registry_for(todos, controller, context)
    approve(controller, context, ActionSpec(operation="todo.add", parameters={"title": "周报", "due_date": "2026-10-21"}))
    added = execute(registry, "todo_add", title="周报", due_date="2026-10-21")
    item_id = added["id"]
    approve(controller, context,
        ActionSpec(operation="todo.update", parameters={"item_id": item_id, "title": "新标题"}),
        ActionSpec(operation="todo.update", parameters={"item_id": item_id, "due_date": None}),
        ActionSpec(operation="todo.complete", parameters={"item_id": item_id}),
        # 两次独立请求需两个动作实例；第二次仍验证业务的changed=false。
        ActionSpec(operation="todo.complete", parameters={"item_id": item_id}),
        ActionSpec(operation="todo.delete", parameters={"item_id": item_id}),
        ActionSpec(operation="todo.delete", parameters={"item_id": item_id}),
    )
    updated = execute(registry, "todo_update", item_id=item_id, patch={"title": "新标题"})
    assert updated["due_date"] == "2026-10-21"
    cleared = execute(registry, "todo_update", item_id=item_id, patch={"due_date": None})
    assert cleared["title"] == "新标题" and cleared["due_date"] is None
    assert execute(registry, "todo_complete", item_id=item_id)["changed"] is True
    assert execute(registry, "todo_complete", item_id=item_id)["changed"] is False
    assert execute(registry, "todo_delete", item_id=item_id)["changed"] is True
    assert execute(registry, "todo_delete", item_id=item_id)["changed"] is False
    assert todos.list_items(context) == []


def test_real_todo_entry_rejects_scope_expansion_without_changing_item():
    sessions = SessionStore()
    context = sessions.create(uuid4())
    todos = TodoService(sessions)
    item = todos.add(context, "原记录")
    controller = TaskController(sessions)
    registry = registry_for(todos, controller, context)
    approve(controller, context, ActionSpec(operation="todo.update", parameters={"item_id": str(item.id), "title": "仅这个值"}))
    with pytest.raises(PermissionError):
        execute(registry, "todo_update", item_id=str(item.id), patch={"title": "越界值"})
    assert todos.list_items(context) == [item]
    assert controller.snapshot(context).status == "waiting_user"


def test_runtime_waits_stops_remaining_calls_and_pairs_all_results_before_resume(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        sessions = SessionStore(storage=storage)
        context = sessions.create(uuid4())
        todos = TodoService(sessions)
        controller = TaskController(sessions)
        registry = registry_for(todos, controller, context)
        calls = [
            ToolCall(id="read", name="todo_list", arguments={}),
            ToolCall(id="denied", name="todo_add", arguments={"title": "周报"}),
            ToolCall(id="skipped", name="todo_add", arguments={"title": "后续不应执行"}),
        ]
        model = ScriptedModel([ModelResponse(finish_reason="tool_calls", tool_calls=calls)])
        runtime = Runtime(model, tools=registry)
        result = runtime.run_session("请求记录", store=sessions, context=context)
        assert result.status == "waiting_user" and result.error_code == "plan_read_only"
        assert len(model.calls) == 1
        assert todos.list_items(context) == []
        snapshot = sessions.snapshot(context)
        assert snapshot.task.status == "waiting_user"
        results = [message for message in snapshot.messages if message.role == "tool"]
        assert [message.tool_call_id for message in results] == [call.id for call in calls]
        assert json.loads(results[0].content)["ok"] is True
        assert all(json.loads(message.content)["error"]["execution_state"] == "not_started" for message in results[1:])
        assert runtime.trace.events[-1].status == "waiting_user"
        assert storage.load(context).snapshot == snapshot

    with SQLiteStorage.open(path) as storage:
        sessions = SessionStore(storage=storage)
        controller = TaskController(sessions)
        todos = TodoService(sessions)
        approve(controller, context, ActionSpec(operation="todo.add", parameters={"title": "周报", "due_date": None}))
        model = ScriptedModel([
            ModelResponse(finish_reason="tool_calls", tool_calls=[ToolCall(id="new", name="todo_add", arguments={"title": "周报"})]),
            ModelResponse(finish_reason="stop", content="已记录"),
        ])
        result = Runtime(model, tools=registry_for(todos, controller, context)).run_session("按确认继续", store=sessions, context=context)
        assert result.status == "completed"
        assert [item.title for item in todos.list_items(context)] == ["周报"]


def test_runtime_rejects_wrong_session_store_and_context_free_bound_tools_before_model():
    sessions = SessionStore()
    first, second = sessions.create(uuid4()), sessions.create(uuid4())
    registry = registry_for(TodoService(sessions), TaskController(sessions), first)
    model = ScriptedModel([ModelResponse(finish_reason="stop", content="不能调用")])
    runtime = Runtime(model, tools=registry)
    with pytest.raises(PermissionError):
        runtime.run_session("错误会话", store=sessions, context=second)
    with pytest.raises(PermissionError):
        runtime.run_session("错误存储", store=SessionStore(), context=first)
    with pytest.raises(PermissionError):
        runtime.run("缺少上下文")
    assert model.calls == []
    assert sessions.snapshot(first).task is sessions.snapshot(second).task is None


def test_bound_runtime_supplies_system_prompt_and_live_state_without_saving_projection():
    sessions = SessionStore()
    context = sessions.create(uuid4())
    controller = TaskController(sessions)
    model = ScriptedModel([ModelResponse(finish_reason="stop", content="直接回答")])
    result = Runtime(model, tools=registry_for(TodoService(sessions), controller, context)).run_session(
        "普通问题", store=sessions, context=context,
    )
    assert result.status == "completed"
    messages = model.calls[0]
    assert messages[0].role == "system" and "Plan" in messages[0].content
    assert messages[1].content == "普通问题" and messages[-1].role == "user"
    payload = json.loads(messages[-1].content)
    assert payload["application_state"]["mode"] == "plan"
    assert "agent_status" not in payload
    assert [message.content for message in sessions.snapshot(context).messages] == ["普通问题", "直接回答"]


@pytest.mark.parametrize("operation", ["select_mode", "propose", "confirm", "authorize"])
def test_work_state_sql_failure_preserves_previous_disk_and_memory(tmp_path, operation):
    path = tmp_path / "state-failure.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        sessions = SessionStore(storage=storage)
        context = sessions.create(uuid4())
        todos = TodoService(sessions)
        item = todos.add(context, "不能丢失")
        controller = TaskController(sessions)
        action = ActionSpec(operation="todo.add", parameters={"title": "已确认", "due_date": None})
        proposal = controller.propose(context, goal_for(action))
        if operation != "confirm":
            controller.confirm(context, proposal.proposal_id)
        if operation == "authorize":
            controller.select_mode(context, "build")
        before = sessions.snapshot(context)
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TRIGGER fail_state BEFORE UPDATE ON sessions BEGIN SELECT RAISE(ABORT, 'state failure'); END")
        with pytest.raises(sqlite3.IntegrityError, match="state failure"):
            if operation == "select_mode":
                controller.select_mode(context, "build")
            elif operation == "propose":
                controller.propose(context, goal_for())
            elif operation == "confirm":
                controller.confirm(context, proposal.proposal_id)
            else:
                controller.authorize(context, ActionSpec(operation="todo.delete", parameters={"item_id": str(item.id)}))
        assert sessions.snapshot(context) == storage.load(context).snapshot == before
        assert todos.list_items(context) == [item]
    with SQLiteStorage.open(path) as storage:
        assert SessionStore(storage=storage).snapshot(context) == before


@pytest.mark.parametrize("tool_name,arguments", [
    ("todo_add", {"title": "非法日期", "due_date": "2026-02-30"}),
    ("todo_add", {"title": "隐藏字段", "owner_id": "forged"}),
    ("todo_add", {"title": "隐藏字段", "mode": "build"}),
    ("todo_list", {"session_id": "forged"}),
    ("todo_update", {"item_id": "bad", "patch": {"title": "错ID"}}),
    ("todo_update", {"item_id": str(uuid4()), "patch": {}}),
    ("todo_update", {"item_id": str(uuid4()), "patch": {"title": None}}),
    ("todo_update", {"item_id": str(uuid4()), "patch": {"deleted": True}}),
])
def test_invalid_tool_arguments_are_repairable_without_authority_or_side_effect(tool_name, arguments):
    sessions = SessionStore()
    context = sessions.create(uuid4())
    controller = TaskController(sessions)
    todos = TodoService(sessions)
    registry = registry_for(todos, controller, context)
    before = sessions.snapshot(context)
    result = registry.execute(ToolCall(id="invalid", name=tool_name, arguments=arguments))
    assert result["ok"] is False and result["error"]["code"] == "invalid_arguments"
    assert result["error"]["execution_state"] == "not_started"
    assert sessions.snapshot(context) == before and todos.list_items(context) == []


def test_relative_date_and_uuid_are_normalized_before_scope_check():
    sessions = SessionStore()
    context = sessions.create(uuid4())
    clock = Clock(now=lambda: datetime(2026, 10, 20, 12, tzinfo=timezone.utc))
    todos = TodoService(sessions, clock=clock)
    controller = TaskController(sessions)
    registry = registry_for(todos, controller, context)
    approve(controller, context, ActionSpec(operation="todo.add", parameters={"title": "明天做", "due_date": "2026-10-21"}))
    added = execute(registry, "todo_add", title="明天做", due_date="明天")
    assert added["due_date"] == "2026-10-21"
    approve(controller, context, ActionSpec(operation="todo.complete", parameters={"item_id": added["id"]}))
    assert execute(registry, "todo_complete", item_id=added["id"].replace("-", ""))["changed"] is True


def test_matching_scope_still_cannot_modify_other_sessions_record():
    sessions = SessionStore()
    first, second = sessions.create(uuid4()), sessions.create(uuid4())
    todos = TodoService(sessions)
    foreign_item = todos.add(second, "其他用户")
    controller = TaskController(sessions)
    registry = registry_for(todos, controller, first)
    approve(controller, first, ActionSpec(operation="todo.delete", parameters={"item_id": str(foreign_item.id)}))
    with pytest.raises(KeyError):
        execute(registry, "todo_delete", item_id=str(foreign_item.id))
    assert todos.list_items(second) == [foreign_item] and todos.list_items(first) == []


def test_factory_rejects_mismatched_service_and_controller_store():
    first, second = SessionStore(), SessionStore()
    context = first.create(uuid4())
    with pytest.raises(ValueError, match="同一SessionStore"):
        registry_for(TodoService(first), TaskController(second), context)


def test_mode_changed_during_model_call_is_rechecked_before_real_write():
    sessions = SessionStore()
    context = sessions.create(uuid4())
    todos = TodoService(sessions)
    controller = TaskController(sessions)
    approve(controller, context, ActionSpec(operation="todo.add", parameters={"title": "周报", "due_date": None}))

    class SwitchModeModel(ScriptedModel):
        def generate(self, messages, *, tools=None):
            controller.select_mode(context, "plan")
            return super().generate(messages, tools=tools)

    model = SwitchModeModel([ModelResponse(finish_reason="tool_calls", tool_calls=[
        ToolCall(id="write", name="todo_add", arguments={"title": "周报"}),
    ])])
    result = Runtime(model, tools=registry_for(todos, controller, context)).run_session("请求", store=sessions, context=context)
    assert result.status == "waiting_user"
    assert todos.list_items(context) == []


def test_mode_switch_waits_for_already_authorized_business_commit():
    sessions = SessionStore()
    context = sessions.create(uuid4())
    controller = TaskController(sessions)
    entered, release, attempted, switched = Event(), Event(), Event(), Event()

    class BlockingTodos(TodoService):
        def add(self, context, title, *, due_date=None):
            entered.set()
            assert release.wait(timeout=5)
            return super().add(context, title, due_date=due_date)

    todos = BlockingTodos(sessions)
    registry = registry_for(todos, controller, context)
    approve(controller, context, ActionSpec(operation="todo.add", parameters={"title": "先提交", "due_date": None}))

    def switch():
        attempted.set()
        controller.select_mode(context, "plan")
        switched.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        writing = pool.submit(execute, registry, "todo_add", title="先提交")
        assert entered.wait(timeout=5)
        switching = pool.submit(switch)
        try:
            assert attempted.wait(timeout=5)
            assert switched.wait(timeout=0.05) is False
        finally:
            release.set()
        writing.result(timeout=5)
        switching.result(timeout=5)
    assert [item.title for item in todos.list_items(context)] == ["先提交"]
    assert controller.snapshot(context).mode == "plan"


def test_runtime_keeps_previous_success_when_later_call_waits_for_authorization():
    sessions = SessionStore()
    context = sessions.create(uuid4())
    todos = TodoService(sessions)
    controller = TaskController(sessions)
    approve(controller, context, ActionSpec(operation="todo.add", parameters={"title": "已确认", "due_date": None}))
    model = ScriptedModel([ModelResponse(finish_reason="tool_calls", tool_calls=[
        ToolCall(id="success", name="todo_add", arguments={"title": "已确认"}),
        ToolCall(id="denied", name="todo_add", arguments={"title": "越界"}),
        ToolCall(id="skip", name="todo_add", arguments={"title": "已确认"}),
    ])])
    result = Runtime(model, tools=registry_for(todos, controller, context)).run_session("记录", store=sessions, context=context)
    assert result.status == "waiting_user" and result.error_code == "scope_not_confirmed"
    assert [item.title for item in todos.list_items(context)] == ["已确认"]
    assert controller.snapshot(context).status == "waiting_user"
    assert len([message for message in sessions.snapshot(context).messages if message.role == "tool"]) == 3


def test_legacy_snapshot_without_work_state_defaults_to_plan_and_bad_present_state_refuses(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        sessions = SessionStore(storage=storage)
        context = sessions.create(uuid4())
        data = sessions.snapshot(context).model_dump(mode="json")
    data.pop("work_state")
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE sessions SET snapshot_json = ?", (json.dumps(data),))
    with SQLiteStorage.open(path) as storage:
        assert TaskController(SessionStore(storage=storage)).snapshot(context).mode == "plan"
    data["work_state"] = {"mode": "build", "status": "ready"}
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE sessions SET snapshot_json = ?", (json.dumps(data),))
    with SQLiteStorage.open(path) as storage:
        with pytest.raises(ValueError):
            SessionStore(storage=storage)


@pytest.mark.parametrize("failure", ["business_write", "waiting_state_write"])
def test_actual_tool_sql_failure_never_reports_success_or_changes_business_memory(tmp_path, failure):
    path = tmp_path / "tool-failure.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        sessions = SessionStore(storage=storage)
        context = sessions.create(uuid4())
        todos = TodoService(sessions)
        item = todos.add(context, "原提交")
        controller = TaskController(sessions)
        registry = registry_for(todos, controller, context)
        approve(controller, context, ActionSpec(operation="todo.add", parameters={"title": "已确认", "due_date": None}))
        before = sessions.snapshot(context)
        table, event = ("todos", "INSERT") if failure == "business_write" else ("sessions", "UPDATE")
        with sqlite3.connect(path) as connection:
            connection.execute(f"CREATE TRIGGER fail_tool BEFORE {event} ON {table} BEGIN SELECT RAISE(ABORT, 'tool write failure'); END")
        with pytest.raises(sqlite3.IntegrityError, match="tool write failure"):
            execute(registry, "todo_add", title="已确认" if failure == "business_write" else "越界")
        assert sessions.snapshot(context) == storage.load(context).snapshot == before
        assert todos.list_items(context) == storage.load(context).todos == [item]


def test_scope_waiting_survives_restart_without_replaying_or_losing_previous_todo(tmp_path):
    path = tmp_path / "waiting.sqlite3"
    action = ActionSpec(operation="todo.add", parameters={"title": "原范围", "due_date": None})
    with SQLiteStorage.create_new(path) as storage:
        sessions = SessionStore(storage=storage)
        context = sessions.create(uuid4())
        todos = TodoService(sessions)
        controller = TaskController(sessions)
        registry = registry_for(todos, controller, context)
        proposal = approve(controller, context, action)
        execute(registry, "todo_add", title="原范围")
        with pytest.raises(PermissionError):
            execute(registry, "todo_add", title="越界")
        expected = controller.snapshot(context)

    with SQLiteStorage.open(path) as storage:
        sessions = SessionStore(storage=storage)
        controller = TaskController(sessions)
        todos = TodoService(sessions)
        assert controller.snapshot(context) == expected
        assert expected.status == "waiting_user" and expected.confirmed_goal_id == proposal.proposal_id
        assert [item.title for item in todos.list_items(context)] == ["原范围"]
        assert controller.authorize(context, action).code == "waiting_user"
        controller.confirm(context, proposal.proposal_id)
        assert controller.authorize(context, action).code == "action_already_consumed"
        approve(controller, context, action)
        assert controller.authorize(context, action).allowed is True
        assert [item.title for item in todos.list_items(context)] == ["原范围"]
