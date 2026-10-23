"""模型相关性选择与规则提醒分开；预算不足时复核而非机械删句。"""

import json
from uuid import uuid4

import pytest

from min_agent.artifacts import ArtifactStore
from min_agent.budget import BudgetLedger, BudgetLimits
from min_agent.compression_selector import GroundedSelector
from min_agent.compression_text import tool_summary
from min_agent.models import ModelResponse
from min_agent.sessions import SessionStore
from tests.fakes.model import ScriptedModel


def response(value):
    return ModelResponse(finish_reason="stop", content=json.dumps(value, ensure_ascii=False))


def setup(script, calls=5):
    store = SessionStore()
    context = store.create(uuid4())
    artifacts = ArtifactStore(store, context)
    model = ScriptedModel(script)
    ledger = BudgetLedger(BudgetLimits(model_calls=calls, tool_calls=0))
    selector = GroundedSelector(model, ledger.start_segment(BudgetLimits(model_calls=calls, tool_calls=0)))
    return artifacts, model, ledger, selector


def test_excerpts_distinguish_model_selection_from_rule_supplement_without_extra_call():
    artifacts, model, ledger, selector = setup([
        response({"selections": [{"unit_id": 0}]})])
    text = "退款流程见帮助中心。\n禁止转载。"
    summary = json.loads(tool_summary(text, "退款申请", artifacts, selector))
    first, second = summary["excerpts"]
    assert first["model_selected"] is True and first["rule_matched"] is False
    assert second["model_selected"] is False and second["rule_matched"] is True
    assert second["relevance_review"] == "not_reviewed"
    assert len(model.calls) == ledger.usage.model_calls == 1
    assert summary["selection_review"] is None


def test_budget_pressure_reviews_rule_only_footer_and_keeps_original_and_audit():
    artifacts, model, ledger, selector = setup([
        response({"selections": [{"unit_id": 0}]}),
        response({"decisions": [{"unit_id": 1, "decision": "drop"}]})])
    text = "退款流程见帮助中心。\n禁止转载" + "版权声明" * 260 + "。"
    summary = json.loads(tool_summary(text, "退款申请", artifacts, selector))
    assert [unit["quote"] for unit in summary["excerpts"]] == ["退款流程见帮助中心。"]
    assert artifacts.get(summary["artifact_id"])["content"] == text
    audit = artifacts.get(summary["selection_review"]["artifact_id"])
    review = json.loads(audit["content"])
    assert review["decisions"] == [{"unit_id": 1, "decision": "drop"}]
    assert review["units"][1]["quote"].startswith("禁止转载")
    assert review["units"][0]["model_selected"] is True
    assert review["units"][1]["model_selected"] is False
    request = json.loads(model.calls[1][-1].content)
    assert request["units"][0]["model_selected"] is True
    assert request["units"][1]["rule_matched"] is True
    assert len(model.calls) == ledger.usage.by_purpose["compaction"].model_calls == 2


@pytest.mark.parametrize("decision", ["keep", "uncertain"])
def test_rule_only_condition_cannot_be_silently_dropped_when_review_cannot_release_it(decision):
    artifacts, model, ledger, selector = setup([
        response({"selections": [{"unit_id": 0}]}),
        response({"decisions": [{"unit_id": 1, "decision": decision}]})])
    text = "收货后可以申请退款。\n但已经拆封的商品不支持退款" + "，该限制适用" * 220 + "。"
    with pytest.raises(ValueError, match="预算"):
        tool_summary(text, "怎么申请退款", artifacts, selector)
    assert len(model.calls) == ledger.usage.model_calls == 2
    assert any(value["content"] == text for value in artifacts.sessions.extension(artifacts.context, "artifacts", {}).values())


@pytest.mark.parametrize("decisions", [[], [{"unit_id": 0, "decision": "drop"}],
    [{"unit_id": 1, "decision": "drop"}, {"unit_id": 1, "decision": "keep"}]])
def test_review_rejects_missing_forged_or_duplicate_candidate_ids(decisions):
    artifacts, model, _, selector = setup([
        response({"selections": [{"unit_id": 0}]}), response({"decisions": decisions})])
    with pytest.raises(ValueError):
        tool_summary("退款指南。\n禁止转载" + "版权说明" * 260 + "。", "退款", artifacts, selector)
    assert len(model.calls) == 2


def test_review_cannot_spend_final_call_reserve():
    artifacts, model, ledger, selector = setup([
        response({"selections": [{"unit_id": 0}]})], calls=2)
    with pytest.raises(ValueError, match="收尾"):
        tool_summary("退款指南。\n禁止转载" + "版权说明" * 260 + "。", "退款", artifacts, selector)
    assert len(model.calls) == ledger.usage.model_calls == 1


def test_review_keeps_short_refund_exception_and_omits_lower_priority_background():
    artifacts, model, _, selector = setup([
        response({"selections": [{"unit_id": 0}, {"unit_id": 1}]}),
        response({"decisions": [{"unit_id": 2, "decision": "keep"}]})])
    text = "退款流程见帮助中心。\n" + "产品背景" * 235 + "。\n但已经拆封的商品不支持退款。"
    summary = json.loads(tool_summary(text, "退款", artifacts, selector))
    condition = next(unit for unit in summary["excerpts"] if "拆封" in unit["quote"])
    assert condition["model_selected"] is False and condition["rule_matched"] is True
    assert condition["relevance_review"] == "keep"
    assert len(model.calls) == 2


def test_model_selected_constraint_is_never_a_review_deletion_candidate():
    artifacts, model, _, selector = setup([
        response({"selections": [{"unit_id": 0}]}),
        response({"decisions": [{"unit_id": 1, "decision": "drop"}]})])
    text = "已经拆封的商品不支持退款。\n禁止转载" + "版权说明" * 260 + "。"
    summary = json.loads(tool_summary(text, "退款", artifacts, selector))
    assert summary["excerpts"][0]["model_selected"] is True
    assert summary["excerpts"][0]["rule_matched"] is True
    assert json.loads(model.calls[1][-1].content)["candidate_ids"] == [1]


def test_history_compaction_can_release_unrelated_dated_footer_with_review_provenance():
    from min_agent.compaction import Compactor
    from min_agent.models import Message
    artifacts, model, ledger, selector = setup([
        response({"selections": [{"unit_id": 0}]}),
        response({"decisions": [{"unit_id": 1, "decision": "drop"}]})])
    compactor = Compactor(model, artifacts.sessions, artifacts.context, selector.budget)
    compactor.objective = "退款"
    initial = {"version": 1, "summary": None, "covered": 0, "covered_hashes": [], "projections": {}}
    text = "退款流程见帮助中心。\n2026-10-23 禁止转载" + "版权说明" * 310 + "。"
    result = compactor.compact_history(initial, [Message(role="user", content=text)], ["hash"], 1)
    summary = result["summary"]
    assert summary["facts"] == ["退款流程见帮助中心。"]
    assert summary["source_references"][0]["model_selected"] is True
    assert artifacts.get(summary["selection_review"]["artifact_id"])["kind"] == "selection_review"
    assert initial["summary"] is None
    assert ledger.usage.by_purpose["compaction"].model_calls == 2


def test_review_checks_whole_input_window_before_consuming_budget():
    artifacts, model, ledger, selector = setup([])
    selector.input_window = 6000
    units = [{"quote": "退款依据。"}, {"quote": "禁止转载" + "版权说明" * 260}]
    with pytest.raises(ValueError, match="窗口"):
        selector.review(units, "退款", {0}, {1})
    assert len(model.calls) == ledger.usage.model_calls == 0


def test_review_honors_user_stop_before_model_call():
    from min_agent.events import SessionEvents
    artifacts, model, ledger, selector = setup([])
    control = SessionEvents(artifacts.sessions, artifacts.context)
    control.stop()
    selector.control = control
    with pytest.raises(ValueError, match="停止"):
        selector.review([{"quote": "退款"}, {"quote": "禁止转载"}], "退款", {0}, {1})
    assert len(model.calls) == ledger.usage.model_calls == 0


def test_deterministic_structured_excerpt_does_not_claim_model_selection():
    from min_agent.compression_text import choose_excerpts, text_units
    units, _ = text_units("不删除原文。")
    selected = choose_excerpts(units, "原文")
    assert selected[0]["model_selected"] is False
    assert selected[0]["rule_matched"] is True
    assert selected[0]["relevance_review"] == "not_reviewed"
