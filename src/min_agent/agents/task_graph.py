"""MA-P5-010 / MA-P5-011：任务图与状态机 —— **模型画规划，规则负责管**。

职责划分（DECISION_LOG S1–S5，用户 2026-11-14 定稿）：

* **模型画规划**：通过 :class:`TaskTools` 的 ``create_task`` 建节点、
  ``update_task`` 加 ``blockedBy`` 边。两个任务能不能同时跑，由模型在
  **加不加边**时一次性决定；规则不看文件、不看资源、不做冲突判定。
* **规则负责管**：只做机械校验与查询——ID 合法、文件排他创建、依赖存在、
  无自依赖、无环、整次校验后统一保存、重复边安全、状态机不可绕过、
  ``can_start`` 只读查询（**唯一判据：依赖项是否全部 completed**）。

MA-P5-011 变更：``Task`` 上的 ``writes`` / ``reads`` / ``resources_declared``
降级为**零读取的兼容占位**，仅为让 MA-P5-010 时期写下的任务图 JSON 仍能被
``_decode`` 读回；本模块与调度器都不再用它们做任何判定。

两阶段建图沿用用户给出的理由：模型可能在一条回复里同时发出多个工具调用，
同级调用在任何工具结果产生前就已确定，因此某个 ``create_task`` 无法直接
使用另一个调用刚生成的 ID；必须先建完所有节点，再用返回的 ID 加边。

边界：

* 任务图 JSON 含任务正文（用户参考设计如此），与 ``subagent_state`` 的
  progress 白名单不同；本模块默认不启用、不接主循环。
* 把并行判断权完全交给模型后，**模型漏加一条 ``blockedBy`` 边就会错误并行**，
  规则不再兜底。跨文件语义冲突（A 改函数签名、B 按旧签名调用）同样查不出。
  这两项是已知边界，靠后续合并后验证 / 独立审查处理，本模块不宣称解决。
* ``failed`` 是相对三态参考实现的扩展终态：worker 未通过时下游保持阻塞，
  不会因父任务永远停在 ``in_progress`` 而假装卡死。
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import uuid
from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal, Sequence

__all__ = [
    "Task", "TaskStatus", "TaskStore", "TaskTools", "Completion",
    "TaskGraphError", "TaskNotFound", "TaskGraphViolation",
    "TASK_ID_PATTERN",
]

TaskStatus = Literal["pending", "in_progress", "completed", "failed"]
TASK_ID_PATTERN = re.compile(r"^task_[0-9a-f]{8}$")


class TaskGraphError(RuntimeError):
    """任务图基础错误。"""


class TaskNotFound(TaskGraphError):
    """任务文件不存在或 ID 非法。"""


class TaskGraphViolation(TaskGraphError):
    """规则校验失败：状态机 / 依赖 / owner / 环。"""


@dataclass(frozen=True)
class Task:
    """一个任务节点；``blockedBy`` 是模型写下的显式依赖边。"""

    id: str
    subject: str
    description: str = ""
    status: TaskStatus = "pending"
    owner: str | None = None
    blockedBy: tuple[str, ...] = ()
    # 模型在规划时声明的资源范围。declared=False 表示未声明，调度会保守串行。
    writes: tuple[str, ...] = ()
    reads: tuple[str, ...] = ()
    resources_declared: bool = False
    error: str = ""


@dataclass(frozen=True)
class Completion:
    """``complete_task`` 的结果：终态任务 + 本次刚被解锁的下游任务。"""

    task: Task
    unblocked: tuple[str, ...]


class TaskStore:
    """``.tasks/`` 目录的 JSON 任务存储；线程安全、排他创建、原子替换。"""

    def __init__(self, directory: str | Path) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    @property
    def directory(self) -> Path:
        return self._dir

    def _path(self, task_id: str) -> Path:
        # ID 白名单同时挡住路径穿越；不拼接任意字符串进文件名。
        if not TASK_ID_PATTERN.fullmatch(task_id):
            raise TaskNotFound(f"invalid_task_id:{task_id!r}")
        return self._dir / f"{task_id}.json"

    @staticmethod
    def _decode(raw: str) -> Task:
        data = json.loads(raw)
        return Task(
            id=data["id"], subject=data["subject"],
            description=data.get("description", ""),
            status=data.get("status", "pending"),
            owner=data.get("owner"),
            blockedBy=tuple(data.get("blockedBy", ())),
            writes=tuple(data.get("writes", ())),
            reads=tuple(data.get("reads", ())),
            resources_declared=bool(data.get("resources_declared", False)),
            error=data.get("error", ""),
        )

    def _write_new(self, task: Task) -> Task:
        """排他创建；ID 已存在就重新生成（用户参考设计要求），返回真实 ID 的任务。"""
        for _ in range(32):
            task_id = "task_" + secrets.token_hex(4)
            candidate = replace(task, id=task_id)
            body = json.dumps(asdict(candidate), ensure_ascii=False, sort_keys=True)
            path = self._dir / f"{task_id}.json"
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(body)
            return candidate
        raise TaskGraphError("task_id_exhausted")

    def create(self, subject: str, description: str = "", *,
               writes: Sequence[str] = (), reads: Sequence[str] = (),
               resources_declared: bool = False) -> Task:
        """建节点。新任务 ``blockedBy`` 固定为空，返回运行时生成的 ID。"""
        if not isinstance(subject, str) or not subject.strip():
            raise TaskGraphViolation("subject_required")
        clean_writes = self._clean_resources(writes, "writes")
        clean_reads = self._clean_resources(reads, "reads")
        with self._lock:
            placeholder = Task(
                id="task_00000000", subject=subject.strip(),
                description=description, writes=clean_writes, reads=clean_reads,
                resources_declared=resources_declared,
            )
            return self._write_new(placeholder)

    @staticmethod
    def _clean_resources(values: Sequence[str], label: str) -> tuple[str, ...]:
        if isinstance(values, str) or not isinstance(values, Iterable):
            raise TaskGraphViolation(f"{label}_must_be_sequence")
        out: list[str] = []
        for item in values:
            if not isinstance(item, str) or not item.strip() or len(item) > 512:
                raise TaskGraphViolation(f"invalid_resource:{label}")
            value = item.strip()
            if value not in out:
                out.append(value)
        return tuple(out)

    def _save(self, task: Task) -> None:
        """原子替换，避免并发读到半写的 JSON。"""
        path = self._path(task.id)
        tmp = self._dir / f".{task.id}.{uuid.uuid4().hex}.tmp"
        tmp.write_text(json.dumps(asdict(task), ensure_ascii=False, sort_keys=True),
                       encoding="utf-8")
        os.replace(tmp, path)

    def load(self, task_id: str) -> Task:
        path = self._path(task_id)
        try:
            return self._decode(path.read_text(encoding="utf-8"))
        except FileNotFoundError as missing:
            raise TaskNotFound(f"task_not_found:{task_id}") from missing

    def list(self) -> tuple[Task, ...]:
        with self._lock:
            return tuple(
                self._decode(path.read_text(encoding="utf-8"))
                for path in sorted(self._dir.glob("task_*.json"))
            )

    def update_dependencies(self, task_id: str,
                            add_blocked_by: Sequence[str]) -> Task:
        """加边：**整次校验通过后统一保存**；重复边安全，不产生重复。"""
        if isinstance(add_blocked_by, str) or not isinstance(add_blocked_by, Iterable):
            raise TaskGraphViolation("addBlockedBy_must_be_sequence")
        added = tuple(dict.fromkeys(add_blocked_by))
        with self._lock:
            task = self.load(task_id)
            if task.status != "pending" or task.owner is not None:
                raise TaskGraphViolation(f"task_not_editable:{task.status}")
            for dep in added:
                self._path(dep)  # 顺带校验 ID 格式
                if dep == task_id:
                    raise TaskGraphViolation("self_dependency")
                if not self._path(dep).exists():
                    raise TaskGraphViolation(f"unknown_dependency:{dep}")
            merged = tuple(dict.fromkeys((*task.blockedBy, *added)))
            candidate = replace(task, blockedBy=merged)
            self._assert_acyclic(candidate)
            self._save(candidate)
            return candidate

    def _assert_acyclic(self, candidate: Task) -> None:
        """整图 DFS 三色标记；只做结构检查，不判断任务语义。"""
        graph = {item.id: item.blockedBy for item in self.list()}
        graph[candidate.id] = candidate.blockedBy
        state: dict[str, int] = {}

        def visit(node: str) -> None:
            mark = state.get(node, 0)
            if mark == 1:
                raise TaskGraphViolation("dependency_cycle")
            if mark == 2:
                return
            state[node] = 1
            for dep in graph.get(node, ()):
                if dep in graph:
                    visit(dep)
            state[node] = 2

        for node in graph:
            visit(node)

    def incomplete_dependencies(self, task: Task) -> tuple[str, ...]:
        """只读查询：任一前置不是 completed（或文件不存在）就阻塞。"""
        pending: list[str] = []
        for dep in task.blockedBy:
            try:
                upstream = self.load(dep)
            except TaskNotFound:
                pending.append(dep)
                continue
            if upstream.status != "completed":
                pending.append(dep)
        return tuple(pending)

    def can_start(self, task_id: str) -> bool:
        return not self.incomplete_dependencies(self.load(task_id))

    def claim(self, task_id: str, owner: str = "agent") -> Task:
        """pending → in_progress；非 pending 或依赖未完成一律拒绝。"""
        if not owner or not isinstance(owner, str):
            raise TaskGraphViolation("owner_required")
        with self._lock:
            task = self.load(task_id)
            if task.status != "pending":
                raise TaskGraphViolation(f"cannot_claim:{task.status}")
            blocked = self.incomplete_dependencies(task)
            if blocked:
                raise TaskGraphViolation(f"blocked_by:{','.join(blocked)}")
            claimed = replace(task, status="in_progress", owner=owner)
            self._save(claimed)
            return claimed

    def _ready_set(self) -> set[str]:
        return {item.id for item in self.list()
                if item.status == "pending" and item.blockedBy
                and self.can_start(item.id)}

    def complete(self, task_id: str, owner: str = "agent") -> Completion:
        """in_progress → completed；校验 owner，返回本次刚解锁的下游。"""
        with self._lock:
            task = self.load(task_id)
            if task.status != "in_progress":
                raise TaskGraphViolation(f"cannot_complete:{task.status}")
            if task.owner != owner:
                raise TaskGraphViolation(f"owned_by:{task.owner}")
            before = self._ready_set()
            finished = replace(task, status="completed")
            self._save(finished)
            after = self._ready_set()
            return Completion(task=finished,
                              unblocked=tuple(sorted(after - before)))

    def fail(self, task_id: str, owner: str = "agent", reason: str = "") -> Task:
        """失败终态（P6 扩展）；下游仍被 ``incomplete_dependencies`` 阻塞。"""
        with self._lock:
            task = self.load(task_id)
            if task.status != "in_progress":
                raise TaskGraphViolation(f"cannot_fail:{task.status}")
            if task.owner != owner:
                raise TaskGraphViolation(f"owned_by:{task.owner}")
            failed = replace(task, status="failed", error=reason[:500])
            self._save(failed)
            return failed

    def snapshot(self) -> tuple[dict[str, object], ...]:
        return tuple(asdict(item) for item in self.list())


class TaskTools:
    """面向模型/调度器的工具面；方法名对齐用户给出的参考实现。"""

    def __init__(self, store: TaskStore) -> None:
        self._store = store

    @property
    def store(self) -> TaskStore:
        return self._store

    def create_task(self, subject: str, description: str = "", *,
                    writes: Sequence[str] = (), reads: Sequence[str] = (),
                    resources_declared: bool = False) -> Task:
        return self._store.create(subject, description, writes=writes, reads=reads,
                                  resources_declared=resources_declared)

    def update_task(self, task_id: str,
                    addBlockedBy: Sequence[str]) -> Task:
        return self._store.update_dependencies(task_id, addBlockedBy)

    def can_start(self, task_id: str) -> bool:
        return self._store.can_start(task_id)

    def claim_task(self, task_id: str, owner: str = "agent") -> str:
        task = self._store.claim(task_id, owner)
        return f"Claimed {task.id} ({task.subject})"

    def complete_task(self, task_id: str, owner: str = "agent") -> str:
        done = self._store.complete(task_id, owner)
        message = f"Completed {done.task.id} ({done.task.subject})"
        if done.unblocked:
            names = [self._store.load(item).subject for item in done.unblocked]
            message += "\nUnblocked: " + ", ".join(names)
        return message

    def get_task(self, task_id: str) -> str:
        return json.dumps(asdict(self._store.load(task_id)), ensure_ascii=False,
                          indent=2)

    def list_tasks(self) -> str:
        lines = [f"{item.id} [{item.status}] {item.subject}"
                 f" blockedBy={list(item.blockedBy)}"
                 for item in self._store.list()]
        return "\n".join(lines)
