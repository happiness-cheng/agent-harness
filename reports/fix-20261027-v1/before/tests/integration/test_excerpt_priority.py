"""模型相关性优先；规则补充不能挤走正文后独占摘要。"""

from uuid import uuid4

import pytest

from min_agent.artifacts import ArtifactStore
from min_agent.budget import BudgetLedger, BudgetLimits
from min_agent.compression_selector import GroundedSelector
from min_agent.compression_text import plan_excerpts, text_units
from min_agent.context import ContextLimit, ContextManager
from min_agent.models import Message, ModelResponse, ToolCall
from min_agent.sessions import SessionStore
from tests.fakes.model import ScriptedModel


class Review:
    def __init__(self, decision):
        self.decision = decision

    def review(self, units, objective, selected, candidates):
        return [{"unit_id": index, "decision": self.decision} for index in sorted(candidates)]


@pytest.mark.parametrize("decision", ["keep", "uncertain"])
def test_protected_supplement_cannot_displace_model_selected_refund_instructions(decision):
    # 两句分别都能放下，合起来放不下；旧实现会只留下版权声明。
    units, _ = text_units("退款流程需要提交订单编号。禁止转载本页图文内容，版权归发布者所有。")
    with pytest.raises(ValueError, match="预算"):
        plan_excerpts(units, "怎么退款", max_bytes=60, selected=[0], reviewer=Review(decision))


def test_oversized_model_selection_cannot_leave_only_a_short_rule_supplement():
    units, _ = text_units("退款办理步骤" + "提交订单编号" * 30 + "。禁止转载。")
    with pytest.raises(ValueError, match="模型选中"):
        plan_excerpts(units, "怎么退款", max_bytes=60, selected=[0], reviewer=Review("uncertain"))


def test_explicitly_irrelevant_supplement_releases_space_for_model_selection():
    units, _ = text_units("退款流程需要提交订单编号。禁止转载本页图文内容，版权归发布者所有。")
    plan = plan_excerpts(units, "怎么退款", max_bytes=60, selected=[0], reviewer=Review("drop"))
    assert [unit["unit_id"] for unit in plan.excerpts] == [0]
    assert plan.excerpts[0]["model_selected"] is True
    assert plan.review == [{"unit_id": 1, "decision": "drop"}]


def test_budget_fits_both_restores_source_order_and_keeps_selection_provenance():
    units, _ = text_units("拆封后不能退款。退款流程需要提交订单编号。")
    plan = plan_excerpts(units, "怎么退款", max_bytes=200, selected=[1])
    assert [unit["unit_id"] for unit in plan.excerpts] == [0, 1]
    assert plan.excerpts[0]["model_selected"] is False
    assert plan.excerpts[0]["rule_matched"] is True
    assert plan.excerpts[1]["model_selected"] is True
    assert plan.review is None


def test_conflicting_tool_summary_pauses_without_freezing_a_supplement_only_projection():
    store = SessionStore()
    context = store.create(uuid4())
    artifacts = ArtifactStore(store, context)
    # 本例不给语义改写额外额度，验证仍会安全暂停且不发布部分投影。
    ledger = BudgetLedger(BudgetLimits(model_calls=3, tool_calls=0))
    model = ScriptedModel([
        ModelResponse(finish_reason="stop", content='{"selections":[{"unit_id":0}]}'),
        ModelResponse(finish_reason="stop", content='{"decisions":[{"unit_id":1,"decision":"uncertain"}]}'),
    ])
    selector = GroundedSelector(model, ledger.start_segment(BudgetLimits(model_calls=3, tool_calls=0)))
    manager = ContextManager(store, context, artifacts, selector=selector)
    text = "退款办理步骤" + "提交订单编号" * 100 + "。禁止转载" + "版权说明" * 150 + "。"
    messages = [Message(role="user", content="怎么退款"),
        Message(role="assistant", content="", tool_calls=[ToolCall(id="read", name="read_webpage", arguments={})]),
        Message(role="tool", content=text, tool_call_id="read")]
    with pytest.raises(ContextLimit):
        manager.prepare(messages)
    assert store.extension(context, "context", {}).get("projections", {}) == {}
    assert any(value["content"] == text for value in store.extension(context, "artifacts", {}).values())
    assert messages[-1].content == text
    assert len(model.calls) == ledger.usage.model_calls == 2
