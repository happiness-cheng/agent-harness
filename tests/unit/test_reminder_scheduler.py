from datetime import UTC, datetime, timedelta
from uuid import uuid4
import pytest

from min_agent.notifications import Notifications
from min_agent.scheduler import Scheduler
from min_agent.tools.reminder import ReminderService
from min_agent.sessions import SessionStore
from min_agent.storage import SQLiteStorage
from min_agent.tools.todo import TodoService


def test_missing_time_and_cross_session_link_refused_and_due_is_separate():
    sessions = SessionStore(); context = sessions.create(uuid4()); other = sessions.create(context.owner_id)
    todos = TodoService(sessions); item = todos.add(context, "任务", due_date="2026-10-22")
    reminders = ReminderService(sessions)
    with pytest.raises(ValueError): reminders.add(context, "提醒", None)
    with pytest.raises(PermissionError): reminders.add(other, "跨会话", "2026-10-22T10:00:00+08:00", todo_id=item.id)
    assert reminders.list_items(context) == []


def test_restart_overdue_generates_one_persistent_event_both_channels_and_unread(tmp_path):
    path = tmp_path / "reminders.sqlite3"
    due = datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
    with SQLiteStorage.create_new(path) as storage:
        sessions = SessionStore(storage=storage); context = sessions.create(uuid4())
        reminder = ReminderService(sessions).add(context, "合成提醒", due)
    with SQLiteStorage.open(path) as storage:
        sessions = SessionStore(storage=storage)
        delivered = []
        scheduler = Scheduler(sessions, now=lambda: due + timedelta(hours=1), desktop=lambda event: delivered.append(event))
        scheduler.tick(); scheduler.tick()
        events = Notifications(sessions).list_items(context)
        assert len(events) == len(delivered) == 1
        event = events[0]
        assert event["late_seconds"] == 3600 and event["unread"]
        assert event["desktop"]["status"] == "accepted" and event["desktop"]["observed"] is None
        Notifications(sessions).mark_read(context, event["id"])
        assert not Notifications(sessions).list_items(context)[0]["unread"]


def test_todo_complete_delete_and_due_race_are_serialized(tmp_path):
    due = datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
    with SQLiteStorage.create_new(tmp_path / "race.sqlite3") as storage:
        sessions = SessionStore(storage=storage); context = sessions.create(uuid4()); todos = TodoService(sessions)
        first = todos.add(context, "先完成"); second = todos.add(context, "先触发")
        reminders = ReminderService(sessions)
        reminders.add(context, "取消", due, todo_id=first.id)
        reminders.add(context, "已触发保留", due, todo_id=second.id)
        todos.complete(context, first.id)
        scheduler = Scheduler(sessions, now=lambda: due, desktop=lambda event: None)
        scheduler.tick(); todos.delete(context, second.id); scheduler.tick()
        assert len(Notifications(sessions).list_items(context)) == 1
        assert reminders.list_items(context)[0]["status"] == "cancelled"


def test_desktop_failure_does_not_lose_session_notification():
    sessions = SessionStore(); context = sessions.create(uuid4())
    due = datetime(2026, 10, 20, tzinfo=UTC)
    ReminderService(sessions).add(context, "仍可在会话看到", due)
    def fail(event): raise OSError("private-error")
    Scheduler(sessions, now=lambda: due, desktop=fail).tick()
    event = Notifications(sessions).list_items(context)[0]
    assert event["desktop"]["status"] == "failed" and event["unread"]
    assert "private-error" not in str(event)


def test_disabled_windows_notifications_are_not_reported_as_accepted():
    from min_agent.desktop_notifications import DesktopChannel
    NotificationSetting = pytest.importorskip("winrt.windows.ui.notifications").NotificationSetting
    class Native:
        setting = NotificationSetting.DISABLED_FOR_USER
    class Toaster:
        toastNotifier = Native()
        def show_toast(self, toast): pytest.fail("系统已禁用时不能报告发送成功")
    channel = DesktopChannel.__new__(DesktopChannel)
    channel.toaster, channel.port = Toaster(), 8765
    sessions = SessionStore(); context = sessions.create(uuid4())
    due = datetime(2026, 10, 20, tzinfo=UTC)
    ReminderService(sessions).add(context, "系统已关闭", due)
    Scheduler(sessions, now=lambda: due, desktop=channel).tick()
    event = Notifications(sessions).list_items(context)[0]
    assert event["desktop"]["status"] == "disabled" and event["unread"]
