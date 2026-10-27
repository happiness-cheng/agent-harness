"""独立于单次运行状态的工作模式与具体动作授权判定；不执行工具。"""

import json
from typing import TYPE_CHECKING, Annotated, Literal
from contextlib import contextmanager
from uuid import UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

if TYPE_CHECKING:
    from min_agent.sessions import ExecutionContext, SessionStore


class ActionSpec(BaseModel):
    """可信执行入口提供的规范化请求，不能凭标签扩大参数或资源。"""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", allow_inf_nan=False)
    operation: Literal["todo.list", "todo.add", "todo.update", "todo.complete", "todo.delete", "reminder.list", "reminder.add", "reminder.update", "reminder.cancel"]
    parameters: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_query(self):
        if self.operation in {"todo.list", "reminder.list"} and self.parameters:
            raise ValueError("todo.list不接受附带参数")
        return self

    def signature(self) -> str:
        """保留JSON值类型，避免Python中True等于1造成范围误匹配。"""
        return json.dumps(
            self.model_dump(), sort_keys=True, ensure_ascii=False,
            allow_nan=False, separators=(",", ":"),
        )


_GoalText = Annotated[str, Field(min_length=1, max_length=1000, pattern=r"\S")]


class GoalSpec(BaseModel):
    """文本记录目标依据；具体动作清单才参与机械权限判定。"""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    objective: _GoalText
    constraints: tuple[_GoalText, ...] = Field(default=(), max_length=20)
    completion_criteria: tuple[_GoalText, ...] = Field(min_length=1, max_length=20)
    actions: tuple[ActionSpec, ...] = Field(default=(), max_length=50)


class GoalProposal(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    proposal_id: UUID = Field(default_factory=uuid4)
    goal: GoalSpec
    # 旧快照按v1读取，但旧确认缺少可证明的消费状态，写入须重新提出提案。
    authorization_version: Literal[1, 2] = 1

    def instance_id(self, index):
        return str(uuid5(self.proposal_id, f"action:{index}"))


class WorkState(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    mode: Literal["plan", "build"] = "plan"
    status: Literal["ready", "waiting_user"] = "ready"
    proposal: GoalProposal | None = None
    confirmed_goal_id: UUID | None = None
    waiting_reason: Literal["goal_unconfirmed", "scope_not_confirmed"] | None = None

    @model_validator(mode="after")
    def check_consistency(self):
        if self.confirmed_goal_id is not None and (
            self.proposal is None or self.confirmed_goal_id != self.proposal.proposal_id
        ):
            raise ValueError("确认必须对应当前目标版本")
        if (self.status == "waiting_user") != (self.waiting_reason is not None):
            raise ValueError("等待状态必须有原因，ready不能保留等待原因")
        if self.confirmed_goal_id is None and (self.proposal is not None or self.mode == "build"):
            if self.status != "waiting_user":
                raise ValueError("未确认目标不能进入可执行状态")
        return self


class AuthorizationDecision(BaseModel):
    """只表示是否允许该请求，不表示操作已经执行。"""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    allowed: bool
    code: Literal["allowed", "plan_read_only", "goal_unconfirmed", "waiting_user", "scope_not_confirmed",
                  "action_already_consumed", "legacy_confirmation_requires_new_proposal"]
    instance_id: str | None = None


class AuthorizationRequired(PermissionError):
    """真实操作尚未开始；必须由用户入口处理，不能靠模型改写参数重试。"""

    def __init__(self, code: str) -> None:
        super().__init__("操作尚未获准，需要用户确认或切换工作模式")
        self.code = code


class TaskController:
    def __init__(self, sessions: "SessionStore") -> None:
        self._sessions = sessions

    def snapshot(self, context: "ExecutionContext") -> WorkState:
        return self._sessions.work_state(context)

    @contextmanager
    def guard(self, context: "ExecutionContext", action: ActionSpec):
        """授权检查至真实业务提交保持同一会话锁，不发放可缓存的许可。"""
        with self._sessions.control(context):
            decision = self.authorize(context, action)
            if not decision.allowed:
                raise AuthorizationRequired(decision.code)
            if decision.instance_id is None:
                yield
                return
            from min_agent.operations import action_scope, operation_id, stamp_action
            state = self.snapshot(context)
            resource = (str(uuid5(context.session_id, decision.instance_id)) if action.operation.endswith(".add")
                        else action.parameters.get("item_id", action.parameters.get("reminder_id")))
            record = {"instance_id": decision.instance_id, "proposal_id": str(state.confirmed_goal_id),
                "owner_id": str(context.owner_id), "session_id": str(context.session_id),
                "operation": action.operation, "signature": action.signature(),
                "resource_id": resource, "request_id": operation_id.get()}
            with action_scope(record):
                yield
                # 无变化操作没有业务提交，也必须消费一次确认；异常路径不执行这里。
                candidate = self._sessions.snapshot(context)
                if decision.instance_id not in candidate.extensions.get("action_receipts", {}):
                    stamp_action(candidate, action.operation.split(".", 1)[0])
                    self._sessions._commit_session(candidate)

    def select_mode(self, context: "ExecutionContext", mode: Literal["plan", "build"]) -> WorkState:
        """由可信用户入口调用；选择Build本身不确认任何目标。"""
        with self._sessions.control(context):
            state = self.snapshot(context)
            data = {**state.model_dump(), "mode": mode}
            if mode == "build" and state.confirmed_goal_id is None:
                data.update(status="waiting_user", waiting_reason="goal_unconfirmed")
            candidate = WorkState.model_validate(data)
            self._sessions.set_work_state(context, candidate)
            return candidate.model_copy(deep=True)

    def propose(self, context: "ExecutionContext", goal: GoalSpec) -> GoalProposal:
        self._sessions.check_access(context)
        if not isinstance(goal, GoalSpec):
            raise TypeError("goal必须是GoalSpec")
        goal = GoalSpec.model_validate(goal.model_dump(warnings=False))
        proposal = GoalProposal(goal=goal, authorization_version=2)
        with self._sessions.control(context):
            state = self.snapshot(context)
            candidate = WorkState(
                mode=state.mode, status="waiting_user", proposal=proposal,
                waiting_reason="goal_unconfirmed",
            )
            self._sessions.set_work_state(context, candidate)
            return proposal.model_copy(deep=True)

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
