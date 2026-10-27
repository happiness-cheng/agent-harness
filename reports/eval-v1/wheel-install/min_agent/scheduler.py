"""注入时钟的到期事件生成；不调用模型，桌面发送记录不确定状态。"""

from datetime import UTC, datetime
from threading import Lock
from uuid import UUID, uuid5

from min_agent.desktop_notifications import DesktopDisabled
from min_agent.tools.reminder import ReminderService


class Scheduler:
    def __init__(self, sessions, *, now=None, desktop=None):
        self.sessions, self.now, self.desktop = sessions, now or (lambda: datetime.now(UTC)), desktop
        self._lock = Lock()

    def tick(self):
        with self._lock:
            now = self.now()
            if now.tzinfo is None: raise ValueError("调度时钟必须有时区")
            for context in self.sessions.contexts():
                with self.sessions.control(context):
                    reminders = self.sessions.extension(context, "reminders", {})
                    events = self.sessions.extension(context, "notifications", {})
                    changed = False
                    for record in ReminderService(self.sessions).list_items(context):
                        if record["status"] != "active": continue
                        due = datetime.fromisoformat(record["scheduled_at"])
                        if due > now: continue
                        if record["todo_id"]:
                            item = self.sessions._todo_items.get(context.session_id, {}).get(UUID(record["todo_id"]))
                            if item is None or item.deleted or item.status != "pending":
                                reminders[record["id"]]["status"] = "cancelled"; changed = True; continue
                        identity = str(uuid5(UUID(record["id"]), "due"))
                        events.setdefault(identity, {"id": identity, "session_id": str(context.session_id),
                            "reminder_id": record["id"], "title": record["title"], "scheduled_at": record["scheduled_at"],
                            "created_at": now.isoformat(), "late_seconds": max(0, int((now - due).total_seconds())),
                            "unread": True, "session_status": "available",
                            "desktop": {"status": "pending", "attempts": 0, "observed": None}})
                        reminders[record["id"]]["status"] = "fired"
                        changed = True
                    if changed:
                        self.sessions.set_extensions(context, {"reminders": reminders, "notifications": events})
                for identity in list(events): self._deliver(context, identity)

    def _deliver(self, context, identity):
        with self.sessions.control(context):
            events = self.sessions.extension(context, "notifications", {}); event = events[identity]
            if event["desktop"]["status"] == "attempting":
                event["desktop"]["status"] = "unknown"
                self.sessions.set_extension(context, "notifications", events)
                return
            if event["desktop"]["status"] != "pending": return
            if self.desktop is None:
                event["desktop"]["status"] = "disabled"
                self.sessions.set_extension(context, "notifications", events)
                return
            event["desktop"]["status"] = "attempting"; event["desktop"]["attempts"] += 1
            self.sessions.set_extension(context, "notifications", events)
        status = "accepted"
        try: self.desktop(event)
        except DesktopDisabled: status = "disabled"
        except Exception: status = "failed"
        with self.sessions.control(context):
            events = self.sessions.extension(context, "notifications", {})
            events[identity]["desktop"]["status"] = status
            self.sessions.set_extension(context, "notifications", events)
