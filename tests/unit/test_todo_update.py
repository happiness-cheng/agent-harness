from uuid import uuid4

import pytest
from pydantic import ValidationError

from min_agent.sessions import ExecutionContext, SessionStore
from min_agent.tools.todo import TodoService


def test_update_changes_only_selected_title_and_preserves_other_records_and_order():
    sessions = SessionStore()
    context = sessions.create(uuid4())
    service = TodoService(sessions)
    first = service.add(context, "写周报")
    second = service.add(context, "写周报")

    updated = service.update(context, first.id, title="  写月报  ")

    assert updated.title == "  写月报  "
    assert updated.id == first.id
    assert updated.session_id == first.session_id
    assert updated.status == first.status == "pending"
    assert first.title == "写周报"
    assert service.list_items(context) == [updated, second]


@pytest.fixture
def todo_state():
    sessions = SessionStore()
    context = sessions.create(uuid4())
    service = TodoService(sessions)
    first = service.add(context, "已有记录")
    second = service.add(context, "另一个记录")
    return sessions, context, service, first, second


@pytest.mark.parametrize("title", ["", " ", "\n\t", "题" * 201, None, 123, True, b"bytes", [], {}])
def test_invalid_title_leaves_target_and_other_records_unchanged(todo_state, title):
    _, context, service, first, second = todo_state

    with pytest.raises(ValidationError):
        service.update(context, first.id, title=title)

    assert service.list_items(context) == [first, second]


def test_updated_title_accepts_exactly_two_hundred_unicode_characters(todo_state):
    _, context, service, first, second = todo_state

    updated = service.update(context, first.id, title="题" * 200)

    assert updated.title == "题" * 200
    assert service.list_items(context) == [updated, second]


@pytest.mark.parametrize("target", ["unknown", "same_owner_other_session", "other_owner"])
def test_unknown_or_out_of_session_id_is_rejected_without_creating_or_changing_records(todo_state, target):
    sessions, context, service, first, second = todo_state
    owner = context.owner_id if target == "same_owner_other_session" else uuid4()
    other_context = sessions.create(owner)
    other = service.add(other_context, "其他会话的记录")
    target_id = uuid4() if target == "unknown" else other.id

    with pytest.raises(KeyError) as error:
        service.update(context, target_id, title="不得写入")

    assert error.value.args == ("当前会话中未找到todo",)
    assert service.list_items(context) == [first, second]
    assert service.list_items(other_context) == [other]


@pytest.mark.parametrize("item_id", ["uuid-string", None, 123, True, [], {}])
def test_non_uuid_id_is_rejected_with_clear_type_error_and_no_write(todo_state, item_id):
    _, context, service, first, second = todo_state
    if item_id == "uuid-string":
        item_id = str(first.id)

    with pytest.raises(TypeError, match="item_id 必须是 UUID"):
        service.update(context, item_id, title="不得写入")

    assert service.list_items(context) == [first, second]


@pytest.mark.parametrize("problem", ["foreign_owner", "missing_session", "other_store"])
def test_invalid_access_context_cannot_update_existing_record(todo_state, problem):
    _, context, service, first, second = todo_state
    contexts = {
        "foreign_owner": ExecutionContext(owner_id=uuid4(), session_id=context.session_id),
        "missing_session": ExecutionContext(owner_id=context.owner_id, session_id=uuid4()),
        "other_store": SessionStore().create(context.owner_id),
    }

    with pytest.raises(PermissionError):
        service.update(contexts[problem], first.id, title="不得写入")

    assert service.list_items(context) == [first, second]


@pytest.mark.parametrize("problem", ["wrong_type", "bypass_validation"])
def test_malformed_context_is_revalidated_before_update(todo_state, problem):
    _, context, service, first, second = todo_state
    invalid = "not-context" if problem == "wrong_type" else ExecutionContext.model_construct(
        owner_id="not-a-uuid", session_id=context.session_id,
    )

    with pytest.raises((TypeError, ValidationError)):
        service.update(invalid, first.id, title="不得写入")

    assert service.list_items(context) == [first, second]


def test_owner_is_checked_before_id_and_title_validation(todo_state):
    _, context, service, first, second = todo_state
    foreign = ExecutionContext(owner_id=uuid4(), session_id=context.session_id)

    with pytest.raises(PermissionError):
        service.update(foreign, [], title=None)

    assert service.list_items(context) == [first, second]


def test_unknown_id_does_not_validate_payload_or_upsert(todo_state):
    _, context, service, first, second = todo_state

    with pytest.raises(KeyError, match="当前会话中未找到todo"):
        service.update(context, uuid4(), title=None)

    assert service.list_items(context) == [first, second]


@pytest.mark.parametrize("field,value", [("id", uuid4()), ("session_id", uuid4()), ("status", "completed")])
def test_protected_fields_are_not_accepted_as_update_arguments(todo_state, field, value):
    _, context, service, first, second = todo_state

    with pytest.raises(TypeError):
        service.update(context, first.id, title="不应部分写入", **{field: value})

    assert service.list_items(context) == [first, second]


def test_title_is_required_instead_of_replacing_record_with_defaults(todo_state):
    _, context, service, first, second = todo_state

    with pytest.raises(TypeError):
        service.update(context, first.id)

    assert service.list_items(context) == [first, second]


def test_same_title_and_repeated_update_do_not_merge_or_add_records(todo_state):
    _, context, service, first, second = todo_state

    updated = service.update(context, first.id, title=second.title)
    repeated = service.update(context, first.id, title=second.title)

    assert repeated == updated
    assert updated.id != second.id
    assert service.list_items(context) == [updated, second]


def test_update_returns_frozen_copy_and_does_not_mutate_previous_snapshot(todo_state):
    _, context, service, first, second = todo_state
    before = service.list_items(context)
    updated = service.update(context, first.id, title="新标题")

    with pytest.raises(ValidationError):
        updated.title = "不应生效"
    local = updated.model_copy(update={"title": None})

    assert local.title is None
    assert before == [first, second]
    assert service.list_items(context) == [updated, second]
    assert service.list_items(context)[0].title == "新标题"


def test_update_does_not_change_session_history_or_running_task(todo_state):
    sessions, context, service, first, second = todo_state
    sessions.begin(context)
    before = sessions.snapshot(context)

    updated = service.update(context, first.id, title="仅修改业务标题")

    assert sessions.snapshot(context) == before
    assert service.list_items(context) == [updated, second]
