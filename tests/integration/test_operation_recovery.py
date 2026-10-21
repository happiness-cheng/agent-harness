from uuid import uuid4

import pytest

from min_agent.operations import operation_scope
from min_agent.sessions import SessionStore
from min_agent.storage import SQLiteStorage
from min_agent.tools.todo import TodoService


def test_add_recovery_after_commit_and_lost_response_uses_same_operation_id(tmp_path):
    path = tmp_path / "writes.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        sessions = SessionStore(storage=storage); context = sessions.create(uuid4())
        with operation_scope("synthetic-operation"):
            item = TodoService(sessions).add(context, "只创建一条")
    with SQLiteStorage.open(path) as storage:
        sessions = SessionStore(storage=storage); todos = TodoService(sessions)
        with operation_scope("synthetic-operation"):
            restored = todos.add(context, "只创建一条")
        assert restored.id == item.id and len(todos.list_items(context)) == 1
        assert todos.add(context, "只创建一条").id != item.id


def test_changed_correction_without_new_evidence_pauses():
    from min_agent.progress_guard import ProgressGuard
    from min_agent.models import ToolCall
    guard = ProgressGuard(); call = ToolCall(id="a", name="search", arguments={"q": "a"})
    for _ in range(3): guard.observe(call, {"result": "no results"})
    changed = ToolCall(id="b", name="search", arguments={"q": "different"})
    assert guard.observe(changed, {"result": "no results"}) == "pause"
