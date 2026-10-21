from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from pydantic import ValidationError

from min_agent.sessions import ExecutionContext, SessionStore
from min_agent.tools.todo import TodoService


def test_delete_hides_only_selected_same_title_record_and_preserves_original_fields():
    sessions = SessionStore()
    context = sessions.create(uuid4())
    service = TodoService(sessions)
    first = service.add(context, "  写周报  ")
    second = service.add(context, "  写周报  ")

    result = service.delete(context, first.id)

    assert result.changed is True
    assert result.item.deleted is True
    assert result.item.id == first.id
    assert result.item.session_id == first.session_id
    assert result.item.title == first.title == "  写周报  "
    assert result.item.status == first.status == "pending"
    assert first.deleted is False
    assert second.deleted is False
    assert service.list_items(context) == [second]


def test_repeated_delete_returns_no_change_and_preserves_saved_deleted_record():
    sessions = SessionStore()
    context = sessions.create(uuid4())
    service = TodoService(sessions)
    item = service.add(context, "写周报")
    first = service.delete(context, item.id)

    second = service.delete(context, item.id)
    third = service.delete(context, item.id)

    assert first.changed is True
    assert second.changed is third.changed is False
    assert second.item == third.item == first.item
    assert service.list_items(context) == []


@pytest.mark.parametrize("status", ["pending", "completed"])
@pytest.mark.parametrize("operation", ["update", "complete"])
def test_deleted_record_cannot_be_modified_or_completed_in_either_original_status(status, operation):
    sessions = SessionStore()
    context = sessions.create(uuid4())
    service = TodoService(sessions)
    item = service.add(context, "保留原文")
    other = service.add(context, "其他记录")
    if status == "completed":
        service.complete(context, item.id)
    deleted = service.delete(context, item.id)

    with pytest.raises(KeyError) as error:
        if operation == "update":
            service.update(context, item.id, title="不得写入")
        else:
            service.complete(context, item.id)

    assert error.value.args == ("当前会话中未找到todo",)
    repeated = service.delete(context, item.id)
    assert repeated.changed is False
    assert repeated.item == deleted.item
    assert repeated.item.status == status
    assert service.list_items(context) == [other]


@pytest.fixture
def todo_state():
    sessions = SessionStore()
    context = sessions.create(uuid4())
    service = TodoService(sessions)
    first = service.add(context, "已有记录")
    second = service.add(context, "另一个记录")
    return sessions, context, service, first, second


@pytest.mark.parametrize("target", ["unknown", "same_owner_other_session", "other_owner"])
def test_unknown_or_out_of_session_id_cannot_delete_or_create_records(todo_state, target):
    sessions, context, service, first, second = todo_state
    owner = context.owner_id if target == "same_owner_other_session" else uuid4()
    other_context = sessions.create(owner)
    other = service.add(other_context, "其他会话记录")
    target_id = uuid4() if target == "unknown" else other.id

    with pytest.raises(KeyError) as error:
        service.delete(context, target_id)

    assert error.value.args == ("当前会话中未找到todo",)
    assert service.list_items(context) == [first, second]
    assert service.list_items(other_context) == [other]


@pytest.mark.parametrize("item_id", ["uuid-string", None, 123, True, [], {}])
def test_non_uuid_id_is_rejected_without_changing_records(todo_state, item_id):
    _, context, service, first, second = todo_state
    if item_id == "uuid-string":
        item_id = str(first.id)

    with pytest.raises(TypeError, match="item_id 必须是 UUID"):
        service.delete(context, item_id)

    assert service.list_items(context) == [first, second]


@pytest.mark.parametrize("problem", ["foreign_owner", "missing_session", "other_store"])
def test_invalid_access_context_cannot_delete_existing_record(todo_state, problem):
    _, context, service, first, second = todo_state
    contexts = {
        "foreign_owner": ExecutionContext(owner_id=uuid4(), session_id=context.session_id),
        "missing_session": ExecutionContext(owner_id=context.owner_id, session_id=uuid4()),
        "other_store": SessionStore().create(context.owner_id),
    }

    with pytest.raises(PermissionError):
        service.delete(contexts[problem], first.id)

    assert service.list_items(context) == [first, second]


@pytest.mark.parametrize("problem", ["wrong_type", "bypass_validation"])
def test_malformed_context_is_revalidated_before_delete(todo_state, problem):
    _, context, service, first, second = todo_state
    invalid = "not-context" if problem == "wrong_type" else ExecutionContext.model_construct(
        owner_id="not-a-uuid", session_id=context.session_id,
    )

    with pytest.raises((TypeError, ValidationError)):
        service.delete(invalid, first.id)

    assert service.list_items(context) == [first, second]


@pytest.mark.parametrize("operation", ["delete", "update", "complete"])
def test_access_to_already_deleted_record_still_checks_owner(todo_state, operation):
    _, context, service, first, second = todo_state
    deleted = service.delete(context, first.id)
    foreign = ExecutionContext(owner_id=uuid4(), session_id=context.session_id)

    with pytest.raises(PermissionError):
        if operation == "update":
            service.update(foreign, first.id, title="不得写入")
        else:
            getattr(service, operation)(foreign, first.id)

    assert service.delete(context, first.id).item == deleted.item
    assert service.list_items(context) == [second]


def test_owner_is_checked_before_id_validation(todo_state):
    _, context, service, first, second = todo_state
    foreign = ExecutionContext(owner_id=uuid4(), session_id=context.session_id)

    with pytest.raises(PermissionError):
        service.delete(foreign, [])

    assert service.list_items(context) == [first, second]


def test_valid_empty_session_cannot_delete_record_from_populated_session(todo_state):
    sessions, context, service, first, second = todo_state
    empty = sessions.create(context.owner_id)

    with pytest.raises(KeyError, match="当前会话中未找到todo"):
        service.delete(empty, first.id)

    assert service.list_items(empty) == []
    assert service.list_items(context) == [first, second]


def test_deleted_record_is_rejected_before_invalid_title_validation(todo_state):
    _, context, service, first, second = todo_state
    deleted = service.delete(context, first.id)

    with pytest.raises(KeyError, match="当前会话中未找到todo"):
        service.update(context, first.id, title=None)

    assert service.delete(context, first.id).item == deleted.item
    assert service.list_items(context) == [second]


def test_frozen_result_and_local_copies_do_not_restore_deleted_record(todo_state):
    _, context, service, first, second = todo_state
    before = service.list_items(context)
    result = service.delete(context, first.id)

    with pytest.raises(ValidationError):
        result.changed = False
    with pytest.raises(ValidationError):
        result.item.deleted = False
    local = result.model_copy(update={"item": result.item.model_copy(update={"deleted": False})})

    assert local.item.deleted is False
    assert before == [first, second]
    assert service.delete(context, first.id).item == result.item
    assert service.list_items(context) == [second]


def test_adding_same_title_after_delete_creates_distinct_live_record(todo_state):
    _, context, service, first, second = todo_state
    deleted = service.delete(context, first.id)

    new = service.add(context, first.title)

    assert new.id != first.id
    assert new.deleted is False
    assert new.status == "pending"
    assert service.delete(context, first.id).item == deleted.item
    assert service.list_items(context) == [second, new]


def test_delete_and_repeat_do_not_change_session_history_or_running_task(todo_state):
    sessions, context, service, first, second = todo_state
    sessions.begin(context)
    before = sessions.snapshot(context)

    service.delete(context, first.id)
    service.delete(context, first.id)

    assert sessions.snapshot(context) == before
    assert before.task.status == "running"
    assert service.list_items(context) == [second]


def test_two_concurrent_deletes_report_exactly_one_change(todo_state):
    _, context, service, first, second = todo_state
    barrier = Barrier(2)

    def delete_item():
        barrier.wait(timeout=5)
        return service.delete(context, first.id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(delete_item) for _ in range(2)]
        results = [future.result(timeout=5) for future in futures]

    assert sorted(result.changed for result in results) == [False, True]
    assert results[0].item == results[1].item
    assert results[0].item.deleted is True
    assert service.list_items(context) == [second]


def test_deleting_middle_record_preserves_surviving_order_and_completed_items(todo_state):
    _, context, service, first, second = todo_state
    third = service.add(context, "最后一条")
    completed = service.complete(context, first.id)

    service.delete(context, second.id)

    assert service.list_items(context) == [completed.item, third]
    assert completed.item.deleted is False
    assert completed.item.status == "completed"
