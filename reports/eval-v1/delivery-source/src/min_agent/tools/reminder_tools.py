"""提醒工具的真实参数授权门；缺时刻必须等待澄清。"""

import json
from pydantic import BaseModel, ConfigDict, Field
from min_agent.task_state import ActionSpec, AuthorizationRequired
from min_agent.tools.registry import ToolDefinition


class _Parameters(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class AddReminder(_Parameters):
    title: str = Field(min_length=1, max_length=200)
    scheduled_at: str | None = None
    time_zone: str = "Asia/Shanghai"
    todo_id: str | None = None


class ReminderId(_Parameters):
    reminder_id: str


class UpdateReminder(ReminderId):
    patch: dict[str, str]


class ListReminders(_Parameters):
    pass


def register_reminders(registry, service, controller, context):
    def execute(operation, parameters):
        if operation == "add":
            if parameters["scheduled_at"] is None: raise AuthorizationRequired("reminder_time_required")
            parameters["scheduled_at"] = service.moment(parameters["scheduled_at"]).isoformat()
        normalized = parameters.copy()
        if operation == "update":
            normalized = {"reminder_id": parameters["reminder_id"], **parameters["patch"]}
            if "scheduled_at" in normalized:
                normalized["scheduled_at"] = service.moment(normalized["scheduled_at"]).isoformat()
        with controller.guard(context, ActionSpec(operation="reminder." + operation, parameters=normalized)):
            if operation == "list": result = service.list_items(context)
            elif operation == "add": result = service.add(context, **parameters)
            elif operation == "update": result = service.update(context, **normalized)
            else: result = service.cancel(context, **parameters)
            return json.dumps(result, ensure_ascii=False)
    definitions = [
        ("reminder_list", "查询当前会话的单次提醒及状态；Plan可用。", ListReminders, lambda: execute("list", {})),
        ("reminder_add", "用户需要到点通知时使用；须明确带偏移的ISO时刻、时区、Build和已确认范围。只有日期时先澄清，不默认早上9点。", AddReminder, lambda **kwargs: execute("add", kwargs)),
        ("reminder_update", "按真实提醒ID修改尚未触发的提醒，须Build和精确确认；不随todo日期隐式修改。", UpdateReminder, lambda **kwargs: execute("update", kwargs)),
        ("reminder_cancel", "按真实ID取消当前会话未触发提醒，须Build和确认；重复取消不产生新副作用。", ReminderId, lambda **kwargs: execute("cancel", kwargs)),
    ]
    for name, description, parameters, handler in definitions:
        registry.register(ToolDefinition(name, description, parameters, handler))
