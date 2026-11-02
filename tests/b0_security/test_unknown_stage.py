"""R2 阶段证据：业务开始后的异常不得被当作业务前拒绝而清除 UNKNOWN。"""
import json
import sqlite3
from uuid import uuid4

import pytest
from pydantic import BaseModel, ConfigDict

from min_agent.models import ToolCall
from min_agent.sessions import SessionStore
from min_agent.storage import SQLiteStorage
from min_agent.task_state import ActionSpec, AuthorizationRequired, GoalSpec, TaskController
from min_agent.tools.registry import ToolDefinition
from min_agent.tools.todo import TodoService
from min_agent.tools.todo_tools import todo_registry


def goal():
    return GoalSpec(objective="synthetic", completion_criteria=("synthetic",),
                    actions=(ActionSpec(operation="todo.add",
                                        parameters={"title": "synthetic", "due_date": None}),))


def setup(tmp_path):
    path = tmp_path / "stage.sqlite3"
    storage = SQLiteStorage.create_new(path)
    store = SessionStore(storage=storage)
    context = store.create(uuid4())
    controller = TaskController(store)
    proposal = controller.propose(context, goal())
    controller.confirm(context, proposal.proposal_id)
    controller.select_mode(context, "build")
    return path, storage, store, context, controller, proposal


def registry(store, context):
    return todo_registry(TodoService(store), TaskController(store), context)


def call(identity="write"):
    return ToolCall(id=identity, name="todo_add",
                    arguments={"title": "synthetic", "due_date": None})


def raw(path):
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        sessions = [json.loads(row[0]) for row in
                    connection.execute("SELECT snapshot_json FROM sessions ORDER BY session_id")]
        todos = [json.loads(row[0]) for row in
                 connection.execute("SELECT item_json FROM todos ORDER BY session_id, position")]
    return {"sessions": sessions, "todos": todos}


def test_business_started_ar_preserves_unknown_and_blocks_same_id(tmp_path, monkeypatch):
    path, storage, store, context, controller, proposal = setup(tmp_path)
    try:
        store.begin(context)
        entered = []
        def business_started(self, context, **parameters):
            count = self._sessions.extension(context, "handler_started", 0)
            self._sessions.set_extension(context, "handler_started", count + 1)
            entered.append(dict(parameters))
            raise AuthorizationRequired("task_not_active")
        monkeypatch.setattr(TodoService, "add", business_started)
        with pytest.raises(AuthorizationRequired) as captured:
            registry(store, context).execute(call(), operation_id="stage-fault")
        assert captured.value.code == "task_not_active"
        first = raw(path)
        assert first["sessions"][0]["extensions"]["handler_started"] == 1
        assert first["sessions"][0]["extensions"]["operations"]["stage-fault"]["status"] == "unknown"
        assert first["todos"] == []
        with pytest.raises(RuntimeError, match="核对"):
            registry(store, context).execute(call(), operation_id="stage-fault")
        second = raw(path)
        assert second["sessions"][0]["extensions"]["handler_started"] == 1
        assert second["sessions"][0]["extensions"]["operations"]["stage-fault"]["status"] == "unknown"
        assert second["todos"] == [] and entered
    finally:
        storage.close()


def test_handler_only_ar_without_guard_proof_does_not_clean(tmp_path):
    path, storage, store, context, controller, proposal = setup(tmp_path)
    try:
        store.begin(context)
        class Empty(BaseModel):
            model_config = ConfigDict(strict=True, extra="forbid")
        def handler_only():
            raise AuthorizationRequired("task_not_active")
        todo = registry(store, context)
        todo.register(ToolDefinition("todo_probe", "handler-only fault", Empty, handler_only))
        with pytest.raises(AuthorizationRequired) as captured:
            todo.execute(ToolCall(id="probe", name="todo_probe", arguments={}),
                         operation_id="handler-only")
        assert captured.value.code == "task_not_active"
        first = raw(path)
        assert first["sessions"][0]["extensions"]["operations"]["handler-only"]["status"] == "unknown"
        with pytest.raises(RuntimeError, match="核对"):
            todo.execute(ToolCall(id="probe", name="todo_probe", arguments={}),
                         operation_id="handler-only")
        assert raw(path)["sessions"][0]["extensions"]["operations"]["handler-only"]["status"] == "unknown"
    finally:
        storage.close()


def test_real_guard_denial_cleans_fresh_and_preserves_other_unknown(tmp_path):
    path, storage, store, context, controller, proposal = setup(tmp_path)
    try:
        first = store.begin(context)
        store.finish(context, first.task_id, status="completed")
        second = store.begin(context)
        store.set_extension(context, "operations", {
            "history": {"status": "unknown", "signature": "old"}})
        with pytest.raises(AuthorizationRequired) as captured:
            registry(store, context).execute(call(), operation_id="fresh")
        assert captured.value.code == "logical_task_mismatch"
        state = raw(path)["sessions"][0]["extensions"]
        assert state["operations"] == {"history": {"status": "unknown", "signature": "old"}}
        assert not state.get("action_receipts")
        controller.confirm(context, proposal.proposal_id)
        assert registry(store, context).execute(call(), operation_id="fresh")["ok"] is True
        assert len(TodoService(store).list_items(context)) == 1
        store.finish(context, second.task_id, status="completed")
    finally:
        storage.close()


def test_nested_managed_and_unmanaged_operations_do_not_cross(tmp_path):
    path, storage, store, context, controller, proposal = setup(tmp_path)
    try:
        store.begin(context)
        reg = registry(store, context)
        class Empty(BaseModel):
            model_config = ConfigDict(strict=True, extra="forbid")

        def outer():
            with controller.guard(context, goal().actions[0]):
                store.set_extension(context, "outer_entered", 1)
                controller.select_mode(context, "plan")
                with pytest.raises(AuthorizationRequired) as inner:
                    reg.execute(ToolCall(id="inner", name="todo_add",
                                         arguments={"title": "synthetic", "due_date": None}),
                                operation_id="inner")
                assert inner.value.code == "plan_read_only"
                raise AuthorizationRequired("task_not_active")

        reg.register(ToolDefinition("todo_outer", "synthetic nested", Empty, outer))
        with pytest.raises(AuthorizationRequired) as captured:
            reg.execute(ToolCall(id="outer", name="todo_outer", arguments={}),
                        operation_id="outer")
        assert captured.value.code == "task_not_active"
        final = raw(path)
        operations = final["sessions"][0]["extensions"]["operations"]
        assert operations["outer"]["status"] == "unknown"
        assert "inner" not in operations
        assert final["todos"] == []
        assert final["sessions"][0]["extensions"]["outer_entered"] == 1
        from min_agent.operations import operation_phase
        assert operation_phase.get() is None
    finally:
        storage.close()
