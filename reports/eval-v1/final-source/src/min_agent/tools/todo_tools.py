"""将可信会话的todo业务绑定为带硬授权门的模型工具。"""

import json
from datetime import date
from typing import Annotated, TypedDict
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

from min_agent.sessions import ExecutionContext
from min_agent.task_state import ActionSpec, TaskController
from min_agent.tools.registry import ToolDefinition, ToolRegistry
from min_agent.tools.todo import TodoService


_Title = Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]


def _check_date_label(value: str) -> str:
    if value not in {"今天", "明天", "后天"}:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError("日期必须为YYYY-MM-DD或今天/明天/后天")
    return value


_DateLabel = Annotated[str, AfterValidator(_check_date_label)]


class _Parameters(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class _ListParameters(_Parameters):
    pass


class _AddParameters(_Parameters):
    title: _Title
    due_date: _DateLabel | None = None


class _IdParameters(_Parameters):
    item_id: UUID = Field(strict=False)


class _Patch(TypedDict, total=False):
    title: _Title
    due_date: _DateLabel | None


_Patch.__pydantic_config__ = ConfigDict(strict=True, extra="forbid")


class _UpdateParameters(_IdParameters):
    patch: _Patch

    @field_validator("patch")
    @classmethod
    def check_patch(cls, value):
        if not value:
            raise ValueError("修改补丁不能为空")
        return value


class _TodoExecutor:
    def __init__(self, todos: TodoService, controller: TaskController, context: ExecutionContext) -> None:
        if todos._sessions is not controller._sessions:
            raise ValueError("工具服务与授权控制器必须使用同一SessionStore")
        self._todos, self._controller = todos, controller
        self._context = todos._sessions.check_access(context)

    def _run(self, operation: str, parameters: dict) -> str:
        # 日期归一化、权限判断和业务提交共用锁；不信任先前的allowed结果。
        with self._todos._sessions.control(self._context):
            if "due_date" in parameters:
                parameters["due_date"] = self._todos.normalize_date(parameters["due_date"])
            normalized = parameters.copy()
            if "item_id" in normalized:
                normalized["item_id"] = str(normalized["item_id"])
            if normalized.get("due_date") is not None:
                normalized["due_date"] = normalized["due_date"].isoformat()
            action = ActionSpec(operation=f"todo.{operation}", parameters=normalized)
            with self._controller.guard(self._context, action):
                if operation == "list":
                    return json.dumps([
                        {**view.item.model_dump(mode="json"), "overdue": view.overdue}
                        for view in self._todos.list_views(self._context)
                    ], ensure_ascii=False, allow_nan=False)
                handler = {
                    "add": self._todos.add, "update": self._todos.update,
                    "complete": self._todos.complete, "delete": self._todos.delete,
                }[operation]
                return handler(self._context, **parameters).model_dump_json()

    def list_items(self) -> str:
        return self._run("list", {})

    def add(self, title: str, due_date: str | None = None) -> str:
        return self._run("add", {"title": title, "due_date": due_date})

    def update(self, item_id: UUID, patch: _Patch) -> str:
        return self._run("update", {"item_id": item_id, **patch})

    def complete(self, item_id: UUID) -> str:
        return self._run("complete", {"item_id": item_id})

    def delete(self, item_id: UUID) -> str:
        return self._run("delete", {"item_id": item_id})


def todo_registry(todos: TodoService, controller: TaskController, context: ExecutionContext) -> ToolRegistry:
    executor = _TodoExecutor(todos, controller, context)
    registry = ToolRegistry(sessions=todos._sessions, context=executor._context)
    definitions = [
        ("todo_list", "查询当前会话的待办、真实ID、状态及逾期；Plan允许，含完成项，不含已删除项。", _ListParameters, executor.list_items),
        ("todo_add", "按已确认范围记录当前会话待办；需Build及确认。日期接受YYYY-MM-DD或今天/明天/后天；无主动提醒能力。", _AddParameters, executor.add),
        ("todo_update", "按真实ID修改已确认的title/due_date。patch省略字段保留原值，due_date=null清除日期；需Build及确认。", _UpdateParameters, executor.update),
        ("todo_complete", "按已确认的真实ID完成当前会话待办；重复完成changed=false；需Build及确认。", _IdParameters, executor.complete),
        ("todo_delete", "按已确认的真实ID逻辑删除当前会话待办；普通查询隐藏，重复删除changed=false；需Build及确认。", _IdParameters, executor.delete),
    ]
    for name, description, parameters, handler in definitions:
        registry.register(ToolDefinition(name=name, description=description, parameters=parameters, handler=handler))
    return registry
