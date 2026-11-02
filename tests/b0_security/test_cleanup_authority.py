"""R3 清理资格：只有受审计内置写 adapter 的本次调用可清理 UNKNOWN。"""
import ast
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import BaseModel, ConfigDict

from min_agent.models import ToolCall
from min_agent.sessions import SessionStore
from min_agent.task_state import ActionSpec, AuthorizationRequired, GoalSpec, TaskController
from min_agent.tools.registry import ToolDefinition, ToolRegistry
from min_agent.tools.reminder_tools import register_reminders
from min_agent.tools.todo import TodoService
from min_agent.tools.todo_tools import todo_registry


ROOT = Path(__file__).resolve().parents[2]


def goal():
    return GoalSpec(objective="synthetic", completion_criteria=("synthetic",),
                    actions=(ActionSpec(operation="todo.add",
                                        parameters={"title": "synthetic", "due_date": None}),))


def setup():
    store = SessionStore()
    context = store.create(uuid4())
    controller = TaskController(store)
    proposal = controller.propose(context, goal())
    controller.confirm(context, proposal.proposal_id)
    controller.select_mode(context, "build")
    return store, context, controller, proposal


def closed_grant_new_task():
    store, context, controller, proposal = setup()
    first = store.begin(context)
    store.finish(context, first.task_id, status="completed")
    second = store.begin(context)
    return store, context, controller, proposal, second


def call(identity="write"):
    return ToolCall(id=identity, name="todo_add",
                    arguments={"title": "synthetic", "due_date": None})


def custom_handler(mode):
    def handler(store, context, controller):
        def run(**kwargs):
            try:
                with controller.guard(context, goal().actions[0]):
                    raise AssertionError("旧 grant 不应通过")
            except AuthorizationRequired as error:
                store.set_extension(context, "handler_started", 1)
                if mode == "different":
                    raise AuthorizationRequired("task_not_active")
                if mode == "same":
                    raise AuthorizationRequired(error.code)
                if mode == "original":
                    raise error
                raise
            finally:
                if mode == "finally":
                    store.set_extension(context, "handler_started", 1)
        return run
    return handler


@pytest.mark.parametrize("mode", ["different", "same", "original", "finally"])
def test_custom_control_flows_preserve_unknown_and_do_not_reenter(mode):
    store, context, controller, proposal, task = closed_grant_new_task()
    registry = todo_registry(TodoService(store), controller, context)
    class Empty(BaseModel):
        model_config = ConfigDict(strict=True, extra="forbid")
    registry.register(ToolDefinition("todo_stage_probe", "synthetic", Empty,
                                     custom_handler(mode)(store, context, controller)))
    with pytest.raises(AuthorizationRequired):
        registry.execute(ToolCall(id="probe", name="todo_stage_probe", arguments={}),
                         operation_id="caught")
    assert registry._tools["todo_stage_probe"].managed_execution is False
    assert store.extension(context, "handler_started") == 1
    assert store.extension(context, "operations")["caught"]["status"] == "unknown"
    with pytest.raises(RuntimeError, match="核对"):
        registry.execute(ToolCall(id="probe", name="todo_stage_probe", arguments={}),
                         operation_id="caught")
    assert store.extension(context, "handler_started") == 1
    store.finish(context, task.task_id, status="failed")


def test_managed_todo_adapter_still_cleans_real_pre_business_denial():
    store, context, controller, proposal, task = closed_grant_new_task()
    registry = todo_registry(TodoService(store), controller, context)
    with pytest.raises(AuthorizationRequired) as captured:
        registry.execute(call(), operation_id="managed-deny")
    assert captured.value.code == "logical_task_mismatch"
    assert not store.extension(context, "operations")
    assert not store.extension(context, "action_receipts", {})
    store.finish(context, task.task_id, status="failed")


def test_managed_business_started_exception_preserves_unknown(monkeypatch):
    store, context, controller, proposal = setup()
    task = store.begin(context)
    entered = []
    def business_started(self, context, **parameters):
        store.set_extension(context, "handler_started", 1)
        entered.append(parameters)
        raise AuthorizationRequired("task_not_active")
    monkeypatch.setattr(TodoService, "add", business_started)
    registry = todo_registry(TodoService(store), controller, context)
    with pytest.raises(AuthorizationRequired):
        registry.execute(call(), operation_id="started")
    assert store.extension(context, "operations")["started"]["status"] == "unknown"
    with pytest.raises(RuntimeError, match="核对"):
        registry.execute(call(), operation_id="started")
    assert store.extension(context, "handler_started") == 1 and entered
    store.finish(context, task.task_id, status="failed")


def test_audit_fake_replacement_is_unmanaged():
    class Params(BaseModel):
        model_config = ConfigDict(strict=True, extra="forbid")
    fake = ToolDefinition("todo_update", "fake", Params, lambda **kwargs: "x")
    assert fake.managed_execution is False


def test_replaced_handler_does_not_inherit_managed_marker(monkeypatch):
    store, context, controller, proposal, task = closed_grant_new_task()
    registry = todo_registry(TodoService(store), controller, context)
    original = registry._tools["todo_add"]
    registry._tools["todo_add"] = ToolDefinition(
        "todo_add", original.description, original.parameters,
        custom_handler("same")(store, context, controller))
    assert registry._tools["todo_add"].managed_execution is False
    with pytest.raises(AuthorizationRequired):
        registry.execute(call(), operation_id="replacement")
    assert store.extension(context, "operations")["replacement"]["status"] == "unknown"
    store.finish(context, task.task_id, status="failed")


def test_registered_query_tools_are_unmanaged_and_write_tools_managed():
    store, context, controller, proposal = setup()
    todo = todo_registry(TodoService(store), controller, context)
    for name in ("todo_list",):
        assert todo.get(name).managed_execution is False
    for name in ("todo_add", "todo_update", "todo_complete", "todo_delete"):
        assert todo.get(name).managed_execution is True

    reminders = ToolRegistry(sessions=store, context=context)
    register_reminders(reminders, object(), controller, context)
    assert reminders.get("reminder_list").managed_execution is False
    for name in ("reminder_add", "reminder_update", "reminder_cancel"):
        assert reminders.get(name).managed_execution is True


def test_definition_strictly_rejects_truthy_non_bool_and_keeps_old_four_args():
    class Params(BaseModel):
        model_config = ConfigDict(strict=True, extra="forbid")
    old = ToolDefinition("old", "old", Params, lambda: "ok")
    assert old.managed_execution is False
    for value in ("yes", 1, None, object()):
        with pytest.raises(TypeError, match="bool"):
            ToolDefinition("bad", "bad", Params, lambda: "ok", managed_execution=value)


def test_schemas_do_not_expose_marker_or_authority():
    store, context, controller, proposal = setup()
    registry = todo_registry(TodoService(store), controller, context)
    for schema in registry.schemas():
        assert set(schema) == {"name", "description", "parameters"}
        assert "managed_execution" not in schema


def test_request_errors_remain_request_errors_with_operation_id():
    store = SessionStore()
    context = store.create(uuid4())
    controller = TaskController(store)
    registry = todo_registry(TodoService(store), controller, context)
    unknown = registry.execute(ToolCall(id="u", name="todo_missing", arguments={}),
                               operation_id="unknown")
    assert unknown["error"]["code"] == "unknown_tool"
    invalid = registry.execute(ToolCall(id="i", name="todo_add", arguments={"extra": 1}),
                               operation_id="invalid")
    assert invalid["error"]["code"] == "invalid_arguments"


def _no_try(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    functions = [node for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef) and node.name in {"_run", "execute"}]
    assert functions
    for function in functions:
        assert not any(isinstance(node, (ast.Try, ast.ExceptHandler)) for node in ast.walk(function))
        guards = [node for node in ast.walk(function)
                  if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute)
                  and node.func.attr == "guard"]
        assert guards


def test_static_contract_audits_registered_and_core_adapters():
    _no_try(ROOT / "src/min_agent/tools/todo_tools.py")
    _no_try(ROOT / "src/min_agent/tools/reminder_tools.py")
