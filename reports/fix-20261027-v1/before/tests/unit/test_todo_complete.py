from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from pydantic import ValidationError

from min_agent.sessions import ExecutionContext, SessionStore
from min_agent.tools.todo import TodoService


def test_complete_changes_only_selected_record_status_and_preserves_other_fields_and_order():
    sessions = SessionStore()
    context = sessions.create(uuid4())
    service = TodoService(sessions)
    first = service.add(context, "  写周报  ")
    second = service.add(context, "  写周报  ")

    result = service.complete(context, first.id)

    assert result.changed is True
    assert result.item.status == "completed"
    assert result.item.id == first.id
    assert result.item.session_id == first.session_id
    assert result.item.title == first.title == "  写周报  "
    assert first.status == "pending"
    assert service.list_items(context) == [result.item, second]


def test_repeated_complete_reports_no_change_and_keeps_entire_record_list_unchanged():
    sessions = SessionStore()
    context = sessions.create(uuid4())
    service = TodoService(sessions)
    item = service.add(context, "写周报")
    first = service.complete(context, item.id)
    before = service.list_items(context)

    second = service.complete(context, item.id)
    third = service.complete(context, item.id)

    assert first.changed is True
    assert second.changed is third.changed is False
    assert second.item == third.item == first.item
    assert service.list_items(context) == before


@pytest.fixture
def todo_state():
    sessions = SessionStore()
    context = sessions.create(uuid4())
    service = TodoService(sessions)
    first = service.add(context, "已有记录")
    second = service.add(context, "另一个记录")
    return sessions, context, service, first, second


@pytest.mark.parametrize("target", ["unknown", "same_owner_other_session", "other_owner"])
def test_unknown_or_out_of_session_id_cannot_complete_or_create_records(todo_state, target):
    sessions, context, service, first, second = todo_state
    owner = context.owner_id if target == "same_owner_other_session" else uuid4()
    other_context = sessions.create(owner)
    other = service.add(other_context, "其他会话记录")
    target_id = uuid4() if target == "unknown" else other.id

    with pytest.raises(KeyError) as error:
        service.complete(context, target_id)

    assert error.value.args == ("当前会话中未找到todo",)
    assert service.list_items(context) == [first, second]
    assert service.list_items(other_context) == [other]


@pytest.mark.parametrize("item_id", ["uuid-string", None, 123, True, [], {}])
def test_non_uuid_id_is_rejected_without_changing_records(todo_state, item_id):
    _, context, service, first, second = todo_state
    if item_id == "uuid-string":
        item_id = str(first.id)

    with pytest.raises(TypeError, match="item_id 必须是 UUID"):
        service.complete(context, item_id)

    assert service.list_items(context) == [first, second]


@pytest.mark.parametrize("problem", ["foreign_owner", "missing_session", "other_store"])
def test_invalid_access_context_cannot_complete_existing_record(todo_state, problem):
    _, context, service, first, second = todo_state
    contexts = {
        "foreign_owner": ExecutionContext(owner_id=uuid4(), session_id=context.session_id),
        "missing_session": ExecutionContext(owner_id=context.owner_id, session_id=uuid4()),
        "other_store": SessionStore().create(context.owner_id),
    }

    with pytest.raises(PermissionError):
        service.complete(contexts[problem], first.id)

    assert service.list_items(context) == [first, second]


@pytest.mark.parametrize("problem", ["wrong_type", "bypass_validation"])
def test_malformed_context_is_revalidated_before_complete(todo_state, problem):
    _, context, service, first, second = todo_state
    invalid = "not-context" if problem == "wrong_type" else ExecutionContext.model_construct(
        owner_id="not-a-uuid", session_id=context.session_id,
    )

    with pytest.raises((TypeError, ValidationError)):
        service.complete(invalid, first.id)

    assert service.list_items(context) == [first, second]


def test_owner_is_checked_before_id_validation(todo_state):
    _, context, service, first, second = todo_state
    foreign = ExecutionContext(owner_id=uuid4(), session_id=context.session_id)

    with pytest.raises(PermissionError):
        service.complete(foreign, [])

    assert service.list_items(context) == [first, second]


def test_already_completed_record_still_requires_valid_owner(todo_state):
    _, context, service, first, second = todo_state
    result = service.complete(context, first.id)
    foreign = ExecutionContext(owner_id=uuid4(), session_id=context.session_id)

    with pytest.raises(PermissionError):
        service.complete(foreign, first.id)

    assert service.list_items(context) == [result.item, second]


def test_valid_empty_session_cannot_complete_record_from_populated_session(todo_state):
    sessions, context, service, first, second = todo_state
    empty = sessions.create(context.owner_id)

    with pytest.raises(KeyError, match="当前会话中未找到todo"):
        service.complete(empty, first.id)

    assert service.list_items(empty) == []
    assert service.list_items(context) == [first, second]


def test_renaming_completed_record_keeps_status_and_repeat_returns_current_title(todo_state):
    _, context, service, first, second = todo_state
    completed = service.complete(context, first.id)

    renamed = service.update(context, first.id, title="完成后改标题")
    repeated = service.complete(context, first.id)

    assert renamed.status == "completed"
    assert repeated.changed is False
    assert repeated.item == renamed
    assert repeated.item.id == first.id
    assert completed.item.title == first.title
    assert service.list_items(context) == [renamed, second]


def test_result_and_item_are_frozen_and_local_copies_do_not_change_saved_state(todo_state):
    _, context, service, first, second = todo_state
    before = service.list_items(context)
    result = service.complete(context, first.id)

    with pytest.raises(ValidationError):
        result.changed = False
    with pytest.raises(ValidationError):
        result.item.status = "pending"
    local = result.model_copy(update={"item": result.item.model_copy(update={"status": "pending"})})

    assert local.item.status == "pending"
    assert before == [first, second]
    assert service.list_items(context) == [result.item, second]
    assert service.complete(context, first.id).changed is False


def test_complete_and_repeat_leave_session_history_and_running_task_unchanged(todo_state):
    sessions, context, service, first, second = todo_state
    sessions.begin(context)
    before = sessions.snapshot(context)

    result = service.complete(context, first.id)
    service.complete(context, first.id)

    assert sessions.snapshot(context) == before
    assert before.task.status == "running"
    assert service.list_items(context) == [result.item, second]


def test_two_concurrent_complete_calls_report_exactly_one_state_change(todo_state):
    _, context, service, first, second = todo_state
    barrier = Barrier(2)

    def complete_item():
        barrier.wait(timeout=5)
        return service.complete(context, first.id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(complete_item) for _ in range(2)]
        results = [future.result(timeout=5) for future in futures]

    assert sorted(result.changed for result in results) == [False, True]
    assert results[0].item == results[1].item
    assert results[0].item.status == "completed"
    assert results[0].item.id == first.id
    assert service.list_items(context) == [results[0].item, second]
