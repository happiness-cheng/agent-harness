# P07 本轮参考源码

以下为当前工作区原始片段，保留已有注释；不是完整代码或独立可运行程序。


## src/min_agent/checkpoints.py:25 — CheckpointNote

```python
class CheckpointNote(BaseModel):
    """模型整理的未核实文字；不能用它替代业务事实或授权状态。"""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    achievements: _Section
    pending: _Section
    blockers: _Section
    next_steps: _Section
```


## src/min_agent/checkpoints.py:35 — ToolResultRef

```python
class ToolResultRef(BaseModel):
    """当前完整消息列表中的结果引用；不复制业务正文或伪造成功。"""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    message_index: int = Field(ge=0)
    call_id: str
    ok: bool | None
    execution_state: Literal["returned", "not_started", "unknown"]
```


## src/min_agent/checkpoints.py:45 — Checkpoint

```python
class Checkpoint(BaseModel):
    """Runtime返回的局部检查点，持久化/定向继续另由可信入口实现。"""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    checkpoint_id: UUID = Field(default_factory=uuid4)
    segment_id: UUID
    budget_id: UUID | None = None
    session_id: UUID | None = None
    reason: Literal["model_reserve", "tool_reserve", "hard_budget", "context_limit", "no_progress", "user_stopped", "interrupted", "failed"]
    closing_status: Literal["returned", "not_started", "failed", "invalid_response"]
    closing_error: Literal["budget_exhausted", "budget_persistence_failed", "model_call_failed", "checkpoint_invalid_response", "context_limit", "no_progress", "user_stopped", "interrupted"] | None = None
    model_note: CheckpointNote | None = None
    tool_results: tuple[ToolResultRef, ...] = ()
    history_length: int = Field(ge=0)
    task_usage: BudgetUsage
    segment_usage: BudgetUsage
```


## src/min_agent/checkpoints.py:63 — CheckpointRecord

```python
class CheckpointRecord(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    checkpoint: Checkpoint
    owner_id: UUID
    goal_id: UUID | None
    history_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    known_segments: tuple[UUID, ...]
    resumed_by: UUID | None = None
```


## src/min_agent/task_state.py:158 — confirm

```python
    def confirm(self, context: "ExecutionContext", proposal_id: UUID) -> WorkState:
        """定向确认当前提案；不从普通消息或模型参数推断批准。"""
        self._sessions.check_access(context)
        if not isinstance(proposal_id, UUID):
            raise TypeError("proposal_id必须是UUID")
        with self._sessions.control(context):
            state = self.snapshot(context)
            if state.proposal is None or state.proposal.proposal_id != proposal_id:
                raise PermissionError("确认不属于当前目标版本")
            candidate = WorkState(
                mode=state.mode, status="ready", proposal=state.proposal,
                confirmed_goal_id=proposal_id,
            )
            self._sessions.set_work_state(context, candidate)
            return candidate.model_copy(deep=True)
```


## src/min_agent/task_state.py:174 — authorize

```python
    def authorize(self, context: "ExecutionContext", action: ActionSpec) -> AuthorizationDecision:
        self._sessions.check_access(context)
        if not isinstance(action, ActionSpec):
            raise TypeError("action必须是ActionSpec")
        action = ActionSpec.model_validate(action.model_dump(warnings=False))
        if action.operation in {"todo.list", "reminder.list"}:
            return AuthorizationDecision(allowed=True, code="allowed")
        with self._sessions.control(context):
            state = self.snapshot(context)
            if state.mode == "plan":
                return AuthorizationDecision(allowed=False, code="plan_read_only")
            if state.confirmed_goal_id is None:
                return AuthorizationDecision(allowed=False, code="goal_unconfirmed")
            if state.status == "waiting_user":
                return AuthorizationDecision(allowed=False, code="waiting_user")
            if state.proposal.authorization_version != 2:
                return AuthorizationDecision(allowed=False, code="legacy_confirmation_requires_new_proposal")
            receipts = self._sessions.extension(context, "action_receipts", {})
            matching = [index for index, allowed in enumerate(state.proposal.goal.actions)
                        if action.signature() == allowed.signature()]
            for index in matching:
                identity = state.proposal.instance_id(index)
                if identity not in receipts:
                    return AuthorizationDecision(allowed=True, code="allowed", instance_id=identity)
            if matching:
                return AuthorizationDecision(allowed=False, code="action_already_consumed")
            candidate = WorkState(
                mode=state.mode, status="waiting_user", proposal=state.proposal,
                confirmed_goal_id=state.confirmed_goal_id, waiting_reason="scope_not_confirmed",
            )
            self._sessions.set_work_state(context, candidate)
            return AuthorizationDecision(allowed=False, code="scope_not_confirmed")
```


## src/min_agent/sessions.py:235 — save_checkpoint

```python
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
```


## src/min_agent/sessions.py:260 — begin

```python
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
```


## src/min_agent/runtime.py:144 — resume_session

```python
    def resume_session(self, checkpoint_id: UUID, *, store: SessionStore, context: ExecutionContext,
                       text="继续已确认任务；依据已有结果，不重放成功操作。"):
        if not isinstance(checkpoint_id, UUID):
            raise TypeError("检查点ID必须是UUID")
        return self.run_session(text, store=store,
                                context=context, resume_checkpoint_id=checkpoint_id)
```
