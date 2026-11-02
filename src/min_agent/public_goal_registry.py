"""可信公开目标的显式登记与执行前冻结；只经 SessionStore 持久提交。"""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Iterable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from min_agent.public_feedback import PublicGoal, PublicGoalSpec, Scope, goal_digest
from min_agent.sessions import ExecutionContext, SessionStore


class RegistryContract(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", allow_inf_nan=False)


class PublicGoalRequest(RegistryContract):
    proposal_id: str = Field(min_length=1, max_length=100)
    goals: tuple[PublicGoal, ...] = Field(strict=False)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    logical_task_id: str | None = None

    @model_validator(mode="after")
    def nonempty(self):
        if not self.goals:
            raise ValueError("公开目标集合不能为空")
        return self


class PublicGoalBinding(RegistryContract):
    binding_id: str = Field(min_length=1, max_length=160)
    scope: Scope
    spec: PublicGoalSpec
    proposal_id: str
    grant_id: str
    logical_task_id: str
    attempt_id: str
    budget_id: str | None = None
    frozen_revision: int = Field(ge=1)
    initial_todo_ids: tuple[str, ...] = Field(default=(), strict=False)
    initial_receipt_ids: tuple[str, ...] = Field(default=(), strict=False)


def _goals_hash(goals: Iterable[PublicGoal]) -> str:
    payload = [goal.model_dump(mode="json") for goal in goals]
    return sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class PublicGoalRegistry:
    """可信应用调用的登记器；模型/普通请求没有调用入口。"""

    REQUEST_KEY = "public_goal_request"
    BINDINGS_KEY = "public_goal_bindings"

    def __init__(self, sessions: SessionStore) -> None:
        self._sessions = sessions

    def _context(self, context: ExecutionContext) -> ExecutionContext:
        return self._sessions.check_access(context)

    def register(self, context: ExecutionContext, proposal_id: UUID | str,
                 goals: Iterable[PublicGoal | dict]) -> PublicGoalRequest:
        self._context(context)
        proposal = str(proposal_id)
        normalized = tuple(goal if isinstance(goal, PublicGoal)
                           else PublicGoal.model_validate(goal) for goal in goals)
        if not normalized:
            raise ValueError("公开目标集合不能为空")
        if any(goal.kind != "TODO_COUNT" for goal in normalized):
            raise ValueError("unsupported_public_goal_kind")
        if len(normalized) > 1:
            predicates = {(goal.kind, goal.source_ref, goal.expected_count)
                          for goal in normalized}
            if len(predicates) == 1:
                raise ValueError("duplicate_public_goal_predicate")
            raise ValueError("unsupported_public_goal_set")
        request = PublicGoalRequest(proposal_id=proposal, goals=normalized,
                                    content_sha256=_goals_hash(normalized))
        with self._sessions.control(context):
            self._sessions.assert_current(context)
            snapshot = self._sessions.snapshot(context)
            state = snapshot.work_state
            if (state.proposal is None or state.confirmed_goal_id is None
                    or str(state.proposal.proposal_id) != proposal
                    or str(state.confirmed_goal_id) != proposal):
                raise PermissionError("公开目标必须绑定当前已确认提案")
            if snapshot.task is not None and snapshot.task.status == "running":
                raise PermissionError("运行中不能登记公开目标")
            current = self._sessions.extension(context, self.REQUEST_KEY, None)
            if current is not None:
                existing = PublicGoalRequest.model_validate(current)
                if existing.proposal_id == request.proposal_id and existing.goals == request.goals:
                    # 已完成/无 task 的同提案再次登记表示下一次执行，需重新建立
                    # 未消费的请求；paused 的 resume 则保留原逻辑任务请求。
                    if (existing.logical_task_id is not None
                            and (snapshot.task is None or snapshot.task.status == "completed")):
                        self._sessions.set_extension(context, self.REQUEST_KEY,
                                                    request.model_dump(mode="json"))
                        return request
                    return existing
                if (existing.proposal_id == request.proposal_id
                        and existing.goals != request.goals):
                    raise PermissionError("同一提案不能替换公开目标登记")
                # 新已确认提案可替换旧提案尚未执行的 pending 请求；旧冻结历史
                # 留在 BINDINGS_KEY 中，不删除。
                self._sessions.set_extension(context, self.REQUEST_KEY,
                                            request.model_dump(mode="json"))
                return request
            self._sessions.set_extension(context, self.REQUEST_KEY, request.model_dump(mode="json"))
        return request

    def pending(self, context: ExecutionContext) -> PublicGoalRequest | None:
        value = self._sessions.extension(context, self.REQUEST_KEY, None)
        if value is None:
            return None
        request = PublicGoalRequest.model_validate(value)
        snapshot = self._sessions.snapshot(context)
        if request.logical_task_id is None:
            return request
        if snapshot.task is not None and str(snapshot.task.logical_task_id) == request.logical_task_id:
            return request
        return None

    @staticmethod
    def _execution_already_started(snapshot, task, *, whole_logical=False) -> bool:
        logical = str(task.logical_task_id)
        attempt = str(task.task_id)
        for value in (snapshot.extensions.get("action_receipts", {}) or {}).values():
            if not isinstance(value, dict):
                continue
            if str(value.get("logical_task_id")) != logical:
                continue
            if whole_logical or str(value.get("attempt_id")) == attempt:
                return True
        for value in (snapshot.extensions.get("operations", {}) or {}).values():
            if not isinstance(value, dict):
                continue
            if str(value.get("logical_task_id")) != logical:
                continue
            if whole_logical or str(value.get("attempt_id")) == attempt:
                return True
        return False

    def freeze(self, context: ExecutionContext, task) -> PublicGoalBinding | None:
        """由 Runtime 在 task 创建后、首次模型/工具执行前调用。"""
        self._context(context)
        with self._sessions.control(context):
            self._sessions.assert_current(context)
            snapshot = self._sessions.snapshot(context)
            request_raw = self._sessions.extension(context, self.REQUEST_KEY, None)
            bindings = dict(self._sessions.extension(context, self.BINDINGS_KEY, {}))
            if request_raw is None:
                existing = bindings.get(f"binding-{snapshot.task.task_id}") if snapshot.task else None
                return (PublicGoalBinding.model_validate(existing)
                        if existing is not None else None)
            request = PublicGoalRequest.model_validate(request_raw)
            state = snapshot.work_state
            current_task = snapshot.task
            if (task is None or current_task is None or current_task.task_id != task.task_id
                    or current_task.status != "running"):
                raise PermissionError("只能在当前运行任务首次执行前冻结")
            if (request.logical_task_id is not None
                    and request.logical_task_id != str(current_task.logical_task_id)):
                existing = bindings.get(f"binding-{current_task.task_id}")
                return (PublicGoalBinding.model_validate(existing)
                        if existing is not None else None)
            if (state.proposal is None or state.confirmed_goal_id is None
                    or str(state.proposal.proposal_id) != request.proposal_id
                    or str(state.confirmed_goal_id) != request.proposal_id):
                raise PermissionError("公开目标与当前提案不一致")
            grant = state.grant
            if (grant is None or grant.status != "bound"
                    or str(grant.proposal_id) != request.proposal_id
                    or grant.logical_task_id != current_task.logical_task_id
                    or grant.attempt_id != current_task.task_id
                    or grant.budget_id != snapshot.active_budget_id):
                raise PermissionError("公开目标缺少当前授权绑定")
            existing = bindings.get(f"binding-{current_task.task_id}")
            if existing is not None:
                old = PublicGoalBinding.model_validate(existing)
                if (old.spec.goals != request.goals or old.proposal_id != request.proposal_id
                        or old.logical_task_id != str(current_task.logical_task_id)):
                    raise PermissionError("同一执行代次已冻结不同公开目标")
                return old
            # 合法事前冻结可按同一 logical task 建立新 attempt 绑定；没有
            # 任何合法事前冻结时，检查整个 logical task 的历史执行证据，
            # 避免恢复后的首次冻结把首段成果吸收为“初始状态”。
            prior = None
            for raw in bindings.values():
                previous = PublicGoalBinding.model_validate(raw)
                if (previous.logical_task_id == str(current_task.logical_task_id)
                        and previous.proposal_id == request.proposal_id
                        and previous.spec.goals == request.goals):
                    prior = previous
                    break
            if prior is None and self._execution_already_started(
                    snapshot, current_task, whole_logical=True):
                raise PermissionError("相关执行已开始，不能事后冻结公开目标")
            scope = Scope(task_id=str(current_task.task_id),
                          owner_id=str(context.owner_id), session_id=str(context.session_id))
            # freeze 自身会提交并推进 revision；freeze_seq 取提交前一个版本，
            # 使 observation.start_seq 至少比它大，又不把 no-write 终态误判为 withheld。
            freeze_seq = max(1, snapshot.revision - 1)
            digest = goal_digest(scope, freeze_seq, request.goals)
            spec = PublicGoalSpec(scope=scope, freeze_seq=freeze_seq, goals=request.goals,
                                  content_sha256=digest)
            from min_agent.tools.todo import TodoService
            initial_todos = tuple(str(item.id) for item in TodoService(self._sessions).list_items(context))
            initial_receipts = tuple(sorted(self._sessions.extension(context, "action_receipts", {}).keys()))
            # 合法 resume 需要保持同一 logical task 的初始范围（已完成记录可
            # 继续计入），但绑定本身仍是新 attempt 的新 binding/hash。
            for raw in bindings.values():
                previous = PublicGoalBinding.model_validate(raw)
                if (previous.logical_task_id == str(current_task.logical_task_id)
                        and previous.proposal_id == request.proposal_id
                        and previous.spec.goals == request.goals):
                    initial_todos = previous.initial_todo_ids
                    initial_receipts = previous.initial_receipt_ids
                    break
            binding = PublicGoalBinding(
                binding_id=f"binding-{current_task.task_id}", scope=scope, spec=spec,
                proposal_id=request.proposal_id, grant_id=str(grant.grant_id),
                logical_task_id=str(grant.logical_task_id), attempt_id=str(grant.attempt_id),
                budget_id=str(snapshot.active_budget_id) if snapshot.active_budget_id is not None else None,
                frozen_revision=snapshot.revision,
                initial_todo_ids=initial_todos, initial_receipt_ids=initial_receipts,
            )
            existing = bindings.get(binding.binding_id)
            if existing is not None:
                old = PublicGoalBinding.model_validate(existing)
                if (old.spec != binding.spec or old.grant_id != binding.grant_id
                        or old.logical_task_id != binding.logical_task_id
                        or old.proposal_id != binding.proposal_id
                        or old.budget_id != binding.budget_id):
                    raise PermissionError("同一执行代次已冻结不同公开目标")
                return old
            bindings[binding.binding_id] = binding.model_dump(mode="json")
            frozen_request = request.model_copy(update={
                "logical_task_id": str(current_task.logical_task_id)})
            self._sessions.set_extensions(context, {
                self.BINDINGS_KEY: bindings,
                self.REQUEST_KEY: frozen_request.model_dump(mode="json")})
            return binding

    def current_binding(self, context: ExecutionContext) -> PublicGoalBinding | None:
        self._context(context)
        snapshot = self._sessions.snapshot(context)
        if snapshot.task is None:
            return None
        bindings = self._sessions.extension(context, self.BINDINGS_KEY, {})
        raw = bindings.get(f"binding-{snapshot.task.task_id}")
        return PublicGoalBinding.model_validate(raw) if raw is not None else None

    def binding_for_scope(self, scope: Scope) -> PublicGoalBinding | None:
        # scope 的 owner/session 必须属于已加载会话；由 adapter 传入当前 context。
        for context in self._sessions.contexts(owner_id=UUID(scope.owner_id)):
            if str(context.session_id) != scope.session_id:
                continue
            bindings = self._sessions.extension(context, self.BINDINGS_KEY, {})
            raw = bindings.get(f"binding-{scope.task_id}")
            if raw is not None:
                binding = PublicGoalBinding.model_validate(raw)
                if binding.scope == scope:
                    return binding
        return None

    register_goals = register
    freeze_binding = freeze
    current = current_binding


GoalRegistry = PublicGoalRegistry
