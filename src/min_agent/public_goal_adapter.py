"""由受信应用固定安装的公开目标反馈/评分适配器；不执行模型或工具。"""
from __future__ import annotations

from hashlib import sha256
import json

from min_agent.progress_scoring import (Goal, GoalSet, GoalState, ProgressScore, ProgressScorer,
                                        ScoringInput, Scope as ScoringScope,
                                        serialize_progress)
from min_agent.public_feedback import (GoalObservation, PublicFeedback, PublicFeedbackProjector,
                                       PublicGoalSpec, Scope, VerifiedSnapshot)
from min_agent.public_goal_registry import PublicGoalBinding, PublicGoalRegistry
from min_agent.sessions import ExecutionContext
from min_agent.todo_observation import TodoObservation, TodoObserver


def _withheld_feedback() -> PublicFeedback:
    return PublicFeedback(feedback_status="WITHHELD", public_task_status="UNKNOWN",
                          reason_code="UNVERIFIED_OBSERVATION",
                          next_action_hints=("REQUEST_AUTHORIZED_REVIEW",))


def _goal_hash(goal) -> str:
    return sha256(json.dumps(goal.model_dump(mode="json"), sort_keys=True, ensure_ascii=False,
                             separators=(",", ":")).encode()).hexdigest()


class PublicGoalAdapter:
    """实现 V1.3.1 collector 和 V1.4-A scoring adapter。"""

    def __init__(self, registry: PublicGoalRegistry, observer: TodoObserver) -> None:
        self._registry = registry
        self._observer = observer

    @staticmethod
    def _context(scope: Scope) -> ExecutionContext:
        from uuid import UUID
        return ExecutionContext(owner_id=UUID(scope.owner_id), session_id=UUID(scope.session_id))

    def _binding(self, scope: Scope) -> PublicGoalBinding | None:
        try:
            return self._registry.binding_for_scope(scope)
        except Exception:
            return None

    # V1.3.1 TrustedCollector
    def verify_goals(self, scope: Scope, spec: PublicGoalSpec) -> bool:
        binding = self._binding(scope)
        return bool(binding is not None and binding.spec == spec and binding.scope == scope)

    def read_snapshot(self, scope: Scope, spec: PublicGoalSpec,
                      token: str) -> VerifiedSnapshot:
        binding = self._binding(scope)
        if binding is None or binding.spec != spec or token != binding.binding_id:
            raise PermissionError("公开目标冻结绑定不匹配")
        context = self._context(scope)
        obs = self._observer.observe(context, binding)
        if obs.status == "WITHHELD":
            raise PermissionError("持久观察不可信")
        if obs.sequence is None or obs.start_seq is None or obs.sequence <= obs.start_seq:
            raise PermissionError("观察版本未超过冻结起点")
        facts = []
        by_id = {fact.public_goal_id: fact for fact in obs.goals}
        for goal in spec.goals:
            fact = by_id.get(goal.public_goal_id)
            if fact is None:
                raise PermissionError("缺少目标观察")
            count = None if obs.status == "UNKNOWN" else fact.observed_count
            facts.append(GoalObservation(public_goal_id=goal.public_goal_id, kind=goal.kind,
                                         count=count))
        return VerifiedSnapshot(scope=scope, goal_sha256=spec.content_sha256,
                                start_seq=obs.start_seq, sequence=obs.sequence,
                                revision=obs.revision or binding.frozen_revision,
                                facts=tuple(facts))

    def feedback(self, context: ExecutionContext) -> PublicFeedback:
        binding = self._registry.current_binding(context)
        if binding is None:
            return _withheld_feedback()
        return PublicFeedbackProjector(self, binding.scope).project(
            binding.spec, binding.binding_id)

    # V1.4-A TrustedScoringAdapter
    def _goal_set(self, binding: PublicGoalBinding) -> GoalSet:
        if len(binding.spec.goals) != 1 or binding.spec.goals[0].kind != "TODO_COUNT":
            raise PermissionError("unsupported_public_goal_set")
        goals = tuple(Goal(public_goal_id=goal.public_goal_id,
                           definition_hash=_goal_hash(goal),
                           credit_unit=goal.public_goal_id,
                           dependencies=()) for goal in binding.spec.goals)
        return GoalSet(
            scope=ScoringScope(task_id=binding.scope.task_id,
                               owner_id=binding.scope.owner_id,
                               session_id=binding.scope.session_id),
            goal_set_ref=binding.binding_id, version=str(binding.spec.freeze_seq),
            measurement_version="1.4-a", goals=goals, complete=True,
            # 仅支持一个目标；没有跨目标独立性可声称，也不把字符串当证明。
            independence_ref="SINGLE_GOAL_TRIVIAL",
        )

    def _observation(self, scope: Scope) -> tuple[PublicGoalBinding, TodoObservation]:
        binding = self._binding(scope)
        if binding is None:
            raise PermissionError("没有可信冻结绑定")
        obs = self._observer.observe(self._context(scope), binding)
        if obs.status == "WITHHELD":
            raise PermissionError("持久观察不可信")
        return binding, obs

    def read_verified(self, goal_ref: str, observation_ref: str,
                      scope: ScoringScope) -> ScoringInput:
        public_scope = Scope(task_id=scope.task_id, owner_id=scope.owner_id,
                             session_id=scope.session_id)
        binding, obs = self._observation(public_scope)
        if goal_ref != binding.binding_id or observation_ref != obs.observation_ref:
            raise PermissionError("观察引用不是当前可信观察")
        if obs.sequence is None or obs.sequence <= binding.spec.freeze_seq:
            raise PermissionError("观察版本未超过冻结版本")
        facts = {fact.public_goal_id: fact for fact in obs.goals}
        states = []
        for goal in binding.spec.goals:
            if obs.status == "UNKNOWN":
                states.append(GoalState(public_goal_id=goal.public_goal_id, status="UNKNOWN"))
                continue
            if obs.status == "WITHHELD":
                raise PermissionError("观察不可评分")
            fact = facts.get(goal.public_goal_id)
            if fact is None or fact.observed_count is None:
                raise PermissionError("缺少目标事实")
            status = "MET" if fact.observed_count == goal.expected_count else "NOT_MET"
            states.append(GoalState(public_goal_id=goal.public_goal_id, status=status))
        return ScoringInput(
            goal_set=self._goal_set(binding), observation_ref=obs.observation_ref,
            revision=obs.revision or binding.frozen_revision,
            sequence=obs.sequence, execution_start=binding.spec.freeze_seq + 1,
            freeze_seq=binding.spec.freeze_seq, states=tuple(states),
        )

    def read_historical(self, goal_ref: str, observation_ref: str,
                        scope: ScoringScope) -> ScoringInput:
        # 旧观察 ref 不能凭重算 hash 恢复新鲜性；本窄版本不保存可比较历史。
        raise PermissionError("历史观察未认证")

    def score(self, context: ExecutionContext) -> ProgressScore:
        binding = self._registry.current_binding(context)
        if binding is None:
            return ProgressScore(score_status="WITHHELD", reason_code="UNVERIFIED_INPUT")
        obs = self._observer.observe(context, binding)
        if obs.status == "WITHHELD" or obs.observation_ref is None:
            return ProgressScore(score_status="WITHHELD", reason_code="UNVERIFIED_INPUT")
        scoring_scope = ScoringScope(task_id=binding.scope.task_id,
                                     owner_id=binding.scope.owner_id,
                                     session_id=binding.scope.session_id)
        return ProgressScorer(self, scoring_scope).score(binding.binding_id,
                                                         obs.observation_ref)

    def score_json(self, context: ExecutionContext) -> str:
        return serialize_progress(self.score(context))

    project_feedback = feedback
    score_public = score
