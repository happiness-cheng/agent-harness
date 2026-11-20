"""MA-P5-004：单 Agent / 旧 S2 / 新 S2 三组同条件对照的固定验收样本。

**这是实验对照基线，不是产品判定入口。** 三组口径使用同一批固定输入、同一套
四态输出；差异只来自「判定者能拿到什么信息」，不来自模型能力或提示词。

* A  ``self_report_judge``：单 Agent 自述口径。只能使用它自己生成的信息——
  声明、自报检查项、artifact、证据引用的存在性；拿不到可信 host 的原始回执
  内容，因此无法区分真实指纹与伪造指纹。
* B0 ``legacy_s2_judge``：MA-P5-002 旧 S2 口径的忠实重建（**重建，不是调用旧
  二进制**）。校验证据指纹真实性，但检查项仍信任提议者自报 ``completed_checks``。
* B1 ``deterministic_review``：本批收紧后的 S2。校验证据指纹 + 可信 host 的
  ``host_checks`` 绑定，完全忽略自报。

``FROZEN_CASES`` 与 ``expect_b1`` 是冻结验收样本与冻结期望，**不得为让结果
变好而修改**；``expect_b1`` 只用于断言行为未退化，不参与任何判定计算。

关键诚实边界（DECISION_LOG H5）：B1 相对 A 的信息增量来自 **harness 提供的
原始工具回执 + 确定性校验**，不是「第二个模型的意见」，因此不得表述为
「多 Agent 讨论优于单 Agent」。
"""

from __future__ import annotations

from hashlib import sha256

from pydantic import ValidationError
import pytest

from min_agent.evaluation.verifier import verdict as merge_verdict
from min_agent.agents.subagent import EvidenceRef
from min_agent.agents.subagent_review import (
    ProposerOutput, ReviewSpec, deterministic_review,
)

RAW_RECEIPT = b"calculator(37*19) -> 703"
FROZEN_REF = sha256(RAW_RECEIPT).hexdigest()
FROZEN_RAW = {FROZEN_REF: RAW_RECEIPT}

SPEC = ReviewSpec(
    objective="核准 37×19 的计算结果并交付",
    required_checks=("calculator_called", "answer_delivered"),
    required_markers=("703",),
    required_evidence_kind="tool_receipt",
    min_receipts=1,
    max_rounds=3,
)

HOST_FULL = {"calculator_called": FROZEN_REF, "answer_delivered": FROZEN_REF}
HOST_LAZY = {"calculator_called": FROZEN_REF}
HOST_FORGED = {"calculator_called": "a" * 64, "answer_delivered": FROZEN_REF}

GOOD = ProposerOutput(
    artifact="37×19=703，已依据工具回执核准。",
    declared_complete=True,
    completed_checks=("calculator_called", "answer_delivered"),
    evidence_refs=(EvidenceRef(kind="tool_receipt", ref=FROZEN_REF),),
    receipt_refs=(FROZEN_REF,),
)
FORGED = GOOD.model_copy(update={
    "evidence_refs": (EvidenceRef(kind="tool_receipt", ref="b" * 64),),
})
WRONG_KIND = GOOD.model_copy(update={
    "evidence_refs": (EvidenceRef(kind="execution_result", ref=FROZEN_REF),),
})
NO_EVIDENCE = ProposerOutput(
    artifact="已办妥，37×19=703。", declared_complete=True,
    completed_checks=("calculator_called", "answer_delivered"),
)
WRONG_ANSWER = GOOD.model_copy(update={"artifact": "37×19=999，已核准。"})
LAZY = GOOD.model_copy(update={"completed_checks": ("calculator_called",)})
ABANDON = ProposerOutput(
    artifact="这事办不了。", declared_complete=False, stop_reason="blocked",
)

# (id, 类别, 说明, 提议者交付, 原始回执, host 绑定, B1 冻结期望)
FROZEN_CASES = (
    ("good", "control", "合法交付：证据真实、host 两项齐、答案含 703",
     GOOD, FROZEN_RAW, HOST_FULL, "PASS"),
    ("forged_evidence", "negative", "伪造证据指纹，自报与交付都齐全",
     FORGED, FROZEN_RAW, HOST_FULL, "FAIL"),
    ("wrong_evidence_kind", "negative", "证据种类不符要求，指纹本身真实",
     WRONG_KIND, FROZEN_RAW, HOST_FULL, "FAIL"),
    ("false_success", "negative", "宣称完成但零证据",
     NO_EVIDENCE, FROZEN_RAW, HOST_FULL, "FAIL"),
    ("delivery_mismatch", "negative", "EVAL-010 形态：回执正确但答案 999",
     WRONG_ANSWER, FROZEN_RAW, HOST_FULL, "FAIL"),
    ("lazy_partial", "negative", "偷懒式假完成：host 只确认一项",
     LAZY, FROZEN_RAW, HOST_LAZY, "FAIL"),
    ("premature_abandon", "negative", "过早放弃：未声明完成且无回执引用",
     ABANDON, FROZEN_RAW, HOST_FULL, "FAIL"),
    ("self_report_all_but_no_host_evidence", "negative",
     "旧 S2 漏洞：自报两项齐全、证据真实，但 host 轨迹缺一项",
     GOOD, FROZEN_RAW, HOST_LAZY, "FAIL"),
    ("host_forged_ref", "negative",
     "host 绑定指纹伪造，提议者交付本身合法",
     GOOD, FROZEN_RAW, HOST_FORGED, "FAIL"),
    ("no_raw_receipts", "negative",
     "原始回执整体不可用，验证器不得伪造 PASS",
     GOOD, {}, HOST_FULL, "UNKNOWN"),
)

Verdict = str


def self_report_judge(spec: ReviewSpec, output: ProposerOutput) -> Verdict:
    """A 组：单 Agent 尽力自检，但**只能看到自己生成的信息**。"""
    if not output.declared_complete:
        return "UNKNOWN"
    if any(marker not in output.artifact for marker in spec.required_markers):
        return "FAIL"
    if any(check not in output.completed_checks for check in spec.required_checks):
        return "FAIL"
    if not output.evidence_refs:
        return "FAIL"
    return "PASS"


def legacy_s2_judge(spec: ReviewSpec, output: ProposerOutput,
                    raw_receipts: dict[str, bytes]) -> tuple[Verdict, str]:
    """B0 组：MA-P5-002 旧判定逻辑的忠实重建（对照专用，非产品代码）。

    与旧版唯一有意差别是不构造 ``ReviewVerdict`` 对象；判定顺序、reason_code
    与四态合并保持原样。**旧版源码未提交进 Git，因此这是重建而非直接调用**，
    该可比性限制已写入 VALIDATION_LOG。
    """
    real = {
        key: value for key, value in raw_receipts.items()
        if isinstance(key, str) and isinstance(value, bytes)
        and sha256(value).hexdigest() == key
    }
    findings: list[tuple[Verdict, str]] = []
    verified = [item for item in output.evidence_refs if item.ref in real]
    unverifiable = len(verified) != len(output.evidence_refs)
    duplicate = len({item.ref for item in verified}) != len(verified)
    matching = [item for item in verified if item.kind == spec.required_evidence_kind]
    missing_checks = tuple(
        name for name in spec.required_checks if name not in output.completed_checks
    )
    missing_markers = tuple(
        marker for marker in spec.required_markers if marker not in output.artifact
    )
    real_receipt_refs = [ref for ref in output.receipt_refs if ref in real]
    evidence_available = bool(raw_receipts)

    if not evidence_available and output.evidence_refs:
        findings.append(("UNKNOWN", "raw_receipts_unavailable"))
    elif unverifiable:
        findings.append(("FAIL", "forged_evidence"))
    if duplicate:
        findings.append(("FAIL", "duplicate_evidence"))

    if not output.declared_complete:
        if not evidence_available:
            findings.append(("UNKNOWN", "receipts_unavailable_for_stop"))
        elif len(real_receipt_refs) < spec.min_receipts:
            findings.append(("FAIL", "premature_abandonment"))
        else:
            findings.append(("UNKNOWN", "not_completed"))
    else:
        if missing_checks:
            findings.append(("FAIL", "lazy_partial_completion"))
        if missing_markers:
            findings.append(("FAIL", "delivery_mismatch"))
        if evidence_available:
            if not matching:
                findings.append(("FAIL", "false_success"))
            if not real_receipt_refs:
                findings.append(("UNKNOWN", "no_raw_receipts"))

    if not findings:
        findings.append(("PASS", "verified"))
    outcome = merge_verdict(state for state, _ in findings)
    reason = next((code for state, code in findings if state == "FAIL"), None)
    if reason is None:
        reason = next((code for state, code in findings if state == "UNKNOWN"), "verified")
    return outcome, reason


def run_comparison() -> dict:
    """对固定样本跑三组口径；返回可 JSON 序列化的结构化结果。"""
    rows = []
    for case_id, kind, description, output, raw, host, expect_b1 in FROZEN_CASES:
        a = self_report_judge(SPEC, output)
        b0, b0_reason = legacy_s2_judge(SPEC, output, raw)
        b1 = deterministic_review(SPEC, output, raw, host_checks=host)
        rows.append({
            "id": case_id, "kind": kind, "description": description,
            "A_single_agent": {"outcome": a, "reason_code": "self_report"},
            "B0_legacy_s2": {"outcome": b0, "reason_code": b0_reason},
            "B1_bound_s2": {"outcome": b1.outcome, "reason_code": b1.reason_code,
                            "missing_checks": list(b1.missing_checks),
                            "verified_evidence": b1.verified_evidence,
                            "semantic_review": b1.semantic_review},
            "expect_b1": expect_b1,
            "b1_matches_expectation": b1.outcome == expect_b1,
            "false_pass": {
                "A_single_agent": kind == "negative" and a == "PASS",
                "B0_legacy_s2": kind == "negative" and b0 == "PASS",
                "B1_bound_s2": kind == "negative" and b1.outcome == "PASS",
            },
        })
    counts = {
        group: sum(bool(row["false_pass"][group]) for row in rows)
        for group in ("A_single_agent", "B0_legacy_s2", "B1_bound_s2")
    }
    return {
        "case_count": len(rows),
        "negative_count": sum(row["kind"] == "negative" for row in rows),
        "control_count": sum(row["kind"] == "control" for row in rows),
        "false_pass_counts": counts,
        "b1_expectations_met": all(row["b1_matches_expectation"] for row in rows),
        "rows": rows,
        "limits": [
            "固定合成反例集，不是真实任务样本，不产生成功率或能力提升结论",
            "A/B0/B1 差异来自判定者可及信息，不是模型能力或提示词差异",
            "B0 是旧口径的重建而非调用未入库的旧二进制",
            "新信息来自可信 host 原始回执与确定性校验，不是第二个模型的意见",
            "语义质量在 B1 固定为 UNKNOWN，不在本对照判定范围内",
        ],
    }


# --- 固定验收断言 -----------------------------------------------------------

def test_case_set_is_frozen():
    assert len(FROZEN_CASES) == 10
    assert sum(1 for c in FROZEN_CASES if c[1] == "control") == 1
    assert {c[0] for c in FROZEN_CASES} == {
        "good", "forged_evidence", "wrong_evidence_kind", "false_success",
        "delivery_mismatch", "lazy_partial", "premature_abandon",
        "self_report_all_but_no_host_evidence", "host_forged_ref",
        "no_raw_receipts",
    }


def test_all_b1_expectations_hold():
    result = run_comparison()
    assert result["b1_expectations_met"] is True
    assert all(row["b1_matches_expectation"] for row in result["rows"])


def test_control_case_is_not_falsely_rejected_by_any_group():
    row = next(r for r in run_comparison()["rows"] if r["id"] == "good")
    assert row["A_single_agent"]["outcome"] == "PASS"
    assert row["B0_legacy_s2"]["outcome"] == "PASS"
    assert row["B1_bound_s2"]["outcome"] == "PASS"


def test_false_passes_strictly_decrease_across_groups():
    counts = run_comparison()["false_pass_counts"]
    assert counts["A_single_agent"] > counts["B0_legacy_s2"] > counts["B1_bound_s2"]
    assert counts["B1_bound_s2"] == 0


def test_binding_fix_blocks_exactly_the_two_cases_legacy_s2_passed():
    """B0 放行而 B1 拦住的，必须恰好是两个检查项绑定相关反例。"""
    rows = run_comparison()["rows"]
    divergent = [
        row["id"] for row in rows
        if row["B0_legacy_s2"]["outcome"] == "PASS"
        and row["B1_bound_s2"]["outcome"] != "PASS"
    ]
    assert divergent == ["self_report_all_but_no_host_evidence", "host_forged_ref"]


def test_no_raw_receipts_is_unknown_not_forged_pass():
    row = next(r for r in run_comparison()["rows"] if r["id"] == "no_raw_receipts")
    assert row["B1_bound_s2"]["outcome"] == "UNKNOWN"
    assert row["B1_bound_s2"]["reason_code"] == "raw_receipts_unavailable"
    assert row["A_single_agent"]["outcome"] == "PASS"


def test_semantic_review_stays_unknown_for_every_case():
    assert all(row["B1_bound_s2"]["semantic_review"] == "UNKNOWN"
               for row in run_comparison()["rows"])


def test_self_report_judge_rejects_wrong_answer_but_not_forged_fingerprint():
    assert self_report_judge(SPEC, WRONG_ANSWER) == "FAIL"
    assert self_report_judge(SPEC, FORGED) == "PASS"


def test_comparison_result_is_json_serializable_and_key_free():
    import json
    encoded = json.dumps(run_comparison(), ensure_ascii=False)
    assert "sk-ws-" not in encoded and "AQ.Ab" not in encoded
    assert "artifact" not in encoded and "private" not in encoded


def test_frozen_spec_rejects_empty_criteria():
    with pytest.raises(ValidationError):
        ReviewSpec(objective="x", required_checks=(), required_markers=(), min_receipts=0)
