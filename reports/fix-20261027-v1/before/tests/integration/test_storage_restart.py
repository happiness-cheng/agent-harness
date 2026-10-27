from uuid import uuid4
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import sqlite3
from threading import Barrier

import pytest

from min_agent.models import Message, ToolCall
from min_agent.sessions import ExecutionContext, SessionStore
from min_agent.storage import SQLiteStorage
from min_agent.tools.todo import TodoService


def test_closed_database_reopens_with_session_history_task_and_isolated_full_todos(tmp_path):
    sessions = SessionStore()
    owner = uuid4()
    a, b = sessions.create(owner), sessions.create(owner)
    service = TodoService(sessions)
    task = sessions.begin(a)
    sessions.append(a, task.task_id, [Message(role="user", content="保留这段历史")])
    sessions.finish(a, task.task_id, status="completed")
    first = service.add(a, "写周报", due_date="2026-10-20")
    removed = service.delete(a, service.add(a, "写周报").id).item
    other = service.add(b, "B私有记录")
    snapshot_a, snapshot_b = sessions.snapshot(a), sessions.snapshot(b)
    path = tmp_path / "state.sqlite3"

    with SQLiteStorage.create_new(path) as storage:
        storage.save(a, snapshot_a, [first, removed])
        storage.save(b, snapshot_b, [other])

    with SQLiteStorage.open(path) as storage:
        restored_a, restored_b = storage.load(a), storage.load(b)
        assert restored_a.snapshot == snapshot_a
        assert restored_a.todos == [first, removed]
        assert restored_a.todos[1].deleted is True
        assert restored_b.snapshot == snapshot_b
        assert restored_b.todos == [other]
        with pytest.raises(PermissionError):
            storage.load(ExecutionContext(owner_id=uuid4(), session_id=a.session_id))


@pytest.fixture
def state():
    sessions = SessionStore()
    context = sessions.create(uuid4())
    service = TodoService(sessions)
    first = service.add(context, "第一条", due_date="2026-10-20")
    second = service.add(context, "第二条")
    return sessions, context, service, first, second


def append_history(sessions, context):
    task = sessions.begin(context)
    sessions.append(context, task.task_id, [Message(role="user", content="新快照历史")])
    sessions.finish(context, task.task_id, status="completed")
    return sessions.snapshot(context)


@pytest.mark.parametrize("deleted", [False, True])
def test_save_cannot_omit_existing_live_or_logically_deleted_records(tmp_path, state, deleted):
    sessions, context, service, first, second = state
    if deleted:
        second = service.delete(context, second.id).item
    before = sessions.snapshot(context)
    path = tmp_path / "state.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        storage.save(context, before, [first, second])
        updated = append_history(sessions, context)

        with pytest.raises(ValueError, match="保留"):
            storage.save(context, updated, [first])

        assert storage.load(context).snapshot == before
        assert storage.load(context).todos == [first, second]


def test_save_cannot_reorder_existing_record_identities(tmp_path, state):
    sessions, context, _, first, second = state
    snapshot = sessions.snapshot(context)
    with SQLiteStorage.create_new(tmp_path / "state.sqlite3") as storage:
        storage.save(context, snapshot, [first, second])

        with pytest.raises(ValueError, match="顺序"):
            storage.save(context, snapshot, [second, first])

        assert storage.load(context).todos == [first, second]


@pytest.mark.parametrize("existing", [False, True])
def test_real_sqlite_failure_on_second_todo_rolls_back_earlier_writes_and_reopens_cleanly(tmp_path, state, existing):
    sessions, context, service, first, second = state
    path = tmp_path / "state.sqlite3"
    before = sessions.snapshot(context)
    with SQLiteStorage.create_new(path) as storage:
        if existing:
            storage.save(context, before, [first, second])
        with closing(sqlite3.connect(path, autocommit=True)) as fault:
            fault.execute("""CREATE TRIGGER reject_second_todo BEFORE INSERT ON todos
                WHEN NEW.position = 1 BEGIN SELECT RAISE(ABORT, 'forced_write_failure'); END""")
        updated = append_history(sessions, context)
        changed_first = service.update(context, first.id, title="不得半写入")

        with pytest.raises(sqlite3.IntegrityError, match="forced_write_failure"):
            storage.save(context, updated, [changed_first, second])

        if existing:
            assert storage.load(context).snapshot == before
            assert storage.load(context).todos == [first, second]
        else:
            assert storage.load_all() == []

    with SQLiteStorage.open(path) as storage:
        if existing:
            assert storage.load(context).snapshot == before
            assert storage.load(context).todos == [first, second]
        else:
            with pytest.raises(PermissionError):
                storage.load(context)


def execute_sql(path, statement, parameters=()):
    with closing(sqlite3.connect(path, autocommit=True)) as connection:
        connection.execute(statement, parameters)


def test_repeat_save_and_append_preserve_order_without_implicitly_saving_business_changes(tmp_path, state):
    sessions, context, service, first, second = state
    snapshot = sessions.snapshot(context)
    with SQLiteStorage.create_new(tmp_path / "state.sqlite3") as storage:
        storage.save(context, snapshot, [first, second])
        changed = service.update(context, first.id, title="新标题")
        third = service.add(context, "第一条")
        assert storage.load(context).todos == [first, second]

        storage.save(context, snapshot, [changed, second, third])
        storage.save(context, snapshot, [changed, second, third])

        assert storage.load(context).todos == [changed, second, third]
        assert len(storage.load_all()) == 1


def test_trusted_load_all_recovers_same_owner_sessions_and_other_owner_without_merging(tmp_path, state):
    sessions, a, service, first, second = state
    b, c = sessions.create(a.owner_id), sessions.create(uuid4())
    item_b, item_c = service.add(b, "第一条"), service.add(c, "第一条")
    with SQLiteStorage.create_new(tmp_path / "state.sqlite3") as storage:
        storage.save(a, sessions.snapshot(a), [first, second])
        storage.save(b, sessions.snapshot(b), [item_b])
        storage.save(c, sessions.snapshot(c), [item_c])

        recovered = {bundle.snapshot.session_id: bundle for bundle in storage.load_all()}

        assert set(recovered) == {a.session_id, b.session_id, c.session_id}
        assert recovered[a.session_id].todos == [first, second]
        assert recovered[b.session_id].todos == [item_b]
        assert recovered[c.session_id].snapshot.owner_id == c.owner_id
        assert recovered[c.session_id].todos == [item_c]


@pytest.mark.parametrize("problem", ["owner", "session", "todo_session", "duplicate_id", "bypass_title", "bypass_snapshot"])
def test_invalid_or_cross_owned_snapshot_never_creates_partial_records(tmp_path, state, problem):
    sessions, context, _, first, second = state
    snapshot = sessions.snapshot(context)
    items = [first, second]
    if problem == "owner":
        context = ExecutionContext(owner_id=uuid4(), session_id=context.session_id)
    elif problem == "session":
        context = ExecutionContext(owner_id=context.owner_id, session_id=uuid4())
    elif problem == "todo_session":
        items = [first, second.model_copy(update={"session_id": uuid4()})]
    elif problem == "duplicate_id":
        items = [first, first]
    elif problem == "bypass_title":
        items = [first, second.model_copy(update={"title": None})]
    else:
        snapshot = snapshot.model_copy(update={"owner_id": "not-a-uuid"})

    with SQLiteStorage.create_new(tmp_path / "state.sqlite3") as storage:
        with pytest.raises((PermissionError, ValueError, TypeError)):
            storage.save(context, snapshot, items)
        assert storage.load_all() == []


def test_existing_owner_is_checked_before_invalid_save_payload(tmp_path, state):
    sessions, context, _, first, second = state
    snapshot = sessions.snapshot(context)
    with SQLiteStorage.create_new(tmp_path / "state.sqlite3") as storage:
        storage.save(context, snapshot, [first, second])
        foreign = ExecutionContext(owner_id=uuid4(), session_id=context.session_id)

        with pytest.raises(PermissionError):
            storage.save(foreign, None, None)

        assert storage.load(context).snapshot == snapshot
        assert storage.load(context).todos == [first, second]


@pytest.mark.parametrize("problem", ["wrong_type", "bypass_validation"])
def test_context_is_revalidated_for_storage_reads_and_writes(tmp_path, state, problem):
    sessions, context, _, first, second = state
    snapshot = sessions.snapshot(context)
    invalid = "not-context" if problem == "wrong_type" else ExecutionContext.model_construct(
        owner_id="not-a-uuid", session_id=context.session_id,
    )
    with SQLiteStorage.create_new(tmp_path / "state.sqlite3") as storage:
        storage.save(context, snapshot, [first, second])
        with pytest.raises((TypeError, ValueError)):
            storage.load(invalid)
        with pytest.raises((TypeError, ValueError)):
            storage.save(invalid, snapshot, [first, second])
        assert storage.load(context).todos == [first, second]


def test_unknown_session_load_uses_same_denial_as_wrong_owner(tmp_path, state):
    sessions, context, _, first, second = state
    with SQLiteStorage.create_new(tmp_path / "state.sqlite3") as storage:
        storage.save(context, sessions.snapshot(context), [first, second])
        errors = []
        for invalid in [
            ExecutionContext(owner_id=context.owner_id, session_id=uuid4()),
            ExecutionContext(owner_id=uuid4(), session_id=context.session_id),
        ]:
            with pytest.raises(PermissionError) as error:
                storage.load(invalid)
            errors.append(str(error.value))
        assert errors == ["会话不存在或不属于当前用户"] * 2


@pytest.mark.parametrize("problem", ["orphan_tool", "completed_pending", "no_task_pending"])
def test_invalid_history_pairing_is_not_saved(tmp_path, state, problem):
    sessions, context, _, first, second = state
    snapshot = sessions.snapshot(context)
    if problem == "orphan_tool":
        messages = [Message(role="tool", content="孤立结果", tool_call_id="missing")]
    else:
        messages = [Message(role="assistant", content="", tool_calls=[ToolCall(id="call", name="tool", arguments={})])]
        if problem == "completed_pending":
            task = sessions.begin(context)
            sessions.finish(context, task.task_id, status="completed")
            snapshot = sessions.snapshot(context)
    snapshot = snapshot.model_copy(update={"messages": messages})
    with SQLiteStorage.create_new(tmp_path / "state.sqlite3") as storage:
        with pytest.raises(ValueError):
            storage.save(context, snapshot, [first, second])
        assert storage.load_all() == []


@pytest.mark.parametrize("status", ["running", "failed", "partial", "cancelled"])
def test_pending_task_facts_survive_restart_without_automatic_replay_or_completion(tmp_path, state, status):
    sessions, context, _, first, second = state
    task = sessions.begin(context)
    sessions.append(context, task.task_id, [
        Message(role="user", content="保留未完成进度"),
        Message(role="assistant", content="", tool_calls=[ToolCall(id="call", name="tool", arguments={})]),
    ])
    if status != "running":
        sessions.finish(context, task.task_id, status=status)
    snapshot = sessions.snapshot(context)
    path = tmp_path / "state.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        storage.save(context, snapshot, [first, second])
    with SQLiteStorage.open(path) as storage:
        restored = storage.load(context)
        assert restored.snapshot == snapshot
        assert restored.snapshot.task.status == status
        assert restored.snapshot.messages[-1].tool_calls[0].id == "call"


@pytest.mark.parametrize("content", [b"", b"user-owned-file"])
def test_create_new_refuses_existing_files_without_changing_bytes(tmp_path, content):
    path = tmp_path / "existing.sqlite3"
    path.write_bytes(content)

    with pytest.raises(FileExistsError):
        SQLiteStorage.create_new(path)

    assert path.read_bytes() == content


def test_open_missing_path_does_not_create_database(tmp_path):
    path = tmp_path / "missing.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        SQLiteStorage.open(path)
    assert not path.exists()


@pytest.mark.parametrize("kind", ["empty", "foreign_database"])
def test_open_unknown_file_refuses_without_initializing_or_modifying_it(tmp_path, kind):
    path = tmp_path / "unknown.sqlite3"
    if kind == "empty":
        path.write_bytes(b"")
    else:
        execute_sql(path, "CREATE TABLE unrelated (value TEXT)")
    before = path.read_bytes()

    with pytest.raises(ValueError, match="格式或版本"):
        SQLiteStorage.open(path)

    assert path.read_bytes() == before


@pytest.mark.parametrize("pragma", ["PRAGMA application_id = 0", "PRAGMA user_version = 99"])
def test_open_rejects_wrong_application_or_newer_schema_without_migrating(tmp_path, pragma):
    path = tmp_path / "state.sqlite3"
    SQLiteStorage.create_new(path).close()
    execute_sql(path, pragma)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="格式或版本"):
        SQLiteStorage.open(path)
    assert path.read_bytes() == before


def test_open_rejects_modified_table_structure_without_repair(tmp_path):
    path = tmp_path / "state.sqlite3"
    SQLiteStorage.create_new(path).close()
    execute_sql(path, "ALTER TABLE sessions ADD COLUMN unexpected TEXT")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="表结构"):
        SQLiteStorage.open(path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("problem", ["snapshot_owner", "todo_json", "todo_id", "todo_session", "position"])
def test_corrupt_record_is_not_loaded_skipped_or_silently_overwritten(tmp_path, state, problem):
    sessions, context, _, first, second = state
    snapshot = sessions.snapshot(context)
    path = tmp_path / "state.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        storage.save(context, snapshot, [first, second])
    if problem == "snapshot_owner":
        execute_sql(path, "UPDATE sessions SET snapshot_json = ?", (
            snapshot.model_copy(update={"owner_id": uuid4()}).model_dump_json(),
        ))
    elif problem == "position":
        execute_sql(path, "UPDATE todos SET position = 10 WHERE todo_id = ?", (str(first.id),))
    else:
        updates = {"todo_json": None, "todo_id": {"id": uuid4()}, "todo_session": {"session_id": uuid4()}}
        update = updates[problem]
        payload = "{" if update is None else first.model_copy(update=update).model_dump_json()
        execute_sql(path, "UPDATE todos SET item_json = ? WHERE todo_id = ?", (payload, str(first.id)))
    before = path.read_bytes()

    with SQLiteStorage.open(path) as storage:
        with pytest.raises(ValueError):
            storage.load(context)
        with pytest.raises(ValueError):
            storage.load_all()
        with pytest.raises(ValueError):
            storage.save(context, snapshot, [first, second])

    assert path.read_bytes() == before


def test_storage_connection_enforces_sqlite_foreign_keys_and_rolls_back_trigger_side_effect(tmp_path, state):
    sessions, context, _, first, second = state
    path = tmp_path / "state.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        execute_sql(path, """CREATE TRIGGER inject_orphan AFTER INSERT ON todos WHEN NEW.position = 0
            BEGIN INSERT INTO todos VALUES ('missing-session', 'orphan', 0, '{}'); END""")
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            storage.save(context, sessions.snapshot(context), [first, second])
        assert storage.load_all() == []


def test_context_manager_closes_connection_and_explicit_close_is_idempotent(tmp_path, state):
    sessions, context, _, first, second = state
    with SQLiteStorage.create_new(tmp_path / "state.sqlite3") as storage:
        storage.save(context, sessions.snapshot(context), [first, second])
    storage.close()
    with pytest.raises(RuntimeError, match="已关闭"):
        storage.load(context)
    with pytest.raises(RuntimeError, match="已关闭"):
        storage.load_all()
    with pytest.raises(RuntimeError, match="已关闭"):
        storage.save(context, sessions.snapshot(context), [first, second])
    with pytest.raises(RuntimeError, match="已关闭"):
        with storage:
            pass


def test_input_and_returned_snapshots_are_detached_from_saved_state(tmp_path, state):
    sessions, context, _, first, second = state
    snapshot = sessions.snapshot(context)
    expected = snapshot.model_copy(deep=True)
    items = [first, second]
    with SQLiteStorage.create_new(tmp_path / "state.sqlite3") as storage:
        storage.save(context, snapshot, items)
        snapshot.messages.append(Message(role="user", content="仅本地输入变化"))
        items.clear()
        loaded = storage.load(context)
        loaded.snapshot.messages.append(Message(role="user", content="仅本地返回值变化"))
        loaded.todos.clear()
        assert storage.load(context).snapshot == expected
        assert storage.load(context).todos == [first, second]


def test_two_threads_can_save_and_load_different_sessions_through_one_adapter(tmp_path, state):
    sessions, a, service, first, second = state
    b = sessions.create(uuid4())
    other = service.add(b, "另一会话")
    barrier = Barrier(2)
    with SQLiteStorage.create_new(tmp_path / "state.sqlite3") as storage:
        def save_and_load(context, items):
            barrier.wait(timeout=5)
            snapshot = sessions.snapshot(context)
            storage.save(context, snapshot, items)
            return storage.load(context)

        with ThreadPoolExecutor(max_workers=2) as pool:
            fa = pool.submit(save_and_load, a, [first, second])
            fb = pool.submit(save_and_load, b, [other])
            assert fa.result(timeout=5).todos == [first, second]
            assert fb.result(timeout=5).todos == [other]
        assert len(storage.load_all()) == 2


def test_sql_like_user_text_remains_data_in_history_and_title(tmp_path, state):
    sessions, context, service, first, second = state
    text = "中文'); DROP TABLE sessions; --"
    changed = service.update(context, first.id, title=text)
    task = sessions.begin(context)
    sessions.append(context, task.task_id, [Message(role="user", content=text)])
    sessions.finish(context, task.task_id, status="completed")
    with SQLiteStorage.create_new(tmp_path / "state.sqlite3") as storage:
        storage.save(context, sessions.snapshot(context), [changed, second])
        assert storage.load(context).todos[0].title == text
        assert storage.load(context).snapshot.messages[0].content == text


@pytest.mark.parametrize("problem", ["restore_deleted", "edit_deleted", "reopen_completed"])
def test_old_or_modified_snapshot_cannot_undo_protected_business_facts(tmp_path, state, problem):
    sessions, context, service, first, second = state
    if problem == "reopen_completed":
        protected = service.complete(context, first.id).item
        invalid = first
    else:
        protected = service.delete(context, first.id).item
        invalid = first if problem == "restore_deleted" else protected.model_copy(update={"title": "不得改写保留内容"})
    snapshot = sessions.snapshot(context)
    with SQLiteStorage.create_new(tmp_path / "state.sqlite3") as storage:
        storage.save(context, snapshot, [protected, second])

        with pytest.raises(ValueError):
            storage.save(context, snapshot, [invalid, second])

        assert storage.load(context).todos == [protected, second]


def test_trusted_load_all_rejects_orphan_rows_instead_of_silently_skipping_them(tmp_path, state):
    _, context, _, first, _ = state
    path = tmp_path / "state.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        execute_sql(path, "INSERT INTO todos VALUES (?, ?, 0, ?)", (
            str(context.session_id), str(first.id), first.model_dump_json(),
        ))

        with pytest.raises(ValueError, match="外键"):
            storage.load_all()
