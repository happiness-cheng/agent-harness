from uuid import UUID, uuid4
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from pydantic import ValidationError

from min_agent.sessions import ExecutionContext, SessionStore
from min_agent.tools.todo import TodoService


def test_added_todo_can_be_queried_with_real_id_session_status_and_original_title():
    sessions = SessionStore()
    context = sessions.create(uuid4())
    service = TodoService(sessions)

    assert service.list_items(context) == []
    item = service.add(context, "  写周报  ")

    assert isinstance(item.id, UUID)
    assert item.session_id == context.session_id
    assert item.title == "  写周报  "
    assert item.status == "pending"
    assert service.list_items(context) == [item]


def test_same_title_is_two_distinct_records_and_list_keeps_insertion_order():
    sessions = SessionStore()
    context = sessions.create(uuid4())
    service = TodoService(sessions)

    first = service.add(context, "写周报")
    second = service.add(context, "写周报")
    third = service.add(context, "读文档")

    assert len({first.id, second.id, third.id}) == 3
    assert service.list_items(context) == [first, second, third]


def test_same_owner_sessions_and_other_owner_session_have_separate_records():
    sessions = SessionStore()
    owner = uuid4()
    a, b, c = sessions.create(owner), sessions.create(owner), sessions.create(uuid4())
    service = TodoService(sessions)

    item_a = service.add(a, "同名任务")
    assert service.list_items(b) == []
    item_b = service.add(b, "同名任务")
    item_c = service.add(c, "第三个会话")

    assert service.list_items(a) == [item_a]
    assert service.list_items(b) == [item_b]
    assert service.list_items(c) == [item_c]
    assert len({item_a.id, item_b.id, item_c.id}) == 3
    assert [item_a.session_id, item_b.session_id, item_c.session_id] == [a.session_id, b.session_id, c.session_id]


@pytest.mark.parametrize("title", ["", " ", "\n\t", "题" * 201, None, 123, True, b"bytes", [], {}])
def test_invalid_title_is_rejected_without_adding_a_record(title):
    sessions = SessionStore()
    context = sessions.create(uuid4())
    service = TodoService(sessions)
    existing = service.add(context, "已有记录")

    with pytest.raises(ValidationError):
        service.add(context, title)

    assert service.list_items(context) == [existing]


def test_title_accepts_exactly_two_hundred_unicode_characters():
    sessions = SessionStore()
    context = sessions.create(uuid4())
    service = TodoService(sessions)

    item = service.add(context, "题" * 200)

    assert item.title == "题" * 200
    assert service.list_items(context) == [item]


@pytest.mark.parametrize("operation", ["add", "list"])
@pytest.mark.parametrize("problem", ["foreign_owner", "missing_session", "other_store"])
def test_unauthorized_or_unknown_context_cannot_read_or_write(operation, problem):
    sessions = SessionStore()
    valid = sessions.create(uuid4())
    service = TodoService(sessions)
    existing = service.add(valid, "已有记录")
    contexts = {
        "foreign_owner": ExecutionContext(owner_id=uuid4(), session_id=valid.session_id),
        "missing_session": ExecutionContext(owner_id=valid.owner_id, session_id=uuid4()),
        "other_store": SessionStore().create(valid.owner_id),
    }

    with pytest.raises(PermissionError):
        if operation == "add":
            service.add(contexts[problem], "不应写入")
        else:
            service.list_items(contexts[problem])

    assert service.list_items(valid) == [existing]


@pytest.mark.parametrize("operation", ["add", "list"])
@pytest.mark.parametrize("problem", ["wrong_type", "bypass_validation"])
def test_invalid_context_is_revalidated_before_access(operation, problem):
    sessions = SessionStore()
    valid = sessions.create(uuid4())
    service = TodoService(sessions)
    context = "not-context" if problem == "wrong_type" else ExecutionContext.model_construct(
        owner_id="not-a-uuid", session_id=valid.session_id,
    )

    with pytest.raises((TypeError, ValidationError)):
        if operation == "add":
            service.add(context, "不应写入")
        else:
            service.list_items(context)

    assert service.list_items(valid) == []


def test_owner_check_precedes_title_validation():
    sessions = SessionStore()
    valid = sessions.create(uuid4())
    service = TodoService(sessions)
    foreign = ExecutionContext(owner_id=uuid4(), session_id=valid.session_id)

    with pytest.raises(PermissionError):
        service.add(foreign, None)

    assert service.list_items(valid) == []


def test_returned_lists_and_item_copies_cannot_modify_saved_records():
    sessions = SessionStore()
    context = sessions.create(uuid4())
    service = TodoService(sessions)
    item = service.add(context, "保存原文")

    with pytest.raises(ValidationError):
        item.title = "不应生效"
    items = service.list_items(context)
    items[0] = items[0].model_copy(update={"title": "只改副本"})
    items.clear()

    assert service.list_items(context) == [item]
    assert service.list_items(context)[0].title == "保存原文"


def test_todo_access_does_not_change_session_history_or_running_task():
    sessions = SessionStore()
    context = sessions.create(uuid4())
    task = sessions.begin(context)
    before = sessions.snapshot(context)
    service = TodoService(sessions)

    assert service.list_items(context) == []
    item = service.add(context, "业务记录")
    assert service.list_items(context) == [item]

    assert sessions.snapshot(context) == before
    assert sessions.snapshot(context).task.task_id == task.task_id
    assert sessions.snapshot(context).task.status == "running"


def test_service_instances_have_separate_in_memory_lifetimes():
    sessions = SessionStore()
    context = sessions.create(uuid4())
    first, second = TodoService(sessions), TodoService(sessions)
    item = first.add(context, "第一实例")

    assert second.list_items(context) == []
    assert first.list_items(context) == [item]


def test_concurrent_same_title_additions_are_not_lost_or_merged():
    sessions = SessionStore()
    context = sessions.create(uuid4())
    service = TodoService(sessions)
    barrier = Barrier(2)

    def add_items():
        barrier.wait(timeout=5)
        return [service.add(context, "同名并发记录") for _ in range(20)]

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(add_items) for _ in range(2)]
        results = [future.result(timeout=5) for future in futures]

    expected_ids = {item.id for batch in results for item in batch}
    actual = service.list_items(context)
    assert len(actual) == len(expected_ids) == 40
    assert {item.id for item in actual} == expected_ids
    assert all(item.title == "同名并发记录" for item in actual)
