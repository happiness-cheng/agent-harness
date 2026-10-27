"""单次绝对时刻提醒，日期待办不隐式生成提醒。"""

from datetime import datetime
from zoneinfo import ZoneInfo
from uuid import UUID, uuid4, uuid5
from typing import Literal
import json
from pydantic import BaseModel, ConfigDict, Field, field_validator

from min_agent.operations import operation_id


class Reminder(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    id: UUID
    title: str = Field(min_length=1, max_length=200, pattern=r"\S")
    scheduled_at: datetime
    time_zone: str = "Asia/Shanghai"
    todo_id: UUID | None = None
    status: Literal["active", "cancelled", "fired"] = "active"

    @field_validator("scheduled_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None: raise ValueError("提醒必须有明确时区偏移")
        return value

    @field_validator("time_zone")
    @classmethod
    def zone(cls, value):
        ZoneInfo(value)
        return value


class ReminderService:
    def __init__(self, sessions): self.sessions = sessions

    @staticmethod
    def moment(value):
        if isinstance(value, str): value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("缺少明确提醒时刻或时区，需澄清")
        return value

    def _link(self, context, todo_id):
        if todo_id is None: return None
        identity = UUID(str(todo_id))
        item = self.sessions._todo_items.get(context.session_id, {}).get(identity)
        if item is None or item.deleted or item.status != "pending":
            raise PermissionError("关联待办不属于当前会话的未完成项")
        return identity

    def add(self, context, title, scheduled_at, *, time_zone="Asia/Shanghai", todo_id=None):
        with self.sessions.control(context):
            identity = operation_id.get()
            identity = uuid5(context.session_id, identity) if identity is not None else uuid4()
            items = self.sessions.extension(context, "reminders", {})
            if str(identity) in items: return items[str(identity)]
            item = Reminder(id=identity, title=title, scheduled_at=self.moment(scheduled_at),
                            time_zone=time_zone, todo_id=self._link(context, todo_id))
            items[str(item.id)] = item.model_dump(mode="json")
            self.sessions.set_extension(context, "reminders", items)
            return items[str(item.id)]

    def list_items(self, context):
        values = self.sessions.extension(context, "reminders", {})
        return [Reminder.model_validate_json(json.dumps(value)).model_dump(mode="json") for value in values.values()]

    def update(self, context, reminder_id, **patch):
        if not patch or set(patch) - {"title", "scheduled_at", "time_zone"}: raise ValueError("提醒补丁字段无效")
        with self.sessions.control(context):
            items = self.sessions.extension(context, "reminders", {}); key = str(UUID(str(reminder_id)))
            if key not in items: raise PermissionError("提醒不属于当前会话")
            if items[key]["status"] != "active": raise ValueError("只能修改未触发的活动提醒")
            candidate = {**items[key], **patch}
            if "scheduled_at" in patch: candidate["scheduled_at"] = self.moment(patch["scheduled_at"]).isoformat()
            item = Reminder.model_validate_json(json.dumps(candidate))
            items[key] = item.model_dump(mode="json"); self.sessions.set_extension(context, "reminders", items)
            return items[key]

    def cancel(self, context, reminder_id):
        with self.sessions.control(context):
            items = self.sessions.extension(context, "reminders", {}); key = str(UUID(str(reminder_id)))
            if key not in items: raise PermissionError("提醒不属于当前会话")
            changed = items[key]["status"] == "active"
            if changed:
                items[key]["status"] = "cancelled"; self.sessions.set_extension(context, "reminders", items)
            return {"item": items[key], "changed": changed}
