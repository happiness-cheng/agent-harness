"""仅保存固定诊断元数据的内存 trace，不保存请求或响应正文。"""

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from hashlib import sha256
from time import perf_counter
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

_active_span = ContextVar("agent_harness_trace_span", default=None)

TraceStatus = Literal["completed", "waiting_user", "partial", "failed", "cancelled"]
ErrorCode = Literal[
    "model_call_failed", "invalid_model_response", "tool_request_failed", "output_limit",
    "tool_execution_not_supported", "duplicate_tool_call_id", "invalid_tool_result",
    "tool_execution_failed", "tool_repair_limit", "model_refusal", "model_error",
    "unknown_tool", "invalid_arguments", "interrupted", "unexpected_error",
    "plan_read_only", "goal_unconfirmed", "waiting_user", "scope_not_confirmed",
    "action_already_consumed", "legacy_confirmation_requires_new_proposal",
    "budget_exhausted",
    "budget_persistence_failed",
    "budget_checkpoint", "checkpoint_invalid_response",
    "user_stopped", "no_progress",
    "reminder_time_required", "context_limit", "network_not_authorized",
    "steer_pending",
]
ExecutionState = Literal["not_started", "returned", "unknown"]


class TraceContext(BaseModel):
    """由可信调用方提供的诊断标签，不承担资源授权。"""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    session_id: UUID | None = None
    task_id: UUID | None = None


class TraceEvent(BaseModel):
    """一次已结束操作的固定字段记录。"""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", allow_inf_nan=False)
    sequence: int = Field(ge=1)
    run_id: UUID
    call_id: UUID
    parent_call_id: UUID | None = None
    session_id: UUID | None = None
    task_id: UUID | None = None
    operation: Literal["run", "model", "tool"]
    status: TraceStatus
    duration_ms: float = Field(ge=0)
    error_code: ErrorCode | None = None
    execution_state: ExecutionState | None = None
    source_call_ref: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    tool_ref: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


@dataclass
class TraceSpan:
    call_id: UUID = field(default_factory=uuid4)
    status: TraceStatus = "failed"
    error_code: ErrorCode | None = None
    execution_state: ExecutionState | None = None

    def finish(
        self, status: TraceStatus, error_code: ErrorCode | None = None,
        execution_state: ExecutionState | None = None,
    ) -> None:
        self.status = status
        self.error_code = error_code
        self.execution_state = execution_state


class TraceRecorder:
    def __init__(self, *, clock: Callable[[], float] = perf_counter) -> None:
        self._clock = clock
        self._events: list[TraceEvent] = []

    @property
    def events(self) -> tuple[TraceEvent, ...]:
        return tuple(self._events)

    def to_json(self) -> str:
        return json.dumps(
            [event.model_dump(mode="json") for event in self._events],
            ensure_ascii=False, allow_nan=False,
        )

    @contextmanager
    def span(
        self, operation: Literal["run", "model", "tool"], *, run_id: UUID,
        context: TraceContext, parent_call_id: UUID | None = None,
        source_call_id: str | None = None, tool_name: str | None = None,
    ) -> Iterator[TraceSpan]:
        active = _active_span.get()
        if parent_call_id is None and active is not None and active[0] is self:
            parent_call_id = active[1]
        span = TraceSpan()
        token = _active_span.set((self, span.call_id))
        started = self._clock()
        try:
            yield span
        except BaseException as error:
            interrupted = not isinstance(error, Exception)
            span.finish(
                "cancelled" if interrupted else "failed",
                "interrupted" if interrupted else "unexpected_error",
                "unknown" if operation == "tool" else None,
            )
            raise
        finally:
            try:
                self._events.append(TraceEvent(
                    sequence=len(self._events) + 1, run_id=run_id, call_id=span.call_id,
                    parent_call_id=parent_call_id, session_id=context.session_id, task_id=context.task_id,
                    operation=operation, status=span.status,
                    duration_ms=(self._clock() - started) * 1000,
                    error_code=span.error_code, execution_state=span.execution_state,
                    source_call_ref=sha256(source_call_id.encode("utf-8")).hexdigest() if source_call_id is not None else None,
                    tool_ref=sha256(tool_name.encode("utf-8")).hexdigest() if tool_name is not None else None,
                ))
            finally:
                _active_span.reset(token)


def record_run_trace(sessions, context, trace, *, run_id, budget_id, status, limit=20):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("trace保留数量无效")
    with sessions.control(context):
        records = sessions.extension(context, "trace_runs", [])
        events = json.loads(trace.to_json())
        records = [record for record in records if record["run_id"] != str(run_id)]
        records.append({"run_id": str(run_id), "logical_task_id": str(budget_id) if budget_id else None,
            "status": status, "event_count": len(events), "truncated": len(events) > 2000, "events": events[:2000]})
        sessions.set_extensions(context, {"last_trace": events[:2000], "trace_runs": records[-limit:]})
