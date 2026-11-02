"""从 SQLiteStorage 一致读边界观察当前目标记录；不信任 Session 缓存或模型文字。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from min_agent.public_goal_registry import PublicGoalBinding
from min_agent.sessions import ExecutionContext
from min_agent.storage import SQLiteStorage


class ObservationContract(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", allow_inf_nan=False)


class GoalFact(ObservationContract):
    public_goal_id: str
    expected_count: int | None = None
    observed_count: int | None = None


class TodoObservation(ObservationContract):
    status: Literal["MET", "NOT_MET", "UNKNOWN", "WITHHELD"]
    reason_code: Literal[
        "CONFIRMED_STATE_MATCH", "CONFIRMED_STATE_MISMATCH",
        "INSUFFICIENT_EVIDENCE", "UNRESOLVED_OPERATION", "UNVERIFIED_OBSERVATION",
    ]
    binding_id: str | None = None
    revision: int | None = None
    start_seq: int | None = None
    sequence: int | None = None
    task_status: str | None = None
    goals: tuple[GoalFact, ...] = ()
    observation_ref: str | None = None


def _withheld(reason="UNVERIFIED_OBSERVATION"):
    return TodoObservation(status="WITHHELD", reason_code=reason)


def read_persisted_todo_snapshot(storage: SQLiteStorage, context: ExecutionContext):
    """兼容诊断入口：一致读事务返回 StoredSession，不把它当可信观察凭证。"""
    return storage.load(context)


class TodoObserver:
    """只读；读失败返回 WITHHELD，绝不转成 0 条或 NOT_MET。"""

    def __init__(self, storage: SQLiteStorage) -> None:
        if not isinstance(storage, SQLiteStorage):
            raise TypeError("TodoObserver 需要 SQLiteStorage")
        self._storage = storage

    def _matches(self, run, binding: PublicGoalBinding) -> bool:
        if str(run.snapshot.owner_id) != binding.scope.owner_id:
            return False
        if str(run.snapshot.session_id) != binding.scope.session_id:
            return False
        task = run.snapshot.task
        state = run.snapshot.work_state
        grant = state.grant
        if task is None or str(task.task_id) != binding.scope.task_id:
            return False
        if (str(task.logical_task_id) != binding.logical_task_id
                or str(task.task_id) != binding.attempt_id):
            return False
        if (state.proposal is None or state.confirmed_goal_id is None
                or str(state.proposal.proposal_id) != binding.proposal_id
                or str(state.confirmed_goal_id) != binding.proposal_id):
            return False
        if grant is None or str(grant.grant_id) != binding.grant_id:
            return False
        if (str(grant.proposal_id) != binding.proposal_id
                or str(grant.logical_task_id) != binding.logical_task_id
                or str(grant.attempt_id) != binding.attempt_id):
            return False
        budget = str(run.snapshot.active_budget_id) if run.snapshot.active_budget_id is not None else None
        if budget != binding.budget_id or (
                str(grant.budget_id) if grant.budget_id is not None else None) != binding.budget_id:
            return False
        allowed = {"running": "bound", "completed": "closed"}.get(task.status, "paused")
        if grant.status != allowed:
            return False
        return run.snapshot.revision >= binding.frozen_revision

    def observe(self, context: ExecutionContext, binding: PublicGoalBinding) -> TodoObservation:
        try:
            run = self._storage.load(context)
        except Exception:
            return _withheld()
        if not self._matches(run, binding):
            return _withheld()
        snapshot = run.snapshot
        task = snapshot.task
        extensions = snapshot.extensions
        operations = extensions.get("operations", {}) or {}
        unresolved = any(isinstance(item, dict) and item.get("status") == "unknown"
                         for item in operations.values())
        receipts = extensions.get("action_receipts", {}) or {}
        initial_todos = set(binding.initial_todo_ids)
        counted_ids = set()
        for item in run.todos:
            identity = str(item.id)
            if item.deleted or identity in initial_todos:
                continue
            receipt = next((value for value in receipts.values()
                            if isinstance(value, dict)
                            and str(value.get("resource_id")) == identity
                            and value.get("status") == "committed"), None)
            if receipt is None:
                continue
            if (str(receipt.get("logical_task_id")) != binding.logical_task_id):
                continue
            budget = receipt.get("budget_id")
            if (str(budget) if budget is not None else None) != binding.budget_id:
                continue
            counted_ids.add(identity)
        count = len(counted_ids)
        facts = tuple(GoalFact(public_goal_id=goal.public_goal_id,
                               expected_count=goal.expected_count,
                               observed_count=count if task.status == "completed" else None)
                      for goal in binding.spec.goals)
        start_seq = binding.spec.freeze_seq + 1
        sequence = snapshot.revision
        observation_ref = f"obs-{sequence}-{binding.spec.content_sha256[:16]}"
        if task.status == "running" or unresolved:
            reason = "UNRESOLVED_OPERATION" if unresolved else "INSUFFICIENT_EVIDENCE"
            status = "UNKNOWN"
        elif task.status != "completed":
            status, reason = "UNKNOWN", "INSUFFICIENT_EVIDENCE"
        else:
            status = "MET" if all(fact.observed_count == fact.expected_count for fact in facts) else "NOT_MET"
            reason = "CONFIRMED_STATE_MATCH" if status == "MET" else "CONFIRMED_STATE_MISMATCH"
        return TodoObservation(
            status=status, reason_code=reason, binding_id=binding.binding_id,
            revision=snapshot.revision, start_seq=start_seq, sequence=sequence,
            task_status=task.status, goals=facts, observation_ref=observation_ref,
        )
