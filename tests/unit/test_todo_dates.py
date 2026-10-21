from datetime import UTC, date, datetime
from uuid import uuid4
from zoneinfo import ZoneInfoNotFoundError

import pytest
from pydantic import ValidationError

from min_agent.clock import Clock
from min_agent.sessions import ExecutionContext, SessionStore
from min_agent.tools.todo import TodoItem, TodoService


def test_tomorrow_uses_user_local_date_and_is_saved_as_absolute_date():
    clock = Clock(now=lambda: datetime(2026, 10, 20, 16, tzinfo=UTC))
    sessions = SessionStore()
    context = sessions.create(uuid4())
    service = TodoService(sessions, clock=clock)

    item = service.add(context, "写周报", due_date="明天")

    assert clock.today() == date(2026, 10, 21)
    assert item.due_date == date(2026, 10, 22)
    assert service.list_items(context) == [item]
    assert item.status == "pending"
    assert item.deleted is False


class ManualTime:
    def __init__(self, instant):
        self.instant = instant
        self.reads = 0

    def __call__(self):
        self.reads += 1
        return self.instant


def test_due_today_is_valid_until_local_next_day_and_projection_does_not_write_state():
    now = ManualTime(datetime(2026, 10, 20, 15, 59, 59, tzinfo=UTC))
    sessions = SessionStore()
    context = sessions.create(uuid4())
    service = TodoService(sessions, clock=Clock(now=now))
    due = service.add(context, "当天截止", due_date=date(2026, 10, 20))
    indefinite = service.add(context, "没有截止")
    before = service.list_items(context)

    today = service.list_views(context)
    now.instant = datetime(2026, 10, 20, 16, tzinfo=UTC)
    tomorrow = service.list_views(context)

    assert [view.item for view in today] == [due, indefinite]
    assert [view.overdue for view in today] == [False, False]
    assert [view.overdue for view in tomorrow] == [True, False]
    assert tomorrow[0].item.status == "pending"
    assert service.list_items(context) == before
    assert due.due_date == date(2026, 10, 20)


def test_completed_items_never_become_overdue_and_deleted_items_are_not_projected():
    now = ManualTime(datetime(2026, 10, 20, tzinfo=UTC))
    sessions = SessionStore()
    context = sessions.create(uuid4())
    service = TodoService(sessions, clock=Clock(now=now))
    past = service.add(context, "已完成", due_date="2026-10-01")
    removed = service.add(context, "已删除", due_date="2026-10-01")
    completed = service.complete(context, past.id)
    service.delete(context, removed.id)

    first = service.list_views(context)
    now.instant = datetime(2027, 1, 1, tzinfo=UTC)
    later = service.list_views(context)

    assert len(first) == len(later) == 1
    assert first[0].item == later[0].item == completed.item
    assert first[0].overdue is later[0].overdue is False
    assert completed.item.due_date == date(2026, 10, 1)


def test_date_only_patch_omission_and_explicit_none_have_distinct_meanings():
    sessions = SessionStore()
    context = sessions.create(uuid4())
    service = TodoService(sessions)
    item = service.add(context, "原标题", due_date="2026-10-20")
    other = service.add(context, "其他记录")

    rescheduled = service.update(context, item.id, due_date="2026-10-25")
    renamed = service.update(context, item.id, title="新标题")
    cleared = service.update(context, item.id, due_date=None)

    assert rescheduled.title == item.title
    assert rescheduled.due_date == renamed.due_date == date(2026, 10, 25)
    assert renamed.title == cleared.title == "新标题"
    assert cleared.due_date is None
    assert cleared.id == item.id
    assert cleared.session_id == item.session_id
    assert cleared.status == "pending"
    assert cleared.deleted is False
    assert service.list_items(context) == [cleared, other]


def test_title_and_relative_date_patch_can_be_applied_together():
    clock = Clock(now=lambda: datetime(2026, 10, 20, 16, tzinfo=UTC))
    sessions = SessionStore()
    context = sessions.create(uuid4())
    service = TodoService(sessions, clock=clock)
    item = service.add(context, "原标题", due_date="2026-10-01")

    updated = service.update(context, item.id, title="新标题", due_date="明天")

    assert updated.title == "新标题"
    assert updated.due_date == date(2026, 10, 22)
    assert updated.id == item.id
    assert service.list_items(context) == [updated]


@pytest.mark.parametrize("title,due,error", [(None, "2026-10-25", ValidationError), ("新标题", "不是日期", ValueError)])
def test_invalid_combined_patch_leaves_all_fields_and_other_records_unchanged(title, due, error):
    sessions = SessionStore()
    context = sessions.create(uuid4())
    service = TodoService(sessions)
    item = service.add(context, "原标题", due_date="2026-10-20")
    other = service.add(context, "其他记录")

    with pytest.raises(error):
        service.update(context, item.id, title=title, due_date=due)

    assert service.list_items(context) == [item, other]


@pytest.fixture
def dated_state():
    now = ManualTime(datetime(2026, 10, 20, 12, tzinfo=UTC))
    sessions = SessionStore()
    context = sessions.create(uuid4())
    service = TodoService(sessions, clock=Clock(now=now))
    return sessions, context, service, now


@pytest.mark.parametrize("value,expected", [
    (None, None), (date(2026, 10, 20), date(2026, 10, 20)),
    ("2026-10-20", date(2026, 10, 20)), ("2028-02-29", date(2028, 2, 29)),
    ("今天", date(2026, 10, 20)), ("明天", date(2026, 10, 21)), ("后天", date(2026, 10, 22)),
])
def test_supported_date_inputs_are_saved_as_absolute_dates(dated_state, value, expected):
    _, context, service, _ = dated_state

    item = service.add(context, "日期输入", due_date=value)

    assert item.due_date == expected
    assert service.list_items(context) == [item]


@pytest.mark.parametrize("value", [
    "", "2026-02-29", "2026-13-01", "20261020", "2026-W43-2", " 2026-10-20",
    "2026-10-20T00:00:00", "下周五", True, 123, [], {},
    datetime(2026, 10, 20, tzinfo=UTC), datetime(2026, 10, 20),
])
def test_invalid_or_ambiguous_date_input_does_not_add_records(dated_state, value):
    _, context, service, _ = dated_state
    existing = service.add(context, "已有记录", due_date="2026-10-20")

    with pytest.raises((TypeError, ValueError)):
        service.add(context, "不得写入", due_date=value)

    assert service.list_items(context) == [existing]


@pytest.mark.parametrize("value,error", [
    (datetime(2026, 10, 20), ValueError), (date(2026, 10, 20), TypeError),
    ("2026-10-20", TypeError), (None, TypeError), (True, TypeError),
])
def test_invalid_clock_does_not_silently_use_host_time_or_partially_write(value, error):
    sessions = SessionStore()
    context = sessions.create(uuid4())
    service = TodoService(sessions, clock=Clock(now=lambda: value))
    existing = service.add(context, "原记录", due_date="2026-10-20")

    with pytest.raises(error):
        service.add(context, "不得写入", due_date="明天")
    with pytest.raises(error):
        service.update(context, existing.id, title="不得部分写入", due_date="今天")
    with pytest.raises(error):
        service.list_views(context)

    assert service.list_items(context) == [existing]


@pytest.mark.parametrize("zone,instant,expected", [
    ("Asia/Shanghai", datetime(2026, 10, 20, 15, 59, 59, tzinfo=UTC), date(2026, 10, 20)),
    ("Asia/Shanghai", datetime(2026, 10, 20, 16, tzinfo=UTC), date(2026, 10, 21)),
    ("America/Los_Angeles", datetime(2026, 10, 20, 6, 59, 59, tzinfo=UTC), date(2026, 10, 19)),
    ("America/Los_Angeles", datetime(2026, 10, 20, 7, tzinfo=UTC), date(2026, 10, 20)),
    ("Pacific/Kiritimati", datetime(2026, 10, 20, 10, tzinfo=UTC), date(2026, 10, 21)),
])
def test_current_date_uses_configured_iana_zone_instead_of_host_date(zone, instant, expected):
    assert Clock(time_zone=zone, now=lambda: instant).today() == expected


def test_tomorrow_is_calendar_day_across_daylight_saving_transition():
    now = ManualTime(datetime(2026, 3, 8, 7, 30, tzinfo=UTC))
    clock = Clock(time_zone="America/Los_Angeles", now=now)
    sessions = SessionStore()
    context = sessions.create(uuid4())
    service = TodoService(sessions, clock=clock)
    item = service.add(context, "跨夏令时日期", due_date="明天")
    assert item.due_date == date(2026, 3, 8)

    now.instant = datetime(2026, 3, 9, 6, 59, 59, tzinfo=UTC)
    assert service.list_views(context)[0].overdue is False
    now.instant = datetime(2026, 3, 9, 7, tzinfo=UTC)
    assert service.list_views(context)[0].overdue is True
    assert service.list_items(context) == [item]


def test_one_projection_batch_reads_today_once_for_consistent_display(dated_state):
    _, context, service, now = dated_state
    items = [service.add(context, "同批记录", due_date="2026-10-19") for _ in range(3)]
    before = now.reads

    views = service.list_views(context)

    assert now.reads == before + 1
    assert [view.item for view in views] == items
    assert [view.overdue for view in views] == [True, True, True]


def test_pending_future_deadline_is_not_overdue(dated_state):
    _, context, service, now = dated_state
    item = service.add(context, "尚未截止", due_date="2026-10-22")

    assert service.list_views(context)[0].overdue is False
    now.instant = datetime(2026, 10, 21, 12, tzinfo=UTC)
    assert service.list_views(context)[0].overdue is False
    assert service.list_items(context) == [item]


@pytest.mark.parametrize("operation", ["add", "update", "list_views"])
def test_new_date_operations_check_owner_before_reading_clock(dated_state, operation):
    _, context, service, now = dated_state
    existing = service.add(context, "原记录", due_date="2026-10-20")
    foreign = ExecutionContext(owner_id=uuid4(), session_id=context.session_id)
    before = now.reads

    with pytest.raises(PermissionError):
        if operation == "add":
            service.add(foreign, "不得写入", due_date="今天")
        elif operation == "update":
            service.update(foreign, existing.id, due_date="明天")
        else:
            service.list_views(foreign)

    assert now.reads == before
    assert service.list_items(context) == [existing]


@pytest.mark.parametrize("problem", ["unknown", "deleted", "other_session"])
def test_date_patch_locates_live_record_before_parsing_relative_input(dated_state, problem):
    sessions, context, service, now = dated_state
    item = service.add(context, "原记录", due_date="2026-10-20")
    other_context = sessions.create(context.owner_id)
    other = service.add(other_context, "其他会话", due_date="2026-10-21")
    if problem == "deleted":
        service.delete(context, item.id)
        target = item.id
    else:
        target = other.id if problem == "other_session" else uuid4()
    before = service.list_items(context)
    now.instant = None

    with pytest.raises(KeyError, match="当前会话中未找到todo"):
        service.update(context, target, title="不得写入", due_date="明天")

    assert service.list_items(context) == before
    assert service.list_items(other_context) == [other]


def test_record_json_round_trip_keeps_absolute_date_without_derived_overdue_field(dated_state):
    _, context, service, now = dated_state
    item = service.add(context, "日期存储", due_date="明天")
    now.instant = datetime(2026, 10, 30, tzinfo=UTC)

    assert service.list_views(context)[0].overdue is True
    assert item.model_dump(mode="json")["due_date"] == "2026-10-21"
    assert "overdue" not in item.model_dump()
    assert TodoItem.model_validate_json(item.model_dump_json()) == item


def test_completed_date_patch_and_deletion_keep_due_date_and_do_not_reopen_record(dated_state):
    _, context, service, now = dated_state
    item = service.add(context, "已完成", due_date="2026-10-01")
    completed = service.complete(context, item.id)
    updated = service.update(context, item.id, due_date="2026-10-25")
    now.instant = datetime(2027, 1, 1, tzinfo=UTC)

    assert updated.status == "completed"
    assert service.list_views(context)[0].overdue is False
    assert completed.item.due_date == date(2026, 10, 1)
    assert service.complete(context, item.id).changed is False
    deleted = service.delete(context, item.id)
    assert deleted.item.due_date == date(2026, 10, 25)
    assert service.delete(context, item.id).item == deleted.item
    assert service.list_views(context) == []


def test_frozen_projection_and_local_copies_cannot_change_stored_due_date(dated_state):
    _, context, service, _ = dated_state
    item = service.add(context, "原截止", due_date="2026-10-01")
    view = service.list_views(context)[0]

    with pytest.raises(ValidationError):
        view.overdue = False
    with pytest.raises(ValidationError):
        view.item.due_date = None
    local = view.model_copy(update={"item": view.item.model_copy(update={"due_date": None})})

    assert local.item.due_date is None
    assert service.list_items(context) == [item]
    assert service.list_views(context)[0].overdue is True


def test_date_operations_do_not_change_session_history_or_running_task(dated_state):
    sessions, context, service, _ = dated_state
    sessions.begin(context)
    before = sessions.snapshot(context)

    item = service.add(context, "业务日期", due_date="今天")
    service.update(context, item.id, due_date="明天")
    service.list_views(context)

    assert sessions.snapshot(context) == before
    assert before.task.status == "running"


@pytest.mark.parametrize("zone,error", [("Not/A_Real_Zone", ZoneInfoNotFoundError), ("", ValueError)])
def test_invalid_time_zone_is_rejected_without_falling_back_to_host_zone(zone, error):
    with pytest.raises(error):
        Clock(time_zone=zone)
