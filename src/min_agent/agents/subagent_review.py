"""S2 提议者-审核者：确定性验证循环（默认关闭、全串行、不调用模型）。

本模块刻意**不接入** ``HarnessApplication`` / ``Runtime`` 主循环。提议者由调用方
以 callable 注入（离线可用脚本/真实模型），审核者是确定性代码，**不是第二个模型**：
同模型 + 同脚手架 + 同上下文的第二意见属共因失效，不能当独立证据；差异化模型
只能作并列语义层，不得覆盖确定性硬门（见 MA-P5-003 隔离实验）。

审核者只接收四样东西：验收规格、提议者的结构化产物、可信 host 提供的原始回执、
以及可信 host 从工具轨迹生成的检查项绑定 ``host_checks``。
``ProposerOutput`` 用 ``extra="forbid"`` 保证消息历史/思考过程无法进入载荷，
从而打断「每转述一次都是有损重新编码」的错误级联。

**MA-P5-004 收紧**：``completed_checks`` 是提议者自报，降级为不可信提示，
**不再参与任何判定**。检查项是否完成改由 ``host_checks``（检查项名 → 原始回执
SHA-256）决定；缺绑定时判 ``UNKNOWN / checks_not_host_verified``，不回退自报、
不放行 PASS。这与项目纪律「完成只是模型的一句宣称，不是证明」一致。

三种过早终止形态在此可判定：

* 偷懒式假完成：``declared_complete`` 为真但 ``required_checks`` 未齐；
* 过早放弃：未声明完成且真实回执数少于 ``min_receipts``；
* 假成功：声明完成但证据与原始回执不匹配、或交付与要求标记不一致。

证据真实性在此兑现 S1 留下的承诺：``EvidenceRef.ref`` 必须等于某条原始回执的
SHA-256，不再只是格式门。四态合并复用 ``evaluation/verifier.py::verdict()``
（FAIL > UNKNOWN > PASS），保证已确定的违规不被证据不足抵消。

限制（不得当作已实现）：

* 只判结构化事实；语义质量、忠实度、开放式写作质量一律 ``UNKNOWN``
  （``ReviewVerdict.semantic_review`` 固定为 UNKNOWN）；
* 循环有界，由验证器判定能否停；不做并行、不抢占、不调用模型；
* 契约层取消不覆盖真实进程；超时仍是合作式检查点；
* ``host_checks`` 的**可信度由调用方保证**：本模块只校验其指纹是否对应真实回执，
  不负责从工具轨迹生成它；若 host 本身不可信，绑定也不可信；
* 原始回执整体不可用时，检查项指纹同样无法核验，判 UNKNOWN 而不指控伪造。
"""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from typing import Callable, Literal
import inspect

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from min_agent.evaluation.verifier import verdict as merge_verdict
from min_agent.agents.subagent import (
    EvidenceKind, EvidenceRef, SubagentContract, SubagentResult,
)

__all__ = [
    "ReviewSpec", "ProposerOutput", "ReviewVerdict", "LoopResult", "HostChecks",
    "deterministic_review", "review_loop", "Proposer",
]

Verdict = Literal["PASS", "FAIL", "UNKNOWN"]

HostChecks = Mapping[str, str]
"""可信 host 从工具轨迹生成的检查项绑定：检查项名 → 原始回执 SHA-256。

键存在表示 host 判定该检查项有可信依据；值必须能在 ``raw_receipts`` 中找到
内容自洽的真实回执。提议者自报的 ``completed_checks`` 不参与判定。
"""


class _Strict(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class ReviewSpec(_Strict):
    """审核者唯一可见的验收规格；不含提示词、消息历史或模型配置。"""

    objective: str = Field(min_length=1, max_length=2000)
    required_checks: tuple[str, ...] = ()
    required_markers: tuple[str, ...] = ()
    required_evidence_kind: EvidenceKind = "tool_receipt"
    min_receipts: int = Field(default=1, ge=0, le=100)
    max_rounds: int = Field(default=3, ge=1, le=10)

    @field_validator("required_checks")
    @classmethod
    def _check_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("检查项重复")
        if any(not item or len(item) > 64 for item in value):
            raise ValueError("检查项无效")
        return value

    @field_validator("required_markers")
    @classmethod
    def _check_markers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item or len(item) > 200 for item in value):
            raise ValueError("交付标记无效")
        return value

    @model_validator(mode="after")
    def _has_criteria(self) -> "ReviewSpec":
        if not self.required_checks and not self.required_markers and self.min_receipts == 0:
            raise ValueError("验收规格至少需要一项可判定判据")
        return self


class ProposerOutput(_Strict):
    """提议者交付。**结构上没有 messages / history / reasoning 字段**。"""

    artifact: str = Field(min_length=1, max_length=20000)
    declared_complete: bool
    completed_checks: tuple[str, ...] = ()
    evidence_refs: tuple[EvidenceRef, ...] = ()
    receipt_refs: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    stop_reason: Literal["done", "blocked", "budget", "unknown"] = "done"


class ReviewVerdict(_Strict):
    """审核结论：只有结构化 reason_code，没有审核者的思考过程。"""

    outcome: Verdict
    reason_code: str = Field(max_length=64)
    missing_checks: tuple[str, ...] = ()
    verified_evidence: int = Field(default=0, ge=0)
    semantic_review: Literal["UNKNOWN"] = "UNKNOWN"


class LoopResult(_Strict):
    """有界循环的结果；``rounds`` 是实际审核轮数。"""

    outcome: Verdict
    reason_code: str = Field(max_length=64)
    rounds: int = Field(ge=0)
    final_verdict: ReviewVerdict | None = None
    contract_finish: Literal["accepted", "late", "rejected"] | None = None


Proposer = Callable[[int, ReviewVerdict | None], ProposerOutput]


def _real_receipts(raw_receipts: dict[str, bytes]) -> dict[str, bytes]:
    """只保留指纹与内容自洽的回执；伪造 key 直接排除。"""
    if not isinstance(raw_receipts, dict):
        raise TypeError("原始回执必须是字典")
    return {
        key: value for key, value in raw_receipts.items()
        if isinstance(key, str) and isinstance(value, bytes)
        and sha256(value).hexdigest() == key
    }


def deterministic_review(
    spec: ReviewSpec,
    output: ProposerOutput,
    raw_receipts: dict[str, bytes],
    *,
    host_checks: HostChecks | None = None,
) -> ReviewVerdict:
    """确定性审核。任何一项 FAIL 都不被 UNKNOWN 抵消。

    ``host_checks`` 必须由可信 host 从工具轨迹生成；``output.completed_checks``
    是提议者自报，**不参与任何判定**。缺绑定时检查项不可判定，默认拒绝。
    """
    if not isinstance(spec, ReviewSpec) or not isinstance(output, ProposerOutput):
        raise TypeError("需要结构化的验收规格与交付")
    if host_checks is not None and not isinstance(host_checks, Mapping):
        raise TypeError("host_checks 必须是映射")
    real = _real_receipts(raw_receipts)
    findings: list[tuple[Verdict, str]] = []

    def add(state: Verdict, code: str) -> None:
        findings.append((state, code))

    verified = [item for item in output.evidence_refs if item.ref in real]
    unverifiable = len(verified) != len(output.evidence_refs)
    duplicate = len({item.ref for item in verified}) != len(verified)
    matching = [item for item in verified if item.kind == spec.required_evidence_kind]
    missing_markers = tuple(
        marker for marker in spec.required_markers if marker not in output.artifact
    )
    real_receipt_refs = [ref for ref in output.receipt_refs if ref in real]
    evidence_available = bool(raw_receipts)

    # 检查项只认可信 host 的绑定：键存在 = host 判定该项有依据；
    # 值必须对应内容自洽的原始回执。自报 completed_checks 一律忽略。
    explicitly_missing: tuple[str, ...] = ()
    unverified_refs: tuple[str, ...] = ()
    checks_unverifiable = False
    if spec.required_checks:
        if host_checks is None:
            checks_unverifiable = True
        else:
            explicitly_missing = tuple(
                name for name in spec.required_checks if name not in host_checks
            )
            unverified_refs = tuple(
                name for name in spec.required_checks
                if name in host_checks and host_checks[name] not in real
            )
    missing_checks = (
        spec.required_checks if checks_unverifiable
        else explicitly_missing + unverified_refs
    )

    if not evidence_available and output.evidence_refs:
        add("UNKNOWN", "raw_receipts_unavailable")
    elif unverifiable:
        add("FAIL", "forged_evidence")
    if duplicate:
        add("FAIL", "duplicate_evidence")
    if checks_unverifiable:
        # 缺绑定：保留未知，既不指控「偷懒」，更不放行 PASS。
        add("UNKNOWN", "checks_not_host_verified")

    if not output.declared_complete:
        if not evidence_available:
            # 无法核验是否「过早放弃」，保留未知，不指控提议者。
            add("UNKNOWN", "receipts_unavailable_for_stop")
        elif len(real_receipt_refs) < spec.min_receipts:
            add("FAIL", "premature_abandonment")
        else:
            add("UNKNOWN", "not_completed")
    else:
        # 键缺失是 host 已确定的事实，不依赖回执可用性，UNKNOWN 不能抵消。
        if explicitly_missing:
            add("FAIL", "lazy_partial_completion")
        if unverified_refs and evidence_available:
            add("FAIL", "forged_check_evidence")
        if missing_markers:
            add("FAIL", "delivery_mismatch")
        if evidence_available:
            if not matching:
                add("FAIL", "false_success")
            if not real_receipt_refs:
                add("UNKNOWN", "no_raw_receipts")

    if not findings:
        # 没有任何失败/未知发现 = 全部判据通过，不是「没配置检查」。
        # 空判据由 ReviewSpec 的 model_validator 在构造时拒绝。
        add("PASS", "verified")
    outcome: Verdict = merge_verdict(state for state, _ in findings)
    reason = next((code for state, code in findings if state == "FAIL"), None)
    if reason is None:
        reason = next((code for state, code in findings if state == "UNKNOWN"), "verified")
    return ReviewVerdict(
        outcome=outcome, reason_code=reason, missing_checks=missing_checks,
        verified_evidence=len(verified),
    )


def review_loop(
    spec: ReviewSpec,
    proposer: Proposer,
    raw_receipts: dict[str, bytes],
    *,
    contract: SubagentContract | None = None,
    host_checks: HostChecks | Callable[[int], HostChecks | None] | None = None,
) -> LoopResult:
    """有界串行循环：**由验证器判定能否停**，模型/提议者无权宣布完成。

    只在审核 PASS 时把交付归属给可选的 S1 契约；其余情况不写入结果，
    避免把「未跑完」或「迟到」包装成已完成。有检查项但缺 ``host_checks``
    时直接默认拒绝（``UNKNOWN``），不发起任何提议者轮次。

    ``host_checks`` 可以是固定映射，或按轮返回映射的 callable——真实 host
    每轮的工具轨迹可能变化，绑定也应随之重新生成；它始终由可信 host 提供，
    提议者的 ``completed_checks`` 永不参与判定。
    """
    if not isinstance(spec, ReviewSpec) or not callable(proposer):
        raise TypeError("需要 ReviewSpec 与可调用的 proposer")
    if contract is not None and not isinstance(contract, SubagentContract):
        raise TypeError("contract 必须是 SubagentContract")
    if spec.required_checks and host_checks is None:
        return LoopResult(
            outcome="UNKNOWN", reason_code="checks_not_host_verified", rounds=0,
        )
    last: ReviewVerdict | None = None
    for round_no in range(1, spec.max_rounds + 1):
        resolved_checks = host_checks(round_no) if callable(host_checks) else host_checks
        if resolved_checks is not None and not isinstance(resolved_checks, Mapping):
            raise TypeError("host_checks 解析后必须是映射")
        if contract is not None and contract.state != "running":
            return LoopResult(
                outcome="UNKNOWN", reason_code="contract_terminated",
                rounds=round_no - 1, final_verdict=last,
            )
        try:
            output = proposer(round_no, last)
        except Exception:
            return LoopResult(
                outcome="UNKNOWN", reason_code="proposer_error",
                rounds=round_no, final_verdict=last,
            )
        last = deterministic_review(spec, output, raw_receipts, host_checks=resolved_checks)
        if last.outcome == "PASS":
            finish = None
            if contract is not None:
                finish = contract.finish(SubagentResult(
                    status="completed", artifact=output.artifact,
                    evidence_refs=output.evidence_refs,
                ))
            return LoopResult(
                outcome="PASS", reason_code="verified", rounds=round_no,
                final_verdict=last, contract_finish=finish,
            )
    # 轮数耗尽：保留已确定的 FAIL，不把违规折价成 UNKNOWN。
    outcome = "FAIL" if last is not None and last.outcome == "FAIL" else "UNKNOWN"
    return LoopResult(
        outcome=outcome, reason_code="max_rounds_exceeded",
        rounds=spec.max_rounds, final_verdict=last,
    )


def review_surface() -> tuple[str, ...]:
    """供验收核对：审核者可见参数里不得出现消息历史/思考过程。"""
    return tuple(inspect.signature(deterministic_review).parameters)
