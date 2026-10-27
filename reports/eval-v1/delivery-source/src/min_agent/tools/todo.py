"""会话内todo新增/查询/修改/完成/删除业务服务；尚未开放给模型调用。"""

from typing import Literal
from uuid import UUID, uuid4, uuid5
from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from min_agent.clock import Clock
from min_agent.sessions import ExecutionContext, SessionStore


class _Unset(Enum):
    VALUE = 0


_UNSET = _Unset.VALUE


class TodoItem(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    id: UUID
    session_id: UUID
    title: str = Field(min_length=1, max_length=200, pattern=r"\S")
    status: Literal["pending", "completed"] = "pending"
    deleted: bool = False
    due_date: date | None = None


class TodoCompletionResult(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    item: TodoItem
    changed: bool


class TodoDeletionResult(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    item: TodoItem
    changed: bool


class TodoView(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    item: TodoItem
    overdue: bool


class TodoService:
    def __init__(self, sessions: SessionStore, *, clock: Clock | None = None) -> None:
        self._sessions = sessions
        self._clock = clock if clock is not None else Clock()
        self._items, self._lock = sessions._bind_todos()

    def normalize_date(self, value: date | str | None) -> date | None:
        """供可信工具入口在授权前归一化日期；业务写入直接使用绝对日期。"""
        return self._clock.resolve_date(value)

    def _replace(self, context: ExecutionContext, item: TodoItem) -> None:
        """持有绑定锁，只发布已成功提交的候选集合；保留原有插入顺序。"""
        candidate = self._items.get(context.session_id, {}).copy()
        candidate[item.id] = item
        self._sessions._persist_todos(context, candidate)
        self._items[context.session_id] = candidate

    def add(self, context: ExecutionContext, title: str, *, due_date: date | str | None = None) -> TodoItem:
        checked = self._sessions.check_access(context)
        from min_agent.operations import operation_id
        identity = operation_id.get()
        item = TodoItem(
            id=uuid5(checked.session_id, identity) if identity is not None else uuid4(), session_id=checked.session_id, title=title,
            due_date=self._clock.resolve_date(due_date),
        )
        with self._lock:
            if identity is not None and item.id in self._items.get(checked.session_id, {}):
                return self._items[checked.session_id][item.id].model_copy(deep=True)
            self._replace(checked, item)
            return item.model_copy(deep=True)

    def list_items(self, context: ExecutionContext) -> list[TodoItem]:
        checked = self._sessions.check_access(context)
        with self._lock:
            return [
                item.model_copy(deep=True)
                for item in self._items.get(checked.session_id, {}).values()
                if not item.deleted
            ]

    def list_views(self, context: ExecutionContext) -> list[TodoView]:
        items = self.list_items(context)
        today = self._clock.today()
        return [
            TodoView(
                item=item,
                overdue=item.status == "pending" and item.due_date is not None and item.due_date < today,
            )
            for item in items
        ]

    def update(
        self, context: ExecutionContext, item_id: UUID, *,
        title: str | _Unset = _UNSET, due_date: date | str | None | _Unset = _UNSET,
    ) -> TodoItem:
        checked = self._sessions.check_access(context)
        if not isinstance(item_id, UUID):
            raise TypeError("item_id 必须是 UUID")
        with self._lock:
            items = self._items.get(checked.session_id, {})
            current = items.get(item_id)
            if current is None or current.deleted:
                raise KeyError("当前会话中未找到todo")
            if title is _UNSET and due_date is _UNSET:
                raise TypeError("update至少需要title或due_date")
            changes = {}
            if title is not _UNSET:
                changes["title"] = title
            if due_date is not _UNSET:
                changes["due_date"] = self._clock.resolve_date(due_date)
            # 保留不可改字段，完整校验成功后再替换；不使用跳过校验的副本更新。
            updated = TodoItem.model_validate({**current.model_dump(), **changes})
            self._replace(checked, updated)
            return updated.model_copy(deep=True)

    def complete(self, context: ExecutionContext, item_id: UUID) -> TodoCompletionResult:
        checked = self._sessions.check_access(context)
        if not isinstance(item_id, UUID):
            raise TypeError("item_id 必须是 UUID")
        with self._lock:
            items = self._items.get(checked.session_id, {})
            current = items.get(item_id)
            if current is None or current.deleted:
                raise KeyError("当前会话中未找到todo")
            if current.status == "completed":
                return TodoCompletionResult(item=current.model_copy(deep=True), changed=False)
            completed = TodoItem.model_validate({**current.model_dump(), "status": "completed"})
            self._replace(checked, completed)
            return TodoCompletionResult(item=completed.model_copy(deep=True), changed=True)

    def delete(self, context: ExecutionContext, item_id: UUID) -> TodoDeletionResult:
        checked = self._sessions.check_access(context)
        if not isinstance(item_id, UUID):
            raise TypeError("item_id 必须是 UUID")
        with self._lock:
            items = self._items.get(checked.session_id, {})
            current = items.get(item_id)
            if current is None:
                raise KeyError("当前会话中未找到todo")
            if current.deleted:
                return TodoDeletionResult(item=current.model_copy(deep=True), changed=False)
            deleted = TodoItem.model_validate({**current.model_dump(), "deleted": True})
            self._replace(checked, deleted)
            return TodoDeletionResult(item=deleted.model_copy(deep=True), changed=True)
