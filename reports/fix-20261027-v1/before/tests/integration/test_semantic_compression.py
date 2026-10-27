import json
from uuid import uuid4

import pytest

from min_agent.artifacts import ArtifactStore
from min_agent.budget import BudgetLedger, BudgetLimits
from min_agent.compression_selector import GroundedSelector
from min_agent.compression_text import tool_summary
from min_agent.models import Message, ModelResponse, ToolCall
from min_agent.sessions import SessionStore
from tests.fakes.model import ScriptedModel


def selected():
    return ModelResponse(finish_reason="stop", content='{"selections":[{"unit_id":0,"category":"constraints"},{"unit_id":1,"category":"constraints"}]}')


def summary():
    return ModelResponse(finish_reason="stop", content=json.dumps({"items": [
        {"text": "收货7天内且未拆封可退款，需提交订单编号。", "source_ids": [0, 1], "category": "constraints"}]}, ensure_ascii=False))


def material():
    return "收货后7天内可以申请退款，" + "退款办理说明，" * 150 + "需提交订单编号。已经拆封的商品不支持退款。"


def fixture(script, calls=10):
    store = SessionStore(); context = store.create(uuid4()); artifacts = ArtifactStore(store, context)
    model = ScriptedModel(script)
    ledger = BudgetLedger(BudgetLimits(model_calls=calls, tool_calls=0))
    selector = GroundedSelector(model, ledger.start_segment(BudgetLimits(model_calls=calls, tool_calls=0)))
    return store, context, artifacts, model, ledger, selector


def test_long_selected_tool_material_becomes_short_summary_with_original_evidence():
    from min_agent.compression_evidence import read_evidence
    store, context, artifacts, model, ledger, selector = fixture([selected(), summary()])
    text = material()
    value = json.loads(tool_summary(text, "退款条件和办理步骤", artifacts, selector, max_bytes=1600))
    assert value["method"] == "task_aware_semantic_summary" and value["unverified"] is True
    assert value["excerpts"] == []
    assert value["semantic_summary"][0]["source_ids"] == [0, 1]
    assert len(json.dumps(value, ensure_ascii=False).encode()) <= 1600
    evidence = read_evidence(value["evidence"], [0, 1], artifacts)
    assert "退款办理说明" * 2 not in value["semantic_summary"][0]["text"]
    assert all(text[unit["start"]:unit["end"]] == unit["quote"] for unit in evidence)
    assert artifacts.get(value["artifact_id"])["content"] == text
    assert len(model.calls) == ledger.usage.model_calls == 2


def test_normal_short_excerpts_do_not_make_a_semantic_model_call():
    _, _, artifacts, model, _, selector = fixture([selected()])
    value = json.loads(tool_summary("收货7天内可退款。拆封后不能退款。", "退款", artifacts, selector))
    assert value["method"] == "task_aware_extractive_summary"
    assert value["excerpts"] and len(model.calls) == 1


def test_semantic_context_projection_is_frozen_and_original_stays_unchanged():
    from min_agent.context import ContextManager
    store, context, artifacts, model, _, selector = fixture([selected(), summary()])
    manager = ContextManager(store, context, artifacts, selector=selector)
    text = material()
    messages = [Message(role="user", content="退款条件"),
        Message(role="assistant", content="", tool_calls=[ToolCall(id="read", name="read_webpage", arguments={})]),
        Message(role="tool", tool_call_id="read", content=text)]
    first = manager.prepare(messages)
    assert json.loads(first[-1].content)["method"] == "task_aware_semantic_summary"
    assert manager.prepare(messages)[-1].content == first[-1].content
    assert messages[-1].content == text and len(model.calls) == 2


def test_semantic_evidence_cannot_be_read_from_another_session():
    from min_agent.compression_evidence import read_evidence
    store, _, artifacts, _, _, selector = fixture([selected(), summary()])
    value = json.loads(tool_summary(material(), "退款", artifacts, selector))
    other = ArtifactStore(store, store.create(uuid4()))
    with pytest.raises(PermissionError):
        read_evidence(value["evidence"], [0], other)


def test_history_and_recompressed_memory_preserve_original_evidence_not_rewritten_offsets():
    from min_agent.compaction import Compactor, CompactionBlocked
    from min_agent.compression_evidence import read_evidence
    store, context, artifacts, model, ledger, selector = fixture([selected(), summary(), selected(), summary()])
    compressor = Compactor(model, store, context, selector.budget)
    compressor.objective = "退款条件和办理步骤"
    initial = {"version": 1, "summary": None, "covered": 0, "covered_hashes": [], "projections": {}}
    text = material().replace("退款办理说明，" * 150, "退款办理说明，" * 350)
    first = compressor.compact_history(initial, [Message(role="user", content=text)], ["hash"], 1)
    assert first["summary"]["constraints"] == ["收货7天内且未拆封可退款，需提交订单编号。"]
    reference = first["summary"]["source_references"][0]
    assert reference["semantic"] is True
    original = read_evidence(reference["evidence"], reference["source_ids"], artifacts)
    second = compressor.compact_memory(first)
    second_ref = second["summary"]["source_references"][0]
    again = read_evidence(second_ref["evidence"], second_ref["source_ids"], artifacts)
    assert [unit["source"]["artifact_id"] for unit in again] == [unit["source"]["artifact_id"] for unit in original]
    assert all(unit["source"]["source_kind"] == "unverified_summary" for unit in again)
    assert initial["summary"] is None
    assert ledger.usage.model_calls == len(model.calls) == 4
    second_ref["evidence"]["sha256"] = "bad"
    with pytest.raises(CompactionBlocked):
        compressor.compact_memory(second)
    assert len(model.calls) == 4


def test_history_keeps_complete_excerpts_when_final_json_fits_even_if_body_exceeds_old_cap():
    from min_agent.compaction import Compactor
    store, context, _, model, ledger, selector = fixture([selected()])
    compressor = Compactor(model, store, context, selector.budget)
    initial = {"version": 1, "summary": None, "covered": 0, "covered_hashes": [], "projections": {}}
    text = material().replace("退款办理说明，" * 150, "退款办理说明，" * 200)
    value = compressor.compact_history(initial, [Message(role="user", content=text)], ["hash"], 1)["summary"]
    assert value["representation"] == "extractive"
    assert len(value["constraints"][0].encode()) > 3500
    assert len(json.dumps(value, ensure_ascii=False).encode()) <= 6000
    assert len(model.calls) == ledger.usage.model_calls == 1


def test_model_selected_noncritical_long_sentence_is_compressed_instead_of_skipped():
    _, _, artifacts, model, _, selector = fixture([selected(), summary()])
    text = "退款办理流程见帮助中心。" + "提交订单和等待审核，" * 150 + "流程结束。"
    value = json.loads(tool_summary(text, "退款", artifacts, selector))
    assert value["method"] == "task_aware_semantic_summary"
    request = json.loads(model.calls[1][-1].content)
    assert len(request["units"]) == 2
    assert all(not unit["rule_matched"] for unit in request["units"])
    assert value["semantic_summary"][0]["source_ids"] == [0, 1]


def test_failed_semantic_repairs_do_not_publish_and_same_source_opens_circuit():
    from min_agent.context import ContextLimit, ContextManager
    bad = ModelResponse(finish_reason="stop", content='{"items":[{"text":"不完整摘要","source_ids":[0]}]}')
    store, context, artifacts, model, ledger, selector = fixture([selected(), bad, bad] * 2)
    manager = ContextManager(store, context, artifacts, selector=selector)
    messages = [Message(role="user", content="退款"),
        Message(role="assistant", content="", tool_calls=[ToolCall(id="read", name="read_webpage", arguments={})]),
        Message(role="tool", tool_call_id="read", content=material())]
    for _ in range(3):
        with pytest.raises(ContextLimit): manager.prepare(messages)
    assert store.extension(context, "context", {}).get("projections", {}) == {}
    assert len(model.calls) == ledger.usage.model_calls == 6


def test_tampered_evidence_index_is_rejected():
    from min_agent.compression_evidence import read_evidence
    _, _, artifacts, _, _, selector = fixture([selected(), summary()])
    value = json.loads(tool_summary(material(), "退款", artifacts, selector))
    value["evidence"]["sha256"] = "bad"
    with pytest.raises(ValueError, match="版本"):
        read_evidence(value["evidence"], [0, 1], artifacts)


def test_metadata_budget_failure_does_not_launch_summary_model():
    _, _, artifacts, model, ledger, selector = fixture([selected()])
    with pytest.raises(ValueError, match="元数据"):
        tool_summary("七天内允许退款。拆封后不能退款。", "退款", artifacts, selector, max_bytes=300)
    assert len(model.calls) == ledger.usage.model_calls == 1


def test_application_semantic_summary_uses_parent_budget_and_freezes_projection(tmp_path):
    from min_agent.application import HarnessApplication
    from min_agent.budget_persistence import SessionBudgets
    class Model:
        def __init__(self): self.selection = 0; self.semantic = 0; self.main = 0
        def generate(self, messages, *, tools=None):
            if tools is None:
                payload = json.loads(messages[-1].content)
                if "target_bytes" in payload:
                    self.semantic += 1
                    return summary()
                self.selection += 1
                return selected()
            self.main += 1
            projected = next(json.loads(message.content) for message in messages if message.role == "tool")
            assert projected["method"] == "task_aware_semantic_summary"
            assert projected["semantic_summary"][0]["source_ids"] == [0, 1]
            return ModelResponse(finish_reason="stop", content="收货7天内且未拆封可退款，需订单编号。")
    model = Model()
    harness = HarnessApplication(tmp_path / "semantic.sqlite3", model=model)
    try:
        identity = harness.create_session()["session_id"]; context = harness.context(identity)
        task = harness.sessions.begin(context)
        harness.sessions.append(context, task.task_id, [Message(role="user", content="核对退款规则"),
            Message(role="assistant", content="", tool_calls=[ToolCall(id="read", name="read_webpage", arguments={})]),
            Message(role="tool", content=material(), tool_call_id="read"), Message(role="assistant", content="已取得资料")])
        harness.sessions.finish(context, task.task_id, status="completed")
        harness.submit(identity, "退款条件呢？"); harness.wait(identity)
        snapshot = harness.sessions.snapshot(context)
        assert snapshot.task.status == "completed"
        usage = SessionBudgets(harness.sessions).open(context, snapshot.active_budget_id).usage
        assert usage.model_calls == 3 and usage.by_purpose["compaction"].model_calls == 2
        first = harness.sessions.extension(context, "context", {})["projections"].copy()
        harness.submit(identity, "再核对一下"); harness.wait(identity)
        assert harness.sessions.snapshot(context).task.status == "completed"
        assert harness.sessions.extension(context, "context", {})["projections"] == first
        assert model.selection == model.semantic == 1 and model.main == 2
    finally:
        harness.close()
