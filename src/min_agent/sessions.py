"""按可信owner/session上下文保存历史；可显式接入SQLite。"""

from typing import TYPE_CHECKING, Literal
from threading import RLock
from contextlib import contextmanager
from uuid import UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from min_agent.budget import BudgetRecord, check_budget_successor
from min_agent.models import Message
from min_agent.checkpoints import CheckpointRecord, history_hash
from min_agent.task_state import WorkState, WorkStateTransition, derive_logical_task_id

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
    logical_task_id: UUID | None = None
    run_id: UUID | None = None
    error_code: str | None = None
    history_start: int = Field(default=0, ge=0)


class SessionSnapshot(BaseModel):
    """读取时返回独立副本，修改副本不会写回存储。"""

    model_config = ConfigDict(strict=True, extra="forbid")
    owner_id: UUID
    session_id: UUID
    revision: int = Field(default=0, ge=0)
    proposal_history: list[str] = Field(default_factory=list)
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

    @model_validator(mode="after")
    def check_authorization(self):
        grant = self.work_state.grant
        if grant is None:
            return self
        if grant.owner_id != self.owner_id or grant.session_id != self.session_id:
            raise ValueError("grant 不属于当前会话")
        work = self.work_state
        if (work.proposal is None or work.confirmed_goal_id is None
                or grant.proposal_id != work.proposal.proposal_id
                or work.confirmed_goal_id != work.proposal.proposal_id):
            raise ValueError("grant 与当前确认提案不一致")
        if grant.status == "pending_first_start":
            if self.task is not None and self.task.status != "completed":
                raise ValueError("pending grant 只能对应无任务或已完成任务")
            if (grant.logical_task_id, grant.attempt_id, grant.budget_id) != (None, None, None):
                raise ValueError("pending grant 不能携带绑定值")
        elif grant.status == "bound":
            task = self.task
            if (task is None or task.status != "running"
                    or task.logical_task_id != grant.logical_task_id
                    or task.task_id != grant.attempt_id):
                raise ValueError("bound grant 必须匹配当前 running attempt")
            if self.active_budget_id != grant.budget_id:
                raise ValueError("bound grant 预算必须精确匹配活动预算")
        else:
            if grant.logical_task_id is None or grant.attempt_id is None:
                raise ValueError("paused/closed grant 必须有逻辑任务与 attempt")
            if grant.budget_id is not None and grant.budget_id not in self.budgets:
                raise ValueError("grant 预算记录不存在")
        return self


class SessionStore:
    def __init__(self, *, storage: "SQLiteStorage | None" = None) -> None:
        self._storage = storage
        self._sessions: dict[UUID, SessionSnapshot] = {}
        self._todo_items: dict[UUID, dict[UUID, "TodoItem"]] = {}
        self._lock = RLock()
        self._budget_ledgers = {}
        self._invalidated: set[UUID] = set()
        if storage is not None:
            # load_all先校验全部记录；损坏时拒绝启动，不降级为空内存库。
            bundles = storage.load_all()
            self._sessions = {bundle.snapshot.session_id: bundle.snapshot for bundle in bundles}
            self._todo_items = {
                bundle.snapshot.session_id: {item.id: item for item in bundle.todos}
                for bundle in bundles
            }

    def _commit_session(self, snapshot: SessionSnapshot, *, budget_expected=None,
                        transition: WorkStateTransition | None = None) -> None:
        """调用方持有会话锁；提交成功后才发布候选状态。"""
        if self._storage is not None:
            if self._storage.has_open_transaction():
                raise RuntimeError("SessionStore 不支持外层存储事务")
            from min_agent.storage import VersionConflict, _SESSION_COMMIT
            try:
                self._storage.save(
                    ExecutionContext(owner_id=snapshot.owner_id, session_id=snapshot.session_id),
                    snapshot, list(self._todo_items.get(snapshot.session_id, {}).values()),
                    **({"budget_expected": budget_expected} if budget_expected is not None else {}),
                    **({"_work_state_transition": transition} if transition is not None else {}),
                    _session_commit=_SESSION_COMMIT,
                )
            except VersionConflict:
                self._invalidated.add(snapshot.session_id)
                raise
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

    def set_work_state(self, context: ExecutionContext, state: WorkState, *,
                       transition: WorkStateTransition, task: TaskState | None = None) -> None:
        """仅可信控制入口调用；提交成功后发布内存；task 只允许补 logical。"""
        with self._lock:
            session = self._session(context)
            if not isinstance(state, WorkState):
                raise TypeError("state必须是WorkState")
            if not isinstance(transition, WorkStateTransition):
                raise TypeError("transition必须是WorkStateTransition")
            checked = WorkState.model_validate(state.model_dump(warnings=False))
            candidate = session.model_copy(deep=True)
            candidate.work_state = checked
            if task is not None:
                if not isinstance(task, TaskState):
                    raise TypeError("task必须是TaskState")
                if session.task is None or task.task_id != session.task.task_id or task.status != session.task.status:
                    raise PermissionError("授权转换不能改变 task 身份或状态")
                if (session.task.logical_task_id is not None
                        and task.logical_task_id != session.task.logical_task_id):
                    raise PermissionError("已存在的逻辑任务身份不能改写")
                candidate.task = task
            if checked.proposal is not None and (
                    session.work_state.proposal is None
                    or checked.proposal.proposal_id != session.work_state.proposal.proposal_id):
                # 只记录可信目标所属的逻辑任务边界，避免新聊天沿用旧进度。
                start = (session.task.history_start if session.task is not None
                         and session.task.status != "completed" else len(session.messages))
                candidate.extensions["goal_tracking"] = {
                    "proposal_id": str(checked.proposal.proposal_id), "history_start": start}
            candidate = SessionSnapshot.model_validate(candidate.model_dump())
            self._commit_session(candidate, transition=transition)

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
                if self._storage.has_open_transaction():
                    raise RuntimeError("SessionStore 不支持外层存储事务")
                from min_agent.storage import VersionConflict, _SESSION_COMMIT
                try:
                    self._storage.save(context, candidate, list(items.values()), _session_commit=_SESSION_COMMIT)
                except VersionConflict:
                    self._invalidated.add(context.session_id)
                    raise
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
        if context.session_id in self._invalidated:
            from min_agent.storage import VersionConflict
            raise VersionConflict("会话缓存已失效，请重新打开会话")
        session = self._sessions.get(context.session_id)
        if session is None or session.owner_id != context.owner_id or session.extensions.get('_internal_owner_document'):
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

    def assert_current(self, context: ExecutionContext) -> None:
        """授权查询须确认缓存版本仍与持久状态一致；失败时不回退旧状态。"""
        self.authoritative_snapshot(context)

    def authoritative_snapshot(self, context: ExecutionContext) -> SessionSnapshot:
        """返回与本地版本一致的持久快照；用于权限和证据验证边界。"""
        with self._lock:
            session = self._session(context)
            if self._storage is None:
                return session.model_copy(deep=True)
            from min_agent.storage import VersionConflict
            try:
                current = self._storage.load(context)
            except Exception:
                self._invalidated.add(context.session_id)
                raise
            if current.snapshot.revision != session.revision:
                self._invalidated.add(context.session_id)
                raise VersionConflict("会话授权状态已过期")
            return current.snapshot.model_copy(deep=True)

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
                    for value in self._sessions.values()
                    if not value.extensions.get('_internal_owner_document')
                    and (owner_id is None or value.owner_id == owner_id)]

    def owner_document(self, context, *, update=None):
        """可信服务专用：同owner一份原子文档，不增加表、不暴露内部会话。"""
        from copy import deepcopy
        with self._lock:
            self._session(context)
            identity = uuid5(context.owner_id, 'adrian-confirmed-memory-v01')
            current = self._sessions.get(identity)
            if self._storage is not None:
                # 共享Memory的撤回/纠正可能由另一个合法Store提交；缓存不能当权威事实。
                self.assert_current(context)
                stored = self._storage.load_optional(ExecutionContext(owner_id=context.owner_id, session_id=identity))
                current = stored.snapshot if stored is not None else None
            if current is not None and (current.owner_id != context.owner_id
                    or not current.extensions.get('_internal_owner_document')):
                raise PermissionError('内部文档归属冲突')
            value = deepcopy(current.extensions.get('long_memory', {})) if current else {}
            if update is None:
                return value
            # 回调失败或SQLite事务失败，不发布内存中的半成品。
            proposed = update(value)
            if current is not None and proposed == value:
                return deepcopy(proposed)
            candidate = (current.model_copy(deep=True) if current else
                         SessionSnapshot(owner_id=context.owner_id, session_id=identity))
            candidate.extensions.update(_internal_owner_document=True, long_memory=proposed)
            self._commit_session(SessionSnapshot.model_validate(candidate.model_dump()))
            return deepcopy(proposed)

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

    def begin(self, context: ExecutionContext, *, checkpoint_id=None, resume_task_id=None, budget=None) -> TaskState:
        if checkpoint_id is not None and resume_task_id is not None:
            raise ValueError("checkpoint_id 与 resume_task_id 不能同时指定")
        with self._lock:
            session = self._session(context)
            if session.task is not None and session.task.status == "running":
                raise RuntimeError("会话已有运行中的任务")
            if self._pending(session.messages):
                raise RuntimeError("会话历史存在未配对工具请求，不能启动新任务")
            if resume_task_id is not None and not isinstance(resume_task_id, UUID):
                raise TypeError("resume_task_id 必须是UUID")
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
            explicit = checkpoint_id is not None or resume_task_id is not None
            old_task = session.task
            if resume_task_id is not None:
                if old_task is None or old_task.task_id != resume_task_id:
                    raise PermissionError("恢复必须定向指定暂停/等待任务")
                if old_task.status in {"completed", "running"}:
                    raise PermissionError("恢复目标必须是暂停/等待任务")
            if explicit and old_task is None:
                raise PermissionError("没有可恢复的任务")
            if explicit and old_task.status == "completed":
                raise PermissionError("已完成任务不能恢复")
            state = session.work_state
            grant = state.grant
            if explicit:
                logical = old_task.logical_task_id or derive_logical_task_id(old_task.task_id)
                if grant is not None:
                    if grant.status != "paused":
                        raise PermissionError("授权未处于可恢复状态")
                    if (state.proposal is None or state.confirmed_goal_id is None
                            or grant.proposal_id != state.proposal.proposal_id
                            or state.confirmed_goal_id != grant.proposal_id):
                        raise PermissionError("恢复目标与当前确认不一致")
                    if grant.logical_task_id != logical or grant.attempt_id != old_task.task_id:
                        raise PermissionError("恢复目标与授权逻辑任务不一致")
                    if grant.budget_id != session.active_budget_id:
                        raise PermissionError("恢复预算与授权不一致")
                    if budget is not None:
                        binding = getattr(budget, "binding", None)
                        if (binding.budget_id if binding is not None else None) != grant.budget_id:
                            raise PermissionError("恢复预算与授权不一致")
                    elif grant.budget_id is not None:
                        raise PermissionError("恢复需要原预算片段")
                task = TaskState(task_id=uuid4(), status="running", logical_task_id=logical,
                                 history_start=old_task.history_start)
                candidate = session.model_copy(deep=True)
                candidate.task = task
                if grant is not None:
                    candidate.work_state = state.model_copy(update={
                        "grant": grant.model_copy(update={"status": "bound", "attempt_id": task.task_id})})
                if checkpoint_id is not None:
                    candidate.checkpoint = record.model_copy(update={"resumed_by": task.task_id})
                candidate = SessionSnapshot.model_validate(candidate.model_dump())
                self._commit_session(
                    candidate,
                    transition=(WorkStateTransition(
                        kind="resume", grant_id=grant.grant_id, logical_task_id=logical,
                        attempt_id=task.task_id, budget_id=grant.budget_id) if grant is not None else None),
                )
                return task.model_copy(deep=True)
            logical = uuid4()
            history_start = (old_task.history_start if old_task is not None and old_task.status != "completed"
                             else len(session.messages))
            task = TaskState(task_id=uuid4(), status="running", logical_task_id=logical,
                             history_start=history_start)
            candidate = session.model_copy(deep=True)
            candidate.task = task
            transition = None
            if grant is not None and grant.status == "pending_first_start":
                binding = getattr(budget, "binding", None) if budget is not None else None
                binding_id = binding.budget_id if binding is not None else None
                if binding_id != session.active_budget_id:
                    raise PermissionError("首次绑定预算与当前活动预算不一致")
                updated = grant.model_copy(update={
                    "status": "bound", "logical_task_id": logical, "attempt_id": task.task_id,
                    "budget_id": session.active_budget_id,
                })
                candidate.work_state = state.model_copy(update={"grant": updated})
                transition = WorkStateTransition(
                    kind="bind_first_start", grant_id=grant.grant_id, logical_task_id=logical,
                    attempt_id=task.task_id, budget_id=session.active_budget_id)
            candidate = SessionSnapshot.model_validate(candidate.model_dump())
            self._commit_session(candidate, transition=transition)
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
            self._tag_memory_dependencies(candidate,len(session.messages),task_id)
            self._commit_session(candidate)

    @staticmethod
    def _tag_memory_dependencies(candidate,start,task_id):
        usage=candidate.extensions.get('long_memory_run_dependencies',{})
        if usage.get('task_id')==str(task_id) and usage.get('memory_ids') and len(candidate.messages)>start:
            candidate.extensions.setdefault('long_memory_dependencies',[]).append(
                dict(start=start,end=len(candidate.messages),memory_ids=usage['memory_ids']))

    def erase_memory_derivatives(self,context,memory_ids):
        """删除本模块追踪的派生回答/归档/摘要；保留用户原话及业务对象。"""
        import json
        with self._lock:
            self._session(context)
            ids=set(memory_ids);candidates=[]
            owned=[s for s in self._sessions.values() if s.owner_id==context.owner_id
                   and not s.extensions.get('_internal_owner_document')]
            if any(s.task is not None and s.task.status=='running' for s in owned):
                raise RuntimeError('请先停止该用户运行中的会话，再清除派生输出')
            for session in owned:
                dependencies=session.extensions.get('long_memory_dependencies',[])
                affected=[d for d in dependencies if ids & set(d['memory_ids'])]
                if not affected: continue
                candidate=session.model_copy(deep=True);positions=set()
                for dep in affected:
                    for i in range(dep['start'],min(dep['end'],len(candidate.messages))):
                        message=candidate.messages[i]
                        if message.role=='user': continue
                        positions.add(i)
                        if message.role=='assistant':
                            candidate.messages[i]=message.model_copy(update=dict(
                                content='[用户已删除相关Memory，派生输出已清除]',provider_state=None,
                                tool_calls=[call.model_copy(update={'arguments':{}}) for call in message.tool_calls]))
                        else:
                            candidate.messages[i]=message.model_copy(update=dict(
                                content=json.dumps({'memory_derived_redacted':True}),provider_state=None))
                # 不保留可能带有同一内容的冻结投影、摘要及旧的有损派生归档。
                for key in ('context','tool_summary_failures','long_memory_last_selection','long_memory_run_dependencies'):
                    candidate.extensions.pop(key,None)
                records=candidate.extensions.get('memory_journal',{})
                for identity,record in list(records.items()):
                    if any(record['start']<=i<record['end'] for i in positions): del records[identity]
                candidate.extensions['long_memory_dependencies']=[d for d in dependencies if d not in affected]
                if candidate.checkpoint is not None:
                    candidate.checkpoint=None
                    if candidate.task is not None:
                        candidate.task=candidate.task.model_copy(update=dict(status='failed',error_code='memory_privacy_redaction'))
                candidates.append(SessionSnapshot.model_validate(candidate.model_dump()))
            if self._storage is not None and candidates:
                if self._storage.has_open_transaction():
                    raise RuntimeError("SessionStore 不支持外层存储事务")
                from min_agent.storage import VersionConflict, _SESSION_COMMIT
                try:
                    self._storage.save_many([(ExecutionContext(owner_id=s.owner_id,session_id=s.session_id),s,
                        list(self._todo_items.get(s.session_id,{}).values())) for s in candidates], _session_commit=_SESSION_COMMIT)
                except VersionConflict:
                    self._invalidated.update(s.session_id for s in candidates)
                    raise
            for candidate in candidates: self._sessions[candidate.session_id]=candidate
            return len(candidates)

    def finish(
        self, context: ExecutionContext, task_id: UUID, *, status: str,
        run_id: UUID | None = None, error_code: str | None = None,
        messages: list[Message] | None = None, checkpoint=None,
    ) -> None:
        with self._lock:
            session = self._active(context, task_id)
            task = TaskState(task_id=task_id, status=status, run_id=run_id, error_code=error_code,
                             logical_task_id=session.task.logical_task_id,
                             history_start=session.task.history_start)
            if task.status == "running":
                raise ValueError("finish 必须提供终态")
            if task.status == "completed" and self._pending(session.messages):
                raise ValueError("工具结果尚未配齐，不能标记完成")
            candidate = session.model_copy(deep=True)
            if messages is not None:
                start=len(candidate.messages)
                candidate.messages.extend(Message.model_validate(message.model_dump()) for message in messages)
                self._tag_memory_dependencies(candidate,start,task_id)
                self._pending(candidate.messages)
            if checkpoint is not None and checkpoint.budget_id is not None:
                record = candidate.budgets[checkpoint.budget_id]
                candidate.checkpoint = CheckpointRecord(
                    checkpoint=checkpoint, owner_id=context.owner_id,
                    goal_id=candidate.work_state.confirmed_goal_id,
                    history_hash=history_hash(candidate.messages), known_segments=tuple(record.state.segments),
                )
            candidate.task = task
            transition = None
            grant = session.work_state.grant
            if (grant is not None and grant.status == "bound"
                    and grant.logical_task_id == session.task.logical_task_id
                    and grant.attempt_id == task_id
                    and grant.budget_id == session.active_budget_id):
                closed = "closed" if task.status == "completed" else "paused"
                candidate.work_state = candidate.work_state.model_copy(update={
                    "grant": grant.model_copy(update={"status": closed})})
                transition = WorkStateTransition(
                    kind="finish", grant_id=grant.grant_id, logical_task_id=grant.logical_task_id,
                    attempt_id=grant.attempt_id, budget_id=grant.budget_id)
            if task.status == "completed":
                from min_agent.memory import record_completed_interaction
                record_completed_interaction(candidate)
            candidate = SessionSnapshot.model_validate(candidate.model_dump())
            self._commit_session(candidate, transition=transition)
