from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import date, datetime, timezone
import sqlite3

import pytest

from min_agent.models import Message, ModelResponse, ToolCall
from min_agent.clock import Clock
from min_agent.runtime import Runtime
from min_agent.sessions import ExecutionContext, SessionStore
from min_agent.storage import SQLiteStorage
from min_agent.tools.calculator import CALCULATOR_TOOL
from min_agent.tools.registry import ToolRegistry
from min_agent.tools.todo import TodoService
from tests.fakes.model import ScriptedModel


def test_session_business_calls_save_automatically_and_restore_on_restart(tmp_path):
    path = tmp_path / "business.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        sessions = SessionStore(storage=storage)
        context = sessions.create(uuid4())
        assert storage.load(context).snapshot == sessions.snapshot(context)
        task = sessions.begin(context)
        assert storage.load(context).snapshot.task == task
        sessions.append(context, task.task_id, [Message(role="user", content="保留历史")])
        assert storage.load(context).snapshot == sessions.snapshot(context)
        sessions.finish(context, task.task_id, status="completed", run_id=uuid4())
        expected = sessions.snapshot(context)

    with SQLiteStorage.open(path) as storage:
        restored = SessionStore(storage=storage)
        assert restored.snapshot(context) == expected
        assert restored.begin(context).status == "running"


def test_todo_business_calls_preserve_all_records_and_order_after_restart(tmp_path):
    path = tmp_path / "todos.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        sessions = SessionStore(storage=storage)
        context = sessions.create(uuid4())
        todos = TodoService(sessions)
        first = todos.add(context, "同名", due_date="2026-10-21")
        assert storage.load(context).todos == [first]
        second = todos.add(context, "同名")
        updated = todos.update(context, first.id, title="修改", due_date=None)
        assert storage.load(context).todos == [updated, second]
        completed = todos.complete(context, first.id).item
        deleted = todos.delete(context, second.id).item
        third = todos.add(context, "仍待办", due_date="2026-10-22")
        assert storage.load(context).todos == [completed, deleted, third]
        task = sessions.begin(context)
        sessions.append(context, task.task_id, [Message(role="user", content="同时保留待办")])
        sessions.finish(context, task.task_id, status="completed")
        snapshot = sessions.snapshot(context)

    with SQLiteStorage.open(path) as storage:
        restored = SessionStore(storage=storage)
        # 尚未构造TodoService时，session写入也必须保留全部待办及墓碑。
        next_task = restored.begin(context)
        assert storage.load(context).todos == [completed, deleted, third]
        todos = TodoService(restored)
        assert todos.list_items(context) == [completed, third]
        assert restored.snapshot(context).messages == snapshot.messages
        assert todos.complete(context, first.id).changed is False
        assert todos.delete(context, second.id).changed is False
        assert restored.snapshot(context).task == next_task


def test_persistent_todo_services_share_current_state_without_lost_updates(tmp_path):
    with SQLiteStorage.create_new(tmp_path / "shared.sqlite3") as storage:
        sessions = SessionStore(storage=storage)
        context = sessions.create(uuid4())
        first_service, second_service = TodoService(sessions), TodoService(sessions)
        first = first_service.add(context, "第一个")
        second = second_service.add(context, "第二个")
        assert first_service.list_items(context) == second_service.list_items(context) == [first, second]
        assert storage.load(context).todos == [first, second]


def install_failure(path, table, event):
    # 仅获准的临时库：让真实SQL失败，而非mock存储层。
    assert table in {"sessions", "todos"} and event in {"INSERT", "UPDATE"}
    with sqlite3.connect(path) as connection:
        connection.execute(
            f"CREATE TRIGGER fail_write BEFORE {event} ON {table} "
            "BEGIN SELECT RAISE(ABORT, 'injected write failure'); END"
        )


@pytest.mark.parametrize("operation", ["create", "begin", "append", "finish"])
def test_session_write_failure_keeps_memory_and_disk_at_previous_commit(tmp_path, monkeypatch, operation):
    path = tmp_path / "session-failure.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        sessions = SessionStore(storage=storage)
        context = sessions.create(uuid4())
        task = sessions.begin(context) if operation in {"append", "finish"} else None
        before = sessions.snapshot(context)
        candidate_context = ExecutionContext(owner_id=context.owner_id, session_id=uuid4())
        if operation == "create":
            monkeypatch.setattr("min_agent.sessions.uuid4", lambda: candidate_context.session_id)
        install_failure(path, "sessions", "INSERT" if operation == "create" else "UPDATE")

        with pytest.raises(sqlite3.IntegrityError, match="injected write failure"):
            if operation == "create":
                sessions.create(context.owner_id)
            elif operation == "begin":
                sessions.begin(context)
            elif operation == "append":
                sessions.append(context, task.task_id, [Message(role="user", content="不能半写")])
            else:
                sessions.finish(context, task.task_id, status="completed")

        assert sessions.snapshot(context) == storage.load(context).snapshot == before
        assert len(storage.load_all()) == 1
        if operation == "create":
            with pytest.raises(PermissionError):
                sessions.snapshot(candidate_context)

    with SQLiteStorage.open(path) as storage:
        assert SessionStore(storage=storage).snapshot(context) == before


@pytest.mark.parametrize("operation", ["add", "update", "complete", "delete"])
def test_todo_write_failure_keeps_memory_and_disk_at_previous_commit(tmp_path, operation):
    path = tmp_path / "todo-failure.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        sessions = SessionStore(storage=storage)
        context = sessions.create(uuid4())
        todos = TodoService(sessions)
        item = todos.add(context, "旧提交", due_date="2026-10-21")
        before = storage.load(context)
        install_failure(path, "todos", "INSERT")

        with pytest.raises(sqlite3.IntegrityError, match="injected write failure"):
            if operation == "add":
                todos.add(context, "失败的新项")
            elif operation == "update":
                todos.update(context, item.id, title="未提交", due_date=None)
            else:
                getattr(todos, operation)(context, item.id)

        assert todos.list_items(context) == [item]
        assert sessions.snapshot(context) == before.snapshot
        assert storage.load(context) == before

    with SQLiteStorage.open(path) as storage:
        assert TodoService(SessionStore(storage=storage)).list_items(context) == [item]


@pytest.mark.parametrize("operation", ["complete", "delete"])
def test_repeated_todo_operation_after_restart_does_not_write(tmp_path, operation):
    path = tmp_path / "idempotent.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        sessions = SessionStore(storage=storage)
        context = sessions.create(uuid4())
        todos = TodoService(sessions)
        item = todos.add(context, "一次即可")
        expected = getattr(todos, operation)(context, item.id)
        assert expected.changed is True

    with SQLiteStorage.open(path) as storage:
        todos = TodoService(SessionStore(storage=storage))
        install_failure(path, "sessions", "UPDATE")
        actual = getattr(todos, operation)(context, item.id)
        assert actual.changed is False
        assert actual.item == expected.item


def test_runtime_continues_restored_conversation_with_tool_history(tmp_path):
    path = tmp_path / "conversation.sqlite3"
    registry = ToolRegistry()
    registry.register(CALCULATOR_TOOL)
    model = ScriptedModel([
        ModelResponse(finish_reason="tool_calls", tool_calls=[
            ToolCall(id="calc", name="calculator", arguments={"expression": "0.1+0.2"}),
        ]),
        ModelResponse(finish_reason="stop", content="0.3"),
    ])
    with SQLiteStorage.create_new(path) as storage:
        sessions = SessionStore(storage=storage)
        context = sessions.create(uuid4())
        result = Runtime(model, tools=registry).run_session("计算", store=sessions, context=context)
        assert result.status == "completed"
        expected = sessions.snapshot(context)

    with SQLiteStorage.open(path) as storage:
        restored = SessionStore(storage=storage)
        followup = ScriptedModel([ModelResponse(finish_reason="stop", content="继续")])
        result = Runtime(followup, tools=registry).run_session("接着说", store=restored, context=context)
        assert result.status == "completed"
        assert followup.calls[0] == expected.messages + [Message(role="user", content="接着说")]
        assert storage.load(context).snapshot == restored.snapshot(context)


def test_restart_preserves_running_and_unpaired_history_without_replaying(tmp_path):
    path = tmp_path / "unfinished.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        sessions = SessionStore(storage=storage)
        context = sessions.create(uuid4())
        task = sessions.begin(context)
        sessions.append(context, task.task_id, [Message(role="assistant", content="", tool_calls=[
            ToolCall(id="pending", name="calculator", arguments={"expression": "1+1"}),
        ])])
        before = sessions.snapshot(context)

    with SQLiteStorage.open(path) as storage:
        restored = SessionStore(storage=storage)
        assert restored.snapshot(context) == before
        with pytest.raises(RuntimeError, match="运行中"):
            restored.begin(context)
        restored.finish(context, task.task_id, status="failed", error_code="interrupted")
        with pytest.raises(RuntimeError, match="未配对"):
            restored.begin(context)
        assert storage.load(context).snapshot == restored.snapshot(context)


def test_restored_sessions_reject_foreign_owner_and_cross_session_todo(tmp_path):
    path = tmp_path / "isolation.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        sessions = SessionStore(storage=storage)
        first, second = sessions.create(uuid4()), sessions.create(uuid4())
        item = TodoService(sessions).add(first, "私有")

    with SQLiteStorage.open(path) as storage:
        sessions = SessionStore(storage=storage)
        todos = TodoService(sessions)
        foreign = ExecutionContext(owner_id=second.owner_id, session_id=first.session_id)
        before = storage.load_all()
        with pytest.raises(PermissionError):
            sessions.snapshot(foreign)
        with pytest.raises(PermissionError):
            todos.add(foreign, "无权写入")
        for operation in (todos.update, todos.complete, todos.delete):
            with pytest.raises(KeyError):
                operation(second, item.id)
        assert todos.list_items(second) == []
        assert storage.load_all() == before


def test_same_session_concurrent_history_and_todo_updates_keep_both(tmp_path):
    with SQLiteStorage.create_new(tmp_path / "threads.sqlite3") as storage:
        sessions = SessionStore(storage=storage)
        context = sessions.create(uuid4())
        task = sessions.begin(context)
        todos = TodoService(sessions)
        barrier = Barrier(2)

        def add_history():
            barrier.wait(timeout=5)
            for index in range(10):
                sessions.append(context, task.task_id, [Message(role="user", content=str(index))])

        def add_todos():
            barrier.wait(timeout=5)
            return [todos.add(context, str(index)) for index in range(10)]

        with ThreadPoolExecutor(max_workers=2) as pool:
            history_future = pool.submit(add_history)
            todo_future = pool.submit(add_todos)
            history_future.result(timeout=10)
            items = todo_future.result(timeout=10)
        sessions.finish(context, task.task_id, status="completed")
        bundle = storage.load(context)
        assert bundle.snapshot == sessions.snapshot(context)
        assert len(bundle.snapshot.messages) == 10
        assert bundle.todos == todos.list_items(context) == items


def test_corrupt_database_refuses_business_startup_instead_of_empty_fallback(tmp_path):
    path = tmp_path / "corrupt.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        SessionStore(storage=storage).create(uuid4())
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE sessions SET snapshot_json = '{}' ")
    with SQLiteStorage.open(path) as storage:
        with pytest.raises(ValueError):
            SessionStore(storage=storage)


def test_closed_storage_rejects_writes_without_changing_cached_business_state(tmp_path):
    with SQLiteStorage.create_new(tmp_path / "closed.sqlite3") as storage:
        sessions = SessionStore(storage=storage)
        context = sessions.create(uuid4())
        todos = TodoService(sessions)
        before = sessions.snapshot(context)
    with pytest.raises(RuntimeError, match="关闭"):
        sessions.begin(context)
    with pytest.raises(RuntimeError, match="关闭"):
        todos.add(context, "不能悄悄改内存")
    assert sessions.snapshot(context) == before
    assert todos.list_items(context) == []


@pytest.mark.parametrize("stage", ["begin", "append", "finish"])
def test_runtime_propagates_persistence_failure_and_restores_last_commit(tmp_path, stage):
    path = tmp_path / "runtime-failure.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        sessions = SessionStore(storage=storage)
        context = sessions.create(uuid4())
        condition = {
            "begin": "1",
            "append": "instr(NEW.snapshot_json, '\"role\":\"user\"') > 0",
            "finish": "instr(NEW.snapshot_json, '\"status\":\"completed\"') > 0",
        }[stage]
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TRIGGER fail_runtime BEFORE UPDATE ON sessions "
                f"WHEN {condition} BEGIN SELECT RAISE(ABORT, 'runtime save failure'); END"
            )
        model = ScriptedModel([ModelResponse(finish_reason="stop", content="回答")])
        with pytest.raises(sqlite3.IntegrityError, match="runtime save failure"):
            Runtime(model).run_session("请求", store=sessions, context=context)
        snapshot = sessions.snapshot(context)
        assert storage.load(context).snapshot == snapshot
        assert len(model.calls) == (0 if stage == "begin" else 1)
        if stage == "begin":
            assert snapshot.task is None
        else:
            assert snapshot.task.status == "running"
        assert [message.content for message in snapshot.messages] == (
            ["请求", "回答"] if stage == "finish" else []
        )

    with SQLiteStorage.open(path) as storage:
        assert SessionStore(storage=storage).snapshot(context) == snapshot


def test_restart_recomputes_overdue_from_absolute_date_and_new_clock(tmp_path):
    path = tmp_path / "clock.sqlite3"
    first_clock = Clock(now=lambda: datetime(2026, 10, 20, 12, tzinfo=timezone.utc))
    with SQLiteStorage.create_new(path) as storage:
        sessions = SessionStore(storage=storage)
        context = sessions.create(uuid4())
        todos = TodoService(sessions, clock=first_clock)
        item = todos.add(context, "今天做", due_date="今天")
        assert item.due_date == date(2026, 10, 20)
        assert todos.list_views(context)[0].overdue is False

    later_clock = Clock(now=lambda: datetime(2026, 10, 21, 12, tzinfo=timezone.utc))
    with SQLiteStorage.open(path) as storage:
        todos = TodoService(SessionStore(storage=storage), clock=later_clock)
        assert todos.list_items(context) == [item]
        assert todos.list_views(context)[0].overdue is True
