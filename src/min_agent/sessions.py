"""按可信owner/session上下文保存历史；可显式接入SQLite。"""

from typing import TYPE_CHECKING, Literal
from threading import RLock
from contextlib import contextmanager
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from min_agent.budget import BudgetRecord, check_budget_successor
from min_agent.models import Message
from min_agent.checkpoints import CheckpointRecord, history_hash
from min_agent.task_state import WorkState

if TYPE_CHECKING:
    from min_agent.storage import SQLiteStorage
    from min_agent.tools.todo import TodoItem


class ExecutionContext(BaseModel):
    """由入口应用提供的身份上下文，不从模型参数取得。"""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    owner_id: UUID
    session_id: UUID


class TaskState(BaseModel):
    """一次调用的执行状态，不承担完整业务目标状态机。"""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    task_id: UUID
    status: Literal["running", "completed", "waiting_user", "partial", "failed", "cancelled"]
    run_id: UUID | None = None
    error_code: str | None = None
    history_start: int = Field(default=0, ge=0)


class SessionSnapshot(BaseModel):
    """读取时返回独立副本，修改副本不会写回存储。"""

    model_config = ConfigDict(strict=True, extra="forbid")
    owner_id: UUID
    session_id: UUID
    messages: list[Message] = Field(default_factory=list)
    task: TaskState | None = None
    work_state: WorkState = Field(default_factory=WorkState)
    budgets: dict[UUID, BudgetRecord] = Field(default_factory=dict)
    active_budget_id: UUID | None = None
    checkpoint: CheckpointRecord | None = None
    extensions: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_budgets(self):
        if bool(self.budgets) != (self.active_budget_id is not None) or (
            self.active_budget_id is not None and self.active_budget_id not in self.budgets
        ):
            raise ValueError("活动预算与会话预算记录不一致")
        for budget_id, record in self.budgets.items():
            if (budget_id, self.owner_id, self.session_id) != (record.budget_id, record.owner_id, record.session_id):
                raise ValueError("预算记录不属于当前会话")
        if self.checkpoint is not None:
            record, cp = self.checkpoint, self.checkpoint.checkpoint
            if record.owner_id != self.owner_id or cp.session_id != self.session_id or cp.budget_id not in self.budgets:
                raise ValueError("检查点不属于当前会话预算")
            if cp.segment_id not in self.budgets[cp.budget_id].state.segments:
                raise ValueError("检查点预算片段不存在")
            if record.resumed_by is None and (cp.history_length != len(self.messages) or record.history_hash != history_hash(self.messages)):
                raise ValueError("待恢复检查点历史不一致")
        return self


class SessionStore:
    def __init__(self, *, storage: "SQLiteStorage | None" = None) -> None:
        self._storage = storage
        self._sessions: dict[UUID, SessionSnapshot] = {}
        self._todo_items: dict[UUID, dict[UUID, "TodoItem"]] = {}
        self._lock = RLock()
        self._budget_ledgers = {}
        if storage is not None:
            # load_all先校验全部记录；损坏时拒绝启动，不降级为空内存库。
            bundles = storage.load_all()
            self._sessions = {bundle.snapshot.session_id: bundle.snapshot for bundle in bundles}
            self._todo_items = {
                bundle.snapshot.session_id: {item.id: item for item in bundle.todos}
                for bundle in bundles
            }

    def _commit_session(self, snapshot: SessionSnapshot, *, budget_expected=None) -> None:
        """调用方持有会话锁；提交成功后才发布候选状态。"""
        if self._storage is not None:
            self._storage.save(
                ExecutionContext(owner_id=snapshot.owner_id, session_id=snapshot.session_id),
                snapshot, list(self._todo_items.get(snapshot.session_id, {}).values()),
                **({"budget_expected": budget_expected} if budget_expected is not None else {}),
            )
        self._sessions[snapshot.session_id] = snapshot

    def set_budget_record(self, context: ExecutionContext, record: BudgetRecord, *, expected_revision,
                          activate: bool = False) -> None:
        """可信预算入口；保存成功才发布，并把预期版本传到SQLite事务。"""
        with self._lock:
            session = self._session(context)
            record = BudgetRecord.model_validate(record.model_dump(warnings="error"))
            previous = session.budgets.get(record.budget_id)
            if previous is None:
                if expected_revision is not None or record.revision != 0:
                    raise ValueError("预算创建版本不一致")
            else:
                if previous.revision != expected_revision:
                    raise ValueError("预算版本已过期")
                check_budget_successor(previous, record)
            candidate = session.model_copy(deep=True)
            candidate.budgets[record.budget_id] = record
            if activate:
                candidate.active_budget_id = record.budget_id
            candidate = SessionSnapshot.model_validate(candidate.model_dump())
            self._commit_session(candidate, budget_expected=(record.budget_id, expected_revision))

    def check_budget_binding(self, context: ExecutionContext, budget) -> None:
        with self._lock:
            session = self._session(context)
            if budget is not None:
                budget.check_context(context, self)
            if session.active_budget_id is not None:
                binding = budget.binding if budget is not None else None
                if binding is None or (binding.budget_id, binding.owner_id, binding.session_id) != (
                    session.active_budget_id, session.owner_id, session.session_id,
                ):
                    raise PermissionError("当前会话必须使用已绑定的活动持久预算")
                if self._budget_ledgers.get((session.session_id, session.active_budget_id)) is not budget._ledger:
                    raise PermissionError("预算必须使用当前存储恢复的唯一账本")

    def _bind_todos(self):
        """仅供TodoService内部绑定；持久化模式共用集合及同一把可重入锁。"""
        if self._storage is None:
            return {}, self._lock
        return self._todo_items, self._lock

    @contextmanager
    def control(self, context: ExecutionContext):
        """串行协调授权状态与真实业务提交；内部嵌套调用共用RLock。"""
        with self._lock:
            self._session(context)
            yield

    def work_state(self, context: ExecutionContext) -> WorkState:
        with self._lock:
            return self._session(context).work_state.model_copy(deep=True)

    def set_work_state(self, context: ExecutionContext, state: WorkState) -> None:
        """仅可信控制入口调用；先提交完整候选快照，再发布内存。"""
        with self._lock:
            session = self._session(context)
            if not isinstance(state, WorkState):
                raise TypeError("state必须是WorkState")
            checked = WorkState.model_validate(state.model_dump(warnings=False))
            candidate = session.model_copy(deep=True)
            candidate.work_state = checked
            if checked.proposal is not None and (
                    session.work_state.proposal is None
                    or checked.proposal.proposal_id != session.work_state.proposal.proposal_id):
                # 只记录可信目标所属的逻辑任务边界，避免新聊天沿用旧进度。
                start = (session.task.history_start if session.task is not None
                         and session.task.status != "completed" else len(session.messages))
                candidate.extensions["goal_tracking"] = {
                    "proposal_id": str(checked.proposal.proposal_id), "history_start": start}
            self._commit_session(candidate)

    def _persist_todos(self, context: ExecutionContext, items: dict[UUID, "TodoItem"]) -> None:
        """服务持有绑定锁，在发布候选集合前保存完整会话。"""
        with self._lock:
            session = self._session(context)
            candidate = session.model_copy(deep=True)
            from min_agent.operations import stamp_action
            stamp_action(candidate, "todo", items=items)
            for reminder in candidate.extensions.get("reminders", {}).values():
                linked = reminder.get("todo_id")
                if linked and reminder["status"] == "active":
                    item = items.get(UUID(linked))
                    if item is None or item.deleted or item.status == "completed":
                        reminder["status"] = "cancelled"
            if self._storage is not None:
                self._storage.save(context, candidate, list(items.values()))
            self._sessions[context.session_id] = candidate

    def create(self, owner_id: UUID) -> ExecutionContext:
        context = ExecutionContext(owner_id=owner_id, session_id=uuid4())
        with self._lock:
            self._commit_session(SessionSnapshot(**context.model_dump()))
        return context

    def _session(self, context: ExecutionContext) -> SessionSnapshot:
        if not isinstance(context, ExecutionContext):
            raise TypeError("context 必须是 ExecutionContext")
        context = ExecutionContext.model_validate(context.model_dump(warnings=False))
        session = self._sessions.get(context.session_id)
        if session is None or session.owner_id != context.owner_id:
            raise PermissionError("会话不存在或不属于当前用户")
        return session

    def _active(self, context: ExecutionContext, task_id: UUID) -> SessionSnapshot:
        session = self._session(context)
        if session.task is None or session.task.task_id != task_id:
            raise PermissionError("任务不属于当前会话的活动任务")
        if session.task.status != "running":
            raise RuntimeError("任务已结束，不能继续写入")
        return session

    def snapshot(self, context: ExecutionContext) -> SessionSnapshot:
        with self._lock:
            return self._session(context).model_copy(deep=True)

    def check_access(self, context: ExecutionContext) -> ExecutionContext:
        """仅验证会话归属，不判断业务操作权限或读取聊天历史。"""
        with self._lock:
            session = self._session(context)
            return ExecutionContext(owner_id=session.owner_id, session_id=session.session_id)

    def extension(self, context, key, default=None):
        from copy import deepcopy
        with self._lock:
            return deepcopy(self._session(context).extensions.get(key, default))

    def set_extension(self, context, key, value):
        self.set_extensions(context, {key: value})

    def set_extensions(self, context, changes):
        with self._lock:
            candidate = self._session(context).model_copy(deep=True)
            candidate.extensions.update(changes)
            if "reminders" in changes:
                from min_agent.operations import stamp_action
                stamp_action(candidate, "reminder", items=changes["reminders"])
            candidate = SessionSnapshot.model_validate(candidate.model_dump())
            self._commit_session(candidate)

    def contexts(self, owner_id=None):
        with self._lock:
            return [ExecutionContext(owner_id=value.owner_id, session_id=value.session_id)
                    for value in self._sessions.values() if owner_id is None or value.owner_id == owner_id]

    def save_checkpoint(self, context, checkpoint):
        with self._lock:
            candidate = self._session(context).model_copy(deep=True)
            if candidate.task is None or candidate.task.status == "running":
                raise RuntimeError("只能为已暂停或结束的运行保存检查点")
            record = candidate.budgets[checkpoint.budget_id]
            candidate.checkpoint = CheckpointRecord(checkpoint=checkpoint, owner_id=context.owner_id,
                goal_id=candidate.work_state.confirmed_goal_id, history_hash=history_hash(candidate.messages),
                known_segments=tuple(record.state.segments))
            self._commit_session(SessionSnapshot.model_validate(candidate.model_dump()))

    @staticmethod
    def _pending(messages: list[Message]) -> set[str]:
        pending: set[str] = set()
        for message in messages:
            if message.role == "tool":
                if message.tool_call_id not in pending:
                    raise ValueError("工具结果没有对应的未完成请求")
                pending.remove(message.tool_call_id)
            else:
                if pending:
                    raise ValueError("工具结果尚未配齐，不能追加下一轮消息")
                pending.update(call.id for call in message.tool_calls)
        return pending

    def begin(self, context: ExecutionContext, *, checkpoint_id=None, budget=None) -> TaskState:
        with self._lock:
            session = self._session(context)
            if session.task is not None and session.task.status == "running":
                raise RuntimeError("会话已有运行中的任务")
            if self._pending(session.messages):
                raise RuntimeError("会话历史存在未配对工具请求，不能启动新任务")
            record = session.checkpoint
            if checkpoint_id is not None or (record is not None and record.resumed_by is None):
                if record is None or record.resumed_by is not None or checkpoint_id != record.checkpoint.checkpoint_id:
                    raise PermissionError("需要定向确认当前检查点")
                self.check_budget_binding(context, budget)
                if (budget is None or budget.binding is None
                        or budget.binding.budget_id != record.checkpoint.budget_id
                        or record.goal_id != session.work_state.confirmed_goal_id
                        or budget.segment_id in record.known_segments
                        or budget.usage.model_calls or budget.usage.tool_calls
                        or budget._parent is not None):
                    raise PermissionError("恢复需要原任务的新预算片段及未改变的目标")
            history_start = (session.task.history_start if session.task is not None and session.task.status != "completed"
                             else len(session.messages))
            task = TaskState(task_id=uuid4(), status="running", history_start=history_start)
            candidate = session.model_copy(deep=True)
            candidate.task = task
            if checkpoint_id is not None:
                candidate.checkpoint = record.model_copy(update={"resumed_by": task.task_id})
            self._commit_session(candidate)
            return task.model_copy(deep=True)

    def append(self, context: ExecutionContext, task_id: UUID, messages: list[Message]) -> None:
        with self._lock:
            session = self._active(context, task_id)
            validated = []
            for message in messages:
                if not isinstance(message, Message):
                    raise TypeError("历史必须由 Message 组成")
                validated.append(Message.model_validate(message.model_dump(warnings="error")))
            self._pending(session.messages + validated)
            candidate = session.model_copy(deep=True)
            candidate.messages.extend(validated)
            self._commit_session(candidate)

    def finish(
        self, context: ExecutionContext, task_id: UUID, *, status: str,
        run_id: UUID | None = None, error_code: str | None = None,
        messages: list[Message] | None = None, checkpoint=None,
    ) -> None:
        with self._lock:
            session = self._active(context, task_id)
            task = TaskState(task_id=task_id, status=status, run_id=run_id, error_code=error_code,
                             history_start=session.task.history_start)
            if task.status == "running":
                raise ValueError("finish 必须提供终态")
            if task.status == "completed" and self._pending(session.messages):
                raise ValueError("工具结果尚未配齐，不能标记完成")
            candidate = session.model_copy(deep=True)
            if messages is not None:
                candidate.messages.extend(Message.model_validate(message.model_dump()) for message in messages)
                self._pending(candidate.messages)
            if checkpoint is not None and checkpoint.budget_id is not None:
                record = candidate.budgets[checkpoint.budget_id]
                candidate.checkpoint = CheckpointRecord(
                    checkpoint=checkpoint, owner_id=context.owner_id,
                    goal_id=candidate.work_state.confirmed_goal_id,
                    history_hash=history_hash(candidate.messages), known_segments=tuple(record.state.segments),
                )
            candidate.task = task
            if task.status == "completed":
                from min_agent.memory import record_completed_interaction
                record_completed_interaction(candidate)
            candidate = SessionSnapshot.model_validate(candidate.model_dump())
            self._commit_session(candidate)
