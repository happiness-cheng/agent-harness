"""MA-P5-002 / MA-P5-004：S2 提议者-审核者的固定验收样本与负面对照。

全部离线，提议者是脚本 callable，0 次模型调用。
``FROZEN_*`` 是固定验收样本，**不得为让测试通过而修改**；新反例另增样本。
对应既有硬反例：偷懒式假完成（SYSVAL L03 形态）、交付与回执不一致
（EVAL-010 的「工具正确但最终回答错误仍 PASS」形态）、假成功、过早放弃。

MA-P5-004 收紧：``completed_checks`` 由判定依据降级为不可信自述，检查项改由
可信 host 的 ``host_checks`` 绑定。**冻结样本一字未改**，只为调用补齐 host 侧
输入；没有任何判据被放宽，且新增了「缺绑定默认拒绝」与「自报无效」的反例。
"""

from __future__ import annotations

from hashlib import sha256

import pytest
from pydantic import ValidationError

from min_agent.ledger.budget import BudgetLedger, BudgetLimits
from min_agent.agents.subagent import EvidenceRef, Handoff, SubagentContract
from min_agent.agents.subagent_review import (
    LoopResult, ProposerOutput, ReviewSpec, ReviewVerdict, deterministic_review,
    review_loop, review_surface,
)

RAW_RECEIPT = b"calculator(37*19) -> 703"
FROZEN_REF = sha256(RAW_RECEIPT).hexdigest()
FROZEN_RAW = {FROZEN_REF: RAW_RECEIPT}

FROZEN_SPEC = ReviewSpec(
    objective="核准 37×19 的计算结果并交付",
    required_checks=("calculator_called", "answer_delivered"),
    required_markers=("703",),
    required_evidence_kind="tool_receipt",
    min_receipts=1,
    max_rounds=3,
)

FROZEN_GOOD = ProposerOutput(
    artifact="37×19=703，已依据工具回执核准。",
    declared_complete=True,
    completed_checks=("calculator_called", "answer_delivered"),
    evidence_refs=(EvidenceRef(kind="tool_receipt", ref=FROZEN_REF),),
    receipt_refs=(FROZEN_REF,),
)

FROZEN_LAZY = ProposerOutput(
    artifact="37×19=703，已依据工具回执核准。",
    declared_complete=True,
    completed_checks=("calculator_called",),
    evidence_refs=(EvidenceRef(kind="tool_receipt", ref=FROZEN_REF),),
    receipt_refs=(FROZEN_REF,),
)

FROZEN_ABANDON = ProposerOutput(
    artifact="这事办不了。",
    declared_complete=False,
    stop_reason="blocked",
    limitations=("第一次尝试未成功",),
)

FROZEN_FALSE_SUCCESS = ProposerOutput(
    artifact="已办妥，37×19=703。",
    declared_complete=True,
    completed_checks=("calculator_called", "answer_delivered"),
    evidence_refs=(),
    receipt_refs=(),
)

FROZEN_DELIVERY_MISMATCH = ProposerOutput(
    artifact="37×19=999，已核准。",
    declared_complete=True,
    completed_checks=("calculator_called", "answer_delivered"),
    evidence_refs=(EvidenceRef(kind="tool_receipt", ref=FROZEN_REF),),
    receipt_refs=(FROZEN_REF,),
)

# 由可信 host 从工具轨迹生成，与提议者自报无关。键存在 = 该项有可信依据。
HOST_CHECKS_FULL = {
    "calculator_called": FROZEN_REF,
    "answer_delivered": FROZEN_REF,
}
HOST_CHECKS_LAZY = {"calculator_called": FROZEN_REF}


def make_contract():
    limits = BudgetLimits(model_calls=10, tool_calls=10)
    parent = BudgetLedger(limits).start_segment(limits)
    handoff = Handoff(
        objective="核准 37×19 的计算结果",
        constraints=(),
        allowed_tools=("calculator",),
        evidence_requirement="tool_receipt",
        max_model_calls=4,
        max_tool_calls=4,
        deadline_seconds=60.0,
    )
    return SubagentContract(parent, handoff)


# --- 固定验收样本：三种过早终止形态 + 正常对照 -----------------------------

def test_frozen_good_delivery_passes():
    result = deterministic_review(FROZEN_SPEC, FROZEN_GOOD, FROZEN_RAW,
                                  host_checks=HOST_CHECKS_FULL)
    assert result.outcome == "PASS"
    assert result.reason_code == "verified"
    assert result.verified_evidence == 1


def test_frozen_lazy_partial_completion_is_fail():
    result = deterministic_review(FROZEN_SPEC, FROZEN_LAZY, FROZEN_RAW,
                                  host_checks=HOST_CHECKS_LAZY)
    assert result.outcome == "FAIL"
    assert result.reason_code == "lazy_partial_completion"
    assert result.missing_checks == ("answer_delivered",)


def test_frozen_premature_abandonment_is_fail():
    result = deterministic_review(FROZEN_SPEC, FROZEN_ABANDON, FROZEN_RAW,
                                  host_checks=HOST_CHECKS_FULL)
    assert result.outcome == "FAIL"
    assert result.reason_code == "premature_abandonment"


def test_frozen_false_success_is_fail():
    result = deterministic_review(FROZEN_SPEC, FROZEN_FALSE_SUCCESS, FROZEN_RAW,
                                  host_checks=HOST_CHECKS_FULL)
    assert result.outcome == "FAIL"
    assert result.reason_code == "false_success"


def test_frozen_delivery_mismatch_is_fail():
    """EVAL-010 形态：工具回执正确，最终答案却错，不得放行。"""
    result = deterministic_review(FROZEN_SPEC, FROZEN_DELIVERY_MISMATCH, FROZEN_RAW,
                                  host_checks=HOST_CHECKS_FULL)
    assert result.outcome == "FAIL"
    assert result.reason_code == "delivery_mismatch"


# --- 证据真实性、四态与语义边界 --------------------------------------------

def test_forged_evidence_is_fail():
    forged = FROZEN_GOOD.model_copy(update={
        "evidence_refs": (EvidenceRef(kind="tool_receipt", ref="b" * 64),),
    })
    result = deterministic_review(FROZEN_SPEC, forged, FROZEN_RAW,
                                  host_checks=HOST_CHECKS_FULL)
    assert result.reason_code == "forged_evidence"


def test_raw_receipts_unavailable_is_unknown_not_false_success():
    result = deterministic_review(FROZEN_SPEC, FROZEN_GOOD, {},
                                  host_checks=HOST_CHECKS_FULL)
    assert result.outcome == "UNKNOWN"
    assert result.reason_code == "raw_receipts_unavailable"


def test_duplicate_evidence_is_fail():
    duplicated = FROZEN_GOOD.model_copy(update={
        "evidence_refs": (
            EvidenceRef(kind="tool_receipt", ref=FROZEN_REF),
            EvidenceRef(kind="tool_receipt", ref=FROZEN_REF),
        ),
    })
    result = deterministic_review(FROZEN_SPEC, duplicated, FROZEN_RAW,
                                  host_checks=HOST_CHECKS_FULL)
    assert result.reason_code == "duplicate_evidence"


def test_fail_is_not_masked_by_missing_evidence():
    """已确定的偷懒式假完成，不能被「回执不可用」的 UNKNOWN 抵消。"""
    result = deterministic_review(FROZEN_SPEC, FROZEN_LAZY, {},
                                  host_checks=HOST_CHECKS_LAZY)
    assert result.outcome == "FAIL"
    assert result.reason_code == "lazy_partial_completion"


def test_not_declared_complete_with_real_receipts_stays_unknown():
    honest_partial = ProposerOutput(
        artifact="37×19=703（部分）", declared_complete=False,
        completed_checks=("calculator_called",),
        evidence_refs=(EvidenceRef(kind="tool_receipt", ref=FROZEN_REF),),
        receipt_refs=(FROZEN_REF,), stop_reason="budget",
    )
    result = deterministic_review(FROZEN_SPEC, honest_partial, FROZEN_RAW,
                                  host_checks=HOST_CHECKS_FULL)
    assert result.outcome == "UNKNOWN"
    assert result.reason_code == "not_completed"


def test_semantic_quality_is_always_unknown():
    result = deterministic_review(FROZEN_SPEC, FROZEN_GOOD, FROZEN_RAW,
                                  host_checks=HOST_CHECKS_FULL)
    assert result.semantic_review == "UNKNOWN"


def test_review_spec_rejects_duplicate_checks():
    with pytest.raises(ValidationError):
        ReviewSpec(objective="x", required_checks=("a", "a"))
    with pytest.raises(ValidationError):
        ReviewSpec(objective="x", required_markers=("",))


def test_review_spec_rejects_empty_criteria():
    with pytest.raises(ValidationError):
        ReviewSpec(objective="x", required_checks=(), required_markers=(), min_receipts=0)


# --- 审核者看不到思考过程 ---------------------------------------------------

def test_review_surface_has_no_history_parameters():
    names = " ".join(review_surface()).lower()
    assert all(word not in names for word in ("message", "history", "reasoning", "thought"))


def test_review_surface_includes_host_checks():
    """MA-P5-004：绑定来源必须出现在审核者的可见参数里。"""
    assert "host_checks" in review_surface()


def test_proposer_output_rejects_messages_field():
    with pytest.raises(ValidationError):
        ProposerOutput(
            artifact="x", declared_complete=True,
            messages=[{"role": "user", "content": "private"}],
        )


# --- MA-P5-004：检查项绑定收紧 ----------------------------------------------

def test_missing_host_binding_is_unknown_and_never_passes():
    """缺绑定时默认拒绝：即使自报齐全，也不得放行。"""
    result = deterministic_review(FROZEN_SPEC, FROZEN_GOOD, FROZEN_RAW)
    assert result.outcome == "UNKNOWN"
    assert result.reason_code == "checks_not_host_verified"
    assert result.missing_checks == FROZEN_SPEC.required_checks


def test_loop_without_host_binding_refuses_before_any_proposer_call():
    calls: list[int] = []

    def proposer(round_no, feedback):
        calls.append(round_no)
        return FROZEN_GOOD

    result = review_loop(FROZEN_SPEC, proposer, FROZEN_RAW)
    assert result.outcome == "UNKNOWN"
    assert result.reason_code == "checks_not_host_verified"
    assert result.rounds == 0
    assert calls == []


def test_self_reported_checks_are_ignored_in_both_directions():
    """自报少报不能制造 FAIL，自报多报也不能制造 PASS；只看 host 绑定。"""
    under_reported = deterministic_review(FROZEN_SPEC, FROZEN_LAZY, FROZEN_RAW,
                                          host_checks=HOST_CHECKS_FULL)
    assert under_reported.outcome == "PASS"

    over_reported = FROZEN_GOOD.model_copy(update={"completed_checks": ()})
    assert deterministic_review(FROZEN_SPEC, over_reported, FROZEN_RAW,
                                host_checks=HOST_CHECKS_FULL).outcome == "PASS"

    assert deterministic_review(FROZEN_SPEC, FROZEN_GOOD, FROZEN_RAW,
                                host_checks=HOST_CHECKS_LAZY).reason_code == \
        "lazy_partial_completion"


def test_forged_host_check_ref_is_fail():
    bad = {"calculator_called": "a" * 64, "answer_delivered": FROZEN_REF}
    result = deterministic_review(FROZEN_SPEC, FROZEN_GOOD, FROZEN_RAW,
                                  host_checks=bad)
    assert result.outcome == "FAIL"
    assert result.reason_code == "forged_check_evidence"


def test_host_check_ref_unverifiable_without_receipts_is_unknown_not_forged():
    """原始回执整体不可用时无法核验指纹，保留 UNKNOWN，不指控 host 伪造。"""
    result = deterministic_review(FROZEN_SPEC, FROZEN_GOOD, {},
                                  host_checks=HOST_CHECKS_FULL)
    assert result.outcome == "UNKNOWN"
    assert result.reason_code == "raw_receipts_unavailable"


def test_host_checks_must_be_mapping():
    with pytest.raises(TypeError):
        deterministic_review(FROZEN_SPEC, FROZEN_GOOD, FROZEN_RAW,
                             host_checks="not-a-mapping")


def test_round_scoped_host_checks_are_recomputed_each_round():
    """真实 host 每轮从工具轨迹重新生成绑定；按轮 provider 必须被实际调用。"""
    seen_rounds: list[int] = []

    def host(round_no):
        seen_rounds.append(round_no)
        return HOST_CHECKS_LAZY if round_no == 1 else HOST_CHECKS_FULL

    def proposer(round_no, feedback):
        return FROZEN_GOOD

    result = review_loop(FROZEN_SPEC, proposer, FROZEN_RAW, host_checks=host)
    assert result.outcome == "PASS"
    assert result.rounds == 2
    assert seen_rounds == [1, 2]


# --- 有界验证循环 ----------------------------------------------------------

def test_correction_loop_passes_on_second_round_with_structured_feedback():
    seen: list[ReviewVerdict | None] = []

    def proposer(round_no, feedback):
        seen.append(feedback)
        return FROZEN_LAZY if round_no == 1 else FROZEN_GOOD

    def host(round_no):
        return HOST_CHECKS_LAZY if round_no == 1 else HOST_CHECKS_FULL

    result = review_loop(FROZEN_SPEC, proposer, FROZEN_RAW, host_checks=host)
    assert result.outcome == "PASS"
    assert result.rounds == 2
    assert seen[0] is None
    assert isinstance(seen[1], ReviewVerdict)
    assert seen[1].reason_code == "lazy_partial_completion"
    assert not hasattr(seen[1], "artifact")


def test_max_rounds_preserves_determined_fail():
    result = review_loop(FROZEN_SPEC, lambda r, f: FROZEN_LAZY, FROZEN_RAW,
                         host_checks=HOST_CHECKS_LAZY)
    assert result.outcome == "FAIL"
    assert result.reason_code == "max_rounds_exceeded"
    assert result.rounds == FROZEN_SPEC.max_rounds


def test_proposer_error_is_unknown():
    def broken(round_no, feedback):
        raise RuntimeError("script proposer failed")

    result = review_loop(FROZEN_SPEC, broken, FROZEN_RAW,
                         host_checks=HOST_CHECKS_FULL)
    assert result.outcome == "UNKNOWN"
    assert result.reason_code == "proposer_error"


def test_successful_loop_is_attributed_to_contract_once():
    contract = make_contract()
    result = review_loop(FROZEN_SPEC, lambda r, f: FROZEN_GOOD, FROZEN_RAW,
                         contract=contract, host_checks=HOST_CHECKS_FULL)
    assert result.contract_finish == "accepted"
    assert contract.gain == "PASS"
    assert contract.state == "completed"


def test_cancelled_contract_short_circuits_and_writes_nothing():
    contract = make_contract()
    contract.cancel()
    result = review_loop(FROZEN_SPEC, lambda r, f: FROZEN_GOOD, FROZEN_RAW,
                         contract=contract, host_checks=HOST_CHECKS_FULL)
    assert result.outcome == "UNKNOWN"
    assert result.reason_code == "contract_terminated"
    assert result.rounds == 0
    assert contract.result is None


def test_loop_result_is_frozen_and_structured():
    result = review_loop(FROZEN_SPEC, lambda r, f: FROZEN_GOOD, FROZEN_RAW,
                         host_checks=HOST_CHECKS_FULL)
    assert isinstance(result, LoopResult)
    with pytest.raises(ValidationError):
        LoopResult(outcome="MAYBE", reason_code="x", rounds=1)
