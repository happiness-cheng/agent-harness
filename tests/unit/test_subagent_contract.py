"""MA-P5-001：最小子 Agent 契约的固定验收样本与负面对照。

全部离线，不调用模型、不碰数据库、不读写生产工作区。
``FROZEN_*`` 是固定验收样本，**不得为让测试通过而修改**；发现新反例时另增样本。
开发用输入由 ``dev_handoff()`` 生成，与固定样本分开。
"""

from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from min_agent.ledger.budget import BudgetExceeded, BudgetLedger, BudgetLimits
from min_agent.agents.subagent import (
    TRAJECTORY_FIELDS, EvidenceRef, Handoff, SubagentContract, SubagentResult,
    SubagentViolation, evaluate_information_gain, receipt_ref,
)

FROZEN_REF = "a" * 64

FROZEN_HANDOFF = Handoff(
    objective="核准公开来源的证据",
    constraints=("只读取本轮已获取原文",),
    allowed_tools=("read_webpage",),
    evidence_requirement="tool_receipt",
    max_model_calls=4,
    max_tool_calls=4,
    deadline_seconds=60.0,
)

FROZEN_VALID_RESULT = SubagentResult(
    status="completed",
    artifact="结论：37×19=703",
    evidence_refs=(EvidenceRef(kind="tool_receipt", ref=FROZEN_REF),),
)

FROZEN_FALSE_SUCCESS = SubagentResult(
    status="completed",
    artifact="已完成全部核准",
    evidence_refs=(),
)

FROZEN_PARTIAL = SubagentResult(
    status="partial",
    limitations=("预算耗尽，未覆盖全部来源",),
)


def make_parent(model_calls: int = 20, tool_calls: int = 20):
    limits = BudgetLimits(model_calls=model_calls, tool_calls=tool_calls)
    return BudgetLedger(limits).start_segment(limits)


def dev_handoff(**overrides) -> Handoff:
    values = dict(
        objective="开发样本目标",
        constraints=(),
        allowed_tools=("read_webpage",),
        evidence_requirement="tool_receipt",
        max_model_calls=4,
        max_tool_calls=4,
        deadline_seconds=60.0,
    )
    values.update(overrides)
    return Handoff(**values)


# --- 固定验收样本 -----------------------------------------------------------

def test_frozen_valid_delivery_has_information_gain():
    assert evaluate_information_gain(FROZEN_VALID_RESULT, FROZEN_HANDOFF) == "PASS"


def test_frozen_completed_without_evidence_is_fail():
    assert evaluate_information_gain(FROZEN_FALSE_SUCCESS, FROZEN_HANDOFF) == "FAIL"


def test_frozen_partial_stays_unknown():
    assert evaluate_information_gain(FROZEN_PARTIAL, FROZEN_HANDOFF) == "UNKNOWN"


def test_false_success_is_not_accepted_and_can_be_retried():
    contract = SubagentContract(make_parent(), FROZEN_HANDOFF)
    assert contract.finish(FROZEN_FALSE_SUCCESS) == "rejected"
    assert contract.state == "running"
    assert contract.result is None
    assert contract.finish(FROZEN_VALID_RESULT) == "accepted"
    assert contract.gain == "PASS"
    assert contract.result == FROZEN_VALID_RESULT


def test_partial_result_is_accepted_but_never_promoted_to_pass():
    contract = SubagentContract(make_parent(), FROZEN_HANDOFF)
    assert contract.finish(FROZEN_PARTIAL) == "accepted"
    assert contract.state == "completed"
    assert contract.gain == "UNKNOWN"


# --- 证据与判据边界 ---------------------------------------------------------

def test_wrong_evidence_kind_does_not_satisfy_requirement():
    result = SubagentResult(
        status="completed",
        evidence_refs=(EvidenceRef(kind="verification_verdict", ref=FROZEN_REF),),
    )
    assert evaluate_information_gain(result, FROZEN_HANDOFF) == "FAIL"


def test_invalid_ref_format_rejected_by_model():
    with pytest.raises(ValidationError):
        EvidenceRef(kind="tool_receipt", ref="xyz")
    with pytest.raises(ValidationError):
        EvidenceRef(kind="tool_receipt", ref="A" * 64)


def test_receipt_ref_is_sha256_of_raw_bytes_without_storing_body():
    assert receipt_ref(b"raw receipt") == hashlib.sha256(b"raw receipt").hexdigest()
    with pytest.raises(TypeError):
        receipt_ref("not bytes")


def test_evaluate_requires_structured_inputs():
    with pytest.raises(TypeError):
        evaluate_information_gain({}, FROZEN_HANDOFF)
    with pytest.raises(TypeError):
        evaluate_information_gain(FROZEN_VALID_RESULT, {})


# --- 移交包与工具白名单 -----------------------------------------------------

def test_handoff_rejects_empty_duplicate_or_invalid_allowlist():
    with pytest.raises(ValidationError):
        dev_handoff(allowed_tools=())
    with pytest.raises(ValidationError):
        dev_handoff(allowed_tools=("read_webpage", "read_webpage"))
    with pytest.raises(ValidationError):
        dev_handoff(allowed_tools=("../etc/passwd",))


def test_tool_allowlist_allows_and_denies_before_action():
    contract = SubagentContract(make_parent(), FROZEN_HANDOFF)
    contract.check_tool("read_webpage")
    with pytest.raises(SubagentViolation):
        contract.check_tool("todo_add")
    with pytest.raises(SubagentViolation):
        contract.check_tool(None)
    assert any(event["kind"] == "tool_denied" for event in contract.events)


def test_contract_requires_trusted_budget_segment():
    with pytest.raises(TypeError):
        SubagentContract(object(), FROZEN_HANDOFF)
    with pytest.raises(TypeError):
        SubagentContract(make_parent(), object())


# --- 父子预算 ---------------------------------------------------------------

def test_child_budget_is_capped_by_parent_remaining():
    parent = make_parent(model_calls=3, tool_calls=2)
    contract = SubagentContract(parent, dev_handoff(max_model_calls=50, max_tool_calls=50))
    assert contract.budget.remaining.model_calls == 3
    assert contract.budget.remaining.tool_calls == 2


def test_child_consumption_counts_toward_parent_and_cannot_exceed():
    parent = make_parent(model_calls=2, tool_calls=2)
    contract = SubagentContract(parent, FROZEN_HANDOFF)
    contract.budget.consume("model_calls")
    contract.budget.consume("model_calls")
    assert parent.usage.model_calls == 2
    with pytest.raises(BudgetExceeded):
        contract.budget.consume("model_calls")
    assert contract.usage == {"model_calls": 2, "tool_calls": 0}


def test_zero_parent_budget_cannot_be_bypassed_by_delegation():
    contract = SubagentContract(make_parent(model_calls=0, tool_calls=0), FROZEN_HANDOFF)
    assert contract.budget.remaining.model_calls == 0
    with pytest.raises(BudgetExceeded):
        contract.budget.consume("model_calls")


# --- 取消、迟到结果与深度 ---------------------------------------------------

def test_cancel_cascades_to_children_without_orphans():
    contract = SubagentContract(make_parent(), FROZEN_HANDOFF)
    child = contract.spawn_child(FROZEN_HANDOFF)
    assert contract.cancel() is True
    assert child.state == "cancelled"
    assert contract.cancel() is False
    with pytest.raises(SubagentViolation):
        child.check_tool("read_webpage")


def test_late_result_is_not_written():
    contract = SubagentContract(make_parent(), FROZEN_HANDOFF)
    contract.cancel()
    assert contract.finish(FROZEN_VALID_RESULT) == "late"
    assert contract.result is None
    assert any(event.get("late") for event in contract.events)


def test_double_finish_is_late():
    contract = SubagentContract(make_parent(), FROZEN_HANDOFF)
    assert contract.finish(FROZEN_VALID_RESULT) == "accepted"
    assert contract.finish(FROZEN_VALID_RESULT) == "late"
    assert contract.events[-1]["kind"] == "late_result"


def test_deadline_expires_cooperatively_and_result_is_late():
    now = [0.0]
    contract = SubagentContract(
        make_parent(), dev_handoff(deadline_seconds=10.0), clock=lambda: now[0],
    )
    now[0] = 11.0
    with pytest.raises(SubagentViolation):
        contract.check_tool("read_webpage")
    assert contract.state == "expired"
    assert contract.finish(FROZEN_VALID_RESULT) == "late"


def test_delegation_depth_is_capped_at_two_levels():
    contract = SubagentContract(make_parent(), FROZEN_HANDOFF)
    child = contract.spawn_child(FROZEN_HANDOFF)
    with pytest.raises(SubagentViolation):
        child.spawn_child(FROZEN_HANDOFF)
    assert any(event["kind"] == "child_denied" for event in child.events)


# --- 轨迹脱敏 ---------------------------------------------------------------

def test_trajectory_is_written_in_real_time(tmp_path):
    path = tmp_path / "trajectory.jsonl"
    contract = SubagentContract(make_parent(), FROZEN_HANDOFF, trajectory_path=path)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1
    contract.check_tool("read_webpage")
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2
    contract.finish(FROZEN_VALID_RESULT)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 3
    assert all(set(json.loads(line)) <= set(TRAJECTORY_FIELDS) for line in lines)


def test_trajectory_never_contains_body_or_credentials(tmp_path):
    marker = "PRIVATE_BODY_MARKER_9f3c"
    path = tmp_path / "trajectory.jsonl"
    contract = SubagentContract(
        make_parent(),
        dev_handoff(objective="核对 " + marker),
        trajectory_path=path,
    )
    contract.check_tool("read_webpage")
    contract.finish(SubagentResult(
        status="completed",
        artifact=marker,
        evidence_refs=(EvidenceRef(kind="tool_receipt", ref=FROZEN_REF),),
    ))
    text = path.read_text(encoding="utf-8")
    assert marker not in text
    assert "GEMINI_API_KEY" not in text
    assert "Bearer" not in text
    for line in text.splitlines():
        assert set(json.loads(line)) <= set(TRAJECTORY_FIELDS)


def test_trajectory_refuses_existing_file_and_missing_directory(tmp_path):
    path = tmp_path / "trajectory.jsonl"
    SubagentContract(make_parent(), FROZEN_HANDOFF, trajectory_path=path)
    with pytest.raises(ValueError):
        SubagentContract(make_parent(), FROZEN_HANDOFF, trajectory_path=path)
    with pytest.raises(ValueError):
        SubagentContract(
            make_parent(), FROZEN_HANDOFF,
            trajectory_path=tmp_path / "missing" / "trajectory.jsonl",
        )
