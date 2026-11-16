"""最小子 Agent 契约（P5 第一批，默认关闭，全串行）。

本模块**不接入** ``HarnessApplication`` / ``Runtime`` 主循环，也不并行执行。
它只提供一次「窄委派」所需的可判定边界：结构化移交包、工具白名单、父子预算、
结果机械校验、信息增量判据、级联取消、迟到结果归属与脱敏轨迹。

设计依据见 ``docs/engineering-log/DECISION_LOG.md`` -> ``MA-P5-001``（D1–D7）：

* 上下文隔离 + 工具参数式移交，不建消息总线、不共享轨迹作为主通道；
* 全串行；写冲突继续由 ``operations.execute_once`` / SQLite CAS / 授权消费负责；
* 预算复用 ``BudgetSegment.child()``，``purpose`` 继承（不扩 ``Purpose`` 枚举）；
* 级联取消只在契约对象树内传播，**不调用**会话级 ``SessionEvents.stop()``；
* 轨迹只写白名单字段，不写正文、模型输出、用户数据或凭据。

限制（不得当作已实现）：

* ``EvidenceRef.ref`` 只校验格式（64 位小写 hex），**不证明证据内容真实**；
  真实性由调用方对原始回执计算，后续批次再由确定性 verifier 复核。
* 取消不终止真实进程；超时是合作式检查点，不是强杀，也不撤销已发生的写入。
* 预算用途无法在 ``by_purpose`` 单独分列，成本对照依赖本模块轨迹事件。
* 本模块是契约与检查点，不是执行器；真正跑模型/工具由调用方在白名单与预算内完成。
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json
import re
from threading import RLock
import time
from typing import Callable, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from min_agent.ledger.budget import BudgetLimits, BudgetSegment

__all__ = [
    "Handoff", "EvidenceRef", "SubagentResult", "SubagentContract",
    "SubagentViolation", "evaluate_information_gain", "receipt_ref",
    "MAX_DEPTH", "TRAJECTORY_FIELDS",
]

MAX_DEPTH = 2
"""主契约 + 一层子契约；沿用既有「不递归委派」边界。"""

TRAJECTORY_FIELDS = (
    "seq", "subagent_id", "kind", "state", "at",
    "model_calls", "tool_calls", "evidence_count", "gain",
    "error_code", "tool", "reason", "late", "depth",
)
"""轨迹白名单。刻意不含 objective / artifact / summary / 凭据 / 模型输出。"""

EvidenceKind = Literal["tool_receipt", "execution_result", "verification_verdict"]
GainVerdict = Literal["PASS", "FAIL", "UNKNOWN"]
ContractState = Literal["running", "completed", "cancelled", "expired"]
FinishVerdict = Literal["accepted", "late", "rejected"]

_REF_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_TOOL_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class _Strict(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class SubagentViolation(RuntimeError):
    """契约边界被触碰；调用方必须停止动作，不得降级放行。"""


def receipt_ref(raw: bytes) -> str:
    """把一条原始回执折叠成可引用的指纹；不保存正文。"""
    if not isinstance(raw, bytes):
        raise TypeError("回执必须是字节串")
    return sha256(raw).hexdigest()


class EvidenceRef(_Strict):
    """一条「单 Agent 生成时无法获得」的新信息引用。

    ``ref`` 是原始回执/执行结果/验证结论的 SHA-256，由可信调用方用
    :func:`receipt_ref` 计算。契约只校验格式，不校验内容真实性。
    """

    kind: EvidenceKind
    ref: str = Field(pattern=r"^[0-9a-f]{64}$")


class Handoff(_Strict):
    """父 Agent 交给子 Agent 的结构化移交包（上下文隔离，不共享聊天历史）。"""

    objective: str = Field(min_length=1, max_length=2000)
    constraints: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...]
    evidence_requirement: EvidenceKind = "tool_receipt"
    max_model_calls: int = Field(ge=1, le=100)
    max_tool_calls: int = Field(ge=1, le=100)
    deadline_seconds: float = Field(default=300.0, gt=0, le=86400)

    @field_validator("allowed_tools")
    @classmethod
    def _check_allowlist(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("工具白名单不能为空")
        if len(set(value)) != len(value):
            raise ValueError("工具白名单存在重复项")
        for name in value:
            if not _TOOL_PATTERN.fullmatch(name):
                raise ValueError("工具名无效")
        return value

    @field_validator("constraints")
    @classmethod
    def _check_constraints(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not isinstance(item, str) or len(item) > 500 for item in value):
            raise ValueError("约束项无效")
        return value


class SubagentResult(_Strict):
    """子 Agent 的结构化交付；``completed`` 不是证明，必须带新信息引用。"""

    status: Literal["completed", "partial", "failed"]
    artifact: str | None = Field(default=None, max_length=20000)
    evidence_refs: tuple[EvidenceRef, ...] = ()
    limitations: tuple[str, ...] = ()
    error_code: str | None = Field(default=None, max_length=64)


def evaluate_information_gain(result: SubagentResult, handoff: Handoff) -> GainVerdict:
    """判断这次委派是否带来单 Agent 生成时无法获得的新信息。

    * 未正常结束 -> ``UNKNOWN``（不猜测业务失败，也不转 PASS）；
    * 宣称 ``completed`` 但没有移交包声明的那一类证据 -> ``FAIL``（假成功）；
    * 完成且有匹配证据 -> ``PASS``。

    这是**格式与判据门**，不是事实门：证据内容是否真实仍须由确定性 verifier 复核。
    """
    if not isinstance(result, SubagentResult) or not isinstance(handoff, Handoff):
        raise TypeError("需要结构化的交付与移交包")
    if result.status != "completed":
        return "UNKNOWN"
    if not any(item.kind == handoff.evidence_requirement for item in result.evidence_refs):
        return "FAIL"
    return "PASS"


class SubagentContract:
    """一次窄委派的边界对象。默认不由任何生产入口创建，调用方须显式实例化。"""

    def __init__(
        self,
        parent_budget: BudgetSegment,
        handoff: Handoff,
        *,
        trajectory_path: str | Path | None = None,
        clock: Callable[[], float] = time.monotonic,
        _depth: int = 0,
    ) -> None:
        if not isinstance(parent_budget, BudgetSegment):
            raise TypeError("父预算必须是可信的 BudgetSegment")
        if not isinstance(handoff, Handoff):
            raise TypeError("移交包必须是 Handoff")
        if trajectory_path is not None:
            path = Path(trajectory_path)
            if not path.parent.is_dir():
                raise ValueError("轨迹目录不存在；不自动创建目录")
            if path.exists():
                raise ValueError("轨迹文件已存在；不覆盖历史证据")
        self._lock = RLock()
        self._clock = clock
        self._id: UUID = uuid4()
        self._handoff = handoff
        self._parent_budget = parent_budget
        self._trajectory_path = Path(trajectory_path) if trajectory_path is not None else None
        self._events: list[dict[str, object]] = []
        self._state: ContractState = "running"
        self._result: SubagentResult | None = None
        self._gain: GainVerdict | None = None
        self._children: list[SubagentContract] = []
        self._parent_contract: SubagentContract | None = None
        self._depth = int(_depth)
        self._deadline = clock() + handoff.deadline_seconds
        remaining = parent_budget.remaining
        self.budget: BudgetSegment = parent_budget.child(BudgetLimits(
            model_calls=min(handoff.max_model_calls, remaining.model_calls),
            tool_calls=min(handoff.max_tool_calls, remaining.tool_calls),
        ))
        self._record("created")

    @property
    def id(self) -> UUID:
        return self._id

    @property
    def handoff(self) -> Handoff:
        return self._handoff

    @property
    def state(self) -> ContractState:
        with self._lock:
            return self._state

    @property
    def result(self) -> SubagentResult | None:
        with self._lock:
            return self._result

    @property
    def gain(self) -> GainVerdict | None:
        with self._lock:
            return self._gain

    @property
    def events(self) -> tuple[dict[str, object], ...]:
        with self._lock:
            return tuple(dict(event) for event in self._events)

    @property
    def usage(self) -> dict[str, int]:
        """子片段用量快照，供成本对照；不依赖 by_purpose 分列。"""
        snapshot = self.budget.usage
        return {"model_calls": snapshot.model_calls, "tool_calls": snapshot.tool_calls}

    def _record(
        self,
        kind: str,
        *,
        evidence_count: int | None = None,
        gain: str | None = None,
        error_code: str | None = None,
        tool: str | None = None,
        reason: str | None = None,
        late: bool | None = None,
    ) -> None:
        """追加一条白名单事件。任何正文/凭据都不进入这里。"""
        usage = self.budget.usage
        event: dict[str, object] = {
            "seq": len(self._events) + 1,
            "subagent_id": str(self._id),
            "kind": str(kind)[:32],
            "state": self._state,
            "at": float(self._clock()),
            "model_calls": int(usage.model_calls),
            "tool_calls": int(usage.tool_calls),
            "depth": int(self._depth),
        }
        optional: dict[str, object] = {
            "evidence_count": evidence_count, "gain": gain, "error_code": error_code,
            "tool": tool, "reason": reason, "late": late,
        }
        for key, value in optional.items():
            if value is not None:
                event[key] = str(value)[:64] if isinstance(value, str) else value
        if set(event) - set(TRAJECTORY_FIELDS):
            raise SubagentViolation("轨迹字段超出白名单")
        line = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if self._trajectory_path is not None:
            try:
                with self._trajectory_path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(line + "\n")
            except OSError as error:
                raise SubagentViolation("轨迹写入失败，禁止继续委派") from error
        self._events.append(event)

    def _ensure_active(self) -> None:
        """调用方须持锁。超时是合作式检查点，不保证强杀。"""
        if self._state != "running":
            raise SubagentViolation(f"子任务已终止: {self._state}")
        if self._clock() >= self._deadline:
            self._state = "expired"
            self._record("expired")
            raise SubagentViolation("子任务已超时")

    def check_tool(self, name: str) -> None:
        """动作前硬门：只允许移交包白名单内的工具。"""
        with self._lock:
            self._ensure_active()
            if not isinstance(name, str) or name not in self._handoff.allowed_tools:
                self._record("tool_denied", reason="not_in_allowlist")
                raise SubagentViolation("工具不在白名单")
            self._record("tool_allowed", tool=name)

    def spawn_child(self, handoff: Handoff) -> "SubagentContract":
        """派生一层子契约；最多两层，避免递归委派。"""
        with self._lock:
            self._ensure_active()
            if self._depth + 1 >= MAX_DEPTH:
                self._record("child_denied", reason="depth_limit")
                raise SubagentViolation("超过最大委派深度")
            child = SubagentContract(
                self.budget, handoff, trajectory_path=None, clock=self._clock,
                _depth=self._depth + 1,
            )
            child._parent_contract = self
            self._children.append(child)
            self._record("child_spawned")
            return child

    def cancel(self, *, reason: str = "cancelled") -> bool:
        """级联取消整棵契约对象树，不留下孤儿；不触碰会话级 control，不终止真实进程。"""
        with self._lock:
            if self._state != "running":
                return False
            self._state = "cancelled"
            self._record("cancelled", reason=reason)
            children = list(self._children)
        for child in children:
            child.cancel(reason="parent_cancelled")
        return True

    def finish(self, result: SubagentResult) -> FinishVerdict:
        """归属一次交付；迟到或被拒的结果绝不写入。"""
        with self._lock:
            if self._state != "running":
                self._record("late_result", late=True)
                return "late"
            if self._clock() >= self._deadline:
                self._state = "expired"
                self._record("expired")
                return "late"
            if not isinstance(result, SubagentResult):
                raise TypeError("交付必须是 SubagentResult")
            gain = evaluate_information_gain(result, self._handoff)
            self._record(
                "finished", evidence_count=len(result.evidence_refs), gain=gain,
                error_code=result.error_code,
            )
            if gain == "FAIL":
                # 宣称完成但拿不出新信息：不放行，保持可重试状态。
                self._record("result_rejected", reason="no_information_gain")
                return "rejected"
            self._result = result
            self._gain = gain
            self._state = "completed"
            return "accepted"
