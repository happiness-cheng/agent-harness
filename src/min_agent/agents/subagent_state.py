"""MA-P5-007：子 Agent 状态与生命周期（**默认关闭、同进程、协作式取消**）。

与 ``SubagentContract`` 轨迹的分工（不重复建设）：

* **契约轨迹**：每个子契约一份，事件级审计，字段固定为 ``TRAJECTORY_FIELDS``；
* **progress 视图**：跨 worker 的轻量调度视图，主 Agent 读**一份** JSONL 就能看到
  全部 worker 的当前状态与最后更新时间，用于卡住检测与取消协调。

本模块只写 ``seq / worker_id / subtask_id / role / state / step / at /
monotonic / note``，**不写任务正文、模型输出、证据正文或凭据**。

取消语义（必须如实说明）：

* ``request_cancel`` 只设置令牌；worker 必须在**检查点**轮询
  ``check_cancelled()`` 才会停下。正在阻塞的模型调用不会被立即打断，
  **不覆盖真实进程，也不等于强制终止**。
* ``acknowledge`` 必须由 worker 显式调用；超时未 ack 标 ``unacknowledged``，
  调用方应视为「可能仍在运行」，不得当作已停止。
* 已取消 / 已过期后到达的结果不改变终态，只记 ``late_result``。
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

__all__ = [
    "WorkerState", "TerminalState", "CancelledByParent", "WorkerHandle",
    "LifecycleRegistry", "PROGRESS_FIELDS",
]

WorkerState = Literal[
    "pending", "running", "waiting_input", "completed", "failed", "cancelled", "expired",
]
TerminalState = Literal["completed", "failed", "cancelled", "expired"]

PROGRESS_FIELDS = (
    "seq", "worker_id", "subtask_id", "role", "state", "step", "at", "monotonic", "note",
)
"""progress 白名单。刻意不含 objective / artifact / summary / 凭据。"""

TERMINAL_STATES = frozenset({"completed", "failed", "cancelled", "expired"})
ACTIVE_STATES = frozenset({"pending", "running", "waiting_input"})


class CancelledByParent(RuntimeError):
    """父级已请求取消；worker 在检查点抛出，不得继续动作。"""


class WorkerHandle:
    """单个 worker 的状态句柄；所有状态变更都落一条 progress 记录。"""

    def __init__(self, registry: "LifecycleRegistry", worker_id: str,
                 subtask_id: str, role: str, path: Path) -> None:
        self._registry = registry
        self._worker_id = worker_id
        self._subtask_id = subtask_id
        self._role = role
        self._path = path
        self._state: WorkerState = "pending"
        self._seq = 0
        self._cancel_requested = False
        self._cancel_requested_monotonic = 0.0
        self._acknowledged = False
        self._ack_reason = ""
        self._finished = False
        self._last_monotonic = registry._clock()
        self._lock = threading.RLock()

    @property
    def worker_id(self) -> str:
        return self._worker_id

    @property
    def subtask_id(self) -> str:
        return self._subtask_id

    @property
    def role(self) -> str:
        return self._role

    @property
    def state(self) -> WorkerState:
        return self._state

    @property
    def cancelled(self) -> bool:
        return self._cancel_requested

    @property
    def acknowledged(self) -> bool:
        return self._acknowledged

    @property
    def cancel_requested_monotonic(self) -> float:
        """取消请求的时刻；ack 超时从这一刻算，而不是最后一次活动。"""
        return self._cancel_requested_monotonic

    @property
    def ack_reason(self) -> str:
        return self._ack_reason

    @property
    def last_updated_monotonic(self) -> float:
        return self._last_monotonic

    def mark_running(self, step: str = "dispatched") -> None:
        self._transition("running", step)

    def mark_step(self, step: str) -> None:
        if self._state in TERMINAL_STATES:
            return
        self._transition(self._state if self._state != "pending" else "running", step)

    def mark_waiting(self, step: str = "waiting_input") -> None:
        if self._state in TERMINAL_STATES:
            return
        self._transition("waiting_input", step)

    def request_cancel(self, reason: str = "parent_cancelled") -> bool:
        with self._lock:
            if self._state in TERMINAL_STATES:
                return False
            self._cancel_requested = True
            self._cancel_requested_monotonic = self._registry._clock()
        self._record("running" if self._state == "pending" else self._state,
                     "cancel_requested", reason)
        return True

    def check_cancelled(self) -> None:
        """检查点调用；已请求取消则抛出，worker 必须停止后续动作。"""
        if self._cancel_requested:
            raise CancelledByParent(self._ack_reason or "parent_cancelled")

    def acknowledge(self, reason: str = "cancelled") -> bool:
        """worker 在安全点确认已停止；未请求取消或已终态时返回 False。"""
        with self._lock:
            if not self._cancel_requested or self._state in TERMINAL_STATES:
                return False
            self._acknowledged = True
            self._ack_reason = reason[:200]
            self._finished = True
            self._state = "cancelled"
            self._last_monotonic = self._registry._clock()
            self._seq += 1
        self._write(state="cancelled", step="cancel_acked", note=reason)
        return True

    def mark_finished(self, state: TerminalState, note: str = "") -> bool:
        """写入终态；已取消/已过期时只记 late_result，不覆盖终态。"""
        with self._lock:
            if self._state in TERMINAL_STATES:
                late = state != self._state
                if late:
                    self._record(self._state, "late_result", note or state)
                return False
            self._state = state
            self._finished = True
            self._last_monotonic = self._registry._clock()
            self._seq += 1
        self._write(state=state, step="finished", note=note)
        return True

    def _transition(self, state: WorkerState, step: str, note: str = "") -> None:
        with self._lock:
            if self._state in TERMINAL_STATES:
                return
            self._state = state
            self._last_monotonic = self._registry._clock()
        self._record(state, step, note)

    def _record(self, state: str, step: str, note: str = "") -> None:
        self._write(state=state, step=step, note=note)

    def _write(self, *, state: str, step: str, note: str = "") -> None:
        with self._lock:
            self._seq += 1
            row = {
                "seq": self._seq,
                "worker_id": self._worker_id,
                "subtask_id": self._subtask_id,
                "role": self._role,
                "state": state,
                "step": step,
                "at": self._registry._wall_clock().isoformat(timespec="seconds"),
                "monotonic": round(self._last_monotonic, 3),
                "note": note[:200],
            }
        if set(row) - set(PROGRESS_FIELDS):
            raise RuntimeError("progress 记录包含白名单外字段")
        payload = json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
        with self._registry._write_lock:
            with self._path.open("a", encoding="utf-8") as stream:
                stream.write(payload)


class LifecycleRegistry:
    """管理多个 worker 的 progress、卡住检测、取消与 ack 收集。"""

    def __init__(self, root: str | Path, *, stall_after_seconds: float = 300.0,
                 ack_timeout_seconds: float = 30.0,
                 clock: Callable[[], float] = time.monotonic,
                 wall_clock: Callable[[], datetime] | None = None,
                 prefix: str = "progress") -> None:
        directory = Path(root)
        if not directory.is_dir():
            raise ValueError("progress 目录不存在；不自动创建目录")
        if stall_after_seconds <= 0 or ack_timeout_seconds <= 0:
            raise ValueError("阈值必须为正数")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = directory / f"{prefix}-{stamp}.jsonl"
        if path.exists():
            raise ValueError("progress 文件已存在；不覆盖历史证据")
        self._path = path
        self._clock = clock
        self._wall_clock = wall_clock or (lambda: datetime.now(timezone.utc))
        self._stall_after = stall_after_seconds
        self._ack_timeout = ack_timeout_seconds
        self._handles: list[WorkerHandle] = []
        self._unacked_marked: set[str] = set()
        self._lock = threading.RLock()
        # 同一 progress 文件的所有写入共用一把锁：Windows 上多线程同时 open("a")
        # 会抛 PermissionError，仅靠每个 handle 自己的锁不够。
        self._write_lock = threading.Lock()

    @property
    def progress_path(self) -> Path:
        return self._path

    @property
    def stall_after_seconds(self) -> float:
        return self._stall_after

    def start(self, subtask_id: str, role: str) -> WorkerHandle:
        with self._lock:
            worker_id = f"w-{len(self._handles) + 1:03d}-{subtask_id}"
            handle = WorkerHandle(self, worker_id, subtask_id, role, self._path)
            self._handles.append(handle)
        handle._record("pending", "registered")
        return handle

    def handles(self) -> tuple[WorkerHandle, ...]:
        return tuple(self._handles)

    def get(self, worker_id: str) -> WorkerHandle | None:
        return next((item for item in self._handles if item.worker_id == worker_id), None)

    def request_cancel(self, worker_id: str, reason: str = "parent_cancelled") -> bool:
        handle = self.get(worker_id)
        if handle is None:
            return False
        return handle.request_cancel(reason)

    def request_cancel_all(self, reason: str = "parent_cancelled") -> tuple[str, ...]:
        requested: list[str] = []
        for handle in self._handles:
            if handle.request_cancel(reason):
                requested.append(handle.worker_id)
        return tuple(requested)

    def collect_acks(self, *, timeout_seconds: float | None = None
                     ) -> dict[str, bool | None]:
        """返回每个被请求取消的 worker 是否 ack；None 表示尚未超时但未 ack。"""
        limit = self._ack_timeout if timeout_seconds is None else timeout_seconds
        now = self._clock()
        result: dict[str, bool | None] = {}
        for handle in self._handles:
            if not handle.cancelled:
                continue
            if handle.acknowledged:
                result[handle.worker_id] = True
                continue
            waited = now - handle.cancel_requested_monotonic
            if waited >= limit:
                result[handle.worker_id] = False
                if handle.worker_id not in self._unacked_marked:
                    handle._record(handle.state, "unacknowledged", "ack_timeout")
                    self._unacked_marked.add(handle.worker_id)
            else:
                result[handle.worker_id] = None
        return result

    def stalled(self) -> tuple[str, ...]:
        now = self._clock()
        return tuple(
            handle.worker_id for handle in self._handles
            if handle.state in ACTIVE_STATES
            and now - handle.last_updated_monotonic >= self._stall_after
        )

    def expire_overdue(self) -> tuple[str, ...]:
        expired: list[str] = []
        for worker_id in self.stalled():
            handle = self.get(worker_id)
            if handle is None:
                continue
            if handle.mark_finished("expired", "stalled"):
                expired.append(worker_id)
        return tuple(expired)

    def snapshot(self) -> tuple[dict[str, object], ...]:
        return tuple({
            "worker_id": handle.worker_id,
            "subtask_id": handle.subtask_id,
            "role": handle.role,
            "state": handle.state,
            "cancelled": handle.cancelled,
            "acknowledged": handle.acknowledged,
            "last_updated_monotonic": round(handle.last_updated_monotonic, 3),
        } for handle in self._handles)
