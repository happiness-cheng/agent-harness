import json
from uuid import uuid4

import pytest

from min_agent.artifacts import ArtifactStore
from min_agent.context import ContextManager, ContextLimit
from min_agent.models import Message, ModelResponse, ToolCall
from min_agent.sessions import SessionStore


def tool_history(text):
    return [Message(role="user", content="核对退款条件与禁止事项"),
        Message(role="assistant", content="", tool_calls=[ToolCall(id="read", name="read_webpage", arguments={})]),
        Message(role="tool", tool_call_id="read", content=text)]


def test_tool_summary_reads_tail_conditions_and_has_verifiable_quotes_not_head_preview():
    store = SessionStore(); context = store.create(uuid4()); artifacts = ArtifactStore(store, context)
    text = "网站导航\n" * 500 + "普通背景介绍。\n退款仅在购买后7天内且未使用时允许。\n不得删除原始材料。"
    manager = ContextManager(store, context, artifacts, window=20000)
    messages = tool_history(text)
    visible = manager.prepare(messages)
    summary = json.loads(visible[-1].content)
    assert "preview" not in summary
    assert any("7天内且未使用" in item["quote"] for item in summary["excerpts"])
    assert any("不得删除" in item["quote"] for item in summary["excerpts"])
    assert summary["dropped_noise"] >= 500
    assert artifacts.get(summary["artifact_id"])["content"] == text
    for item in summary["excerpts"]:
        assert text[item["start"]:item["end"]] == item["quote"]
    assert manager.prepare(messages)[-1].content == visible[-1].content


def test_structured_layer_compacts_completed_rounds_before_any_llm():
    store = SessionStore(); context = store.create(uuid4())
    task = store.begin(context)
    store.append(context, task.task_id, [Message(role="user", content="背景材料。\n" * 1200 + "不要删除原文。"),
        Message(role="assistant", content="已了解。\n" * 1000)])
    store.finish(context, task.task_id, status="completed")
    task = store.begin(context)
    store.append(context, task.task_id, [Message(role="user", content="上一轮"), Message(role="assistant", content="上一轮回答")])
    store.finish(context, task.task_id, status="completed")
    messages = store.snapshot(context).messages + [Message(role="user", content="当前任务")]
    manager = ContextManager(store, context, ArtifactStore(store, context), window=10000, output_reserve=1000)
    visible = manager.prepare(messages)
    memory = json.loads(visible[0].content)["untrusted_session_memory"]
    assert memory["kind"] == "structured"
    assert len(memory["episodes"]) == 1
    assert "不要删除原文" in json.dumps(memory, ensure_ascii=False)
    assert [m.content for m in visible[-3:]] == ["上一轮", "上一轮回答", "当前任务"]
    assert store.snapshot(context).messages == messages[:-1]


def test_fabricated_or_rewritten_llm_summary_cannot_replace_original():
    from min_agent.budget import BudgetLedger, BudgetLimits
    from min_agent.compaction import Compactor, CompactionBlocked
    from tests.fakes.model import ScriptedModel
    store = SessionStore(); context = store.create(uuid4())
    model = ScriptedModel([ModelResponse(finish_reason="stop", content=json.dumps({
        "facts": ["删除原文"], "constraints": [], "corrections": [], "open_questions": [],
        "source_references": ["message-does-not-exist"]}))])
    budget = BudgetLedger(BudgetLimits(model_calls=4, tool_calls=0)).start_segment(BudgetLimits(model_calls=4, tool_calls=0))
    compactor = Compactor(model, store, context, budget)
    state = {"version": 1, "projections": {}, "summary": None, "covered": 0, "covered_hashes": []}
    with pytest.raises(CompactionBlocked):
        compactor.compact_history(state, [Message(role="user", content="不要删除原文。")], ["hash"], 1)
    assert state["summary"] is None


def test_selection_covers_every_chunk_and_cannot_forge_source_ids():
    from min_agent.budget import BudgetLedger, BudgetLimits
    from min_agent.compression_selector import GroundedSelector
    class Model:
        def __init__(self): self.seen = []
        def generate(self, messages):
            rows = json.loads(messages[-1].content)["units"]
            self.seen.extend(row["text"] for row in rows)
            return ModelResponse(finish_reason="stop", content=json.dumps({"selections": [
                {"unit_id": row["unit_id"], "category": "facts"} for row in rows]}))
    model = Model()
    ledger = BudgetLedger(BudgetLimits(model_calls=20, tool_calls=0))
    budget = ledger.start_segment(BudgetLimits(model_calls=20, tool_calls=0))
    units = [{"quote": f"第{index}段" + "background " * 30} for index in range(12)]
    chosen = GroundedSelector(model, budget, input_window=8000).select(units, "背景对比")
    assert model.seen == [unit["quote"] for unit in units]
    assert len(chosen) == 12 and ledger.usage.model_calls > 1
    from tests.fakes.model import ScriptedModel
    forged = ScriptedModel([ModelResponse(finish_reason="stop", content='{"selections":[{"unit_id":999}]}')])
    with pytest.raises(ValueError):
        GroundedSelector(forged, budget).select(units[:1], "核对")


def test_llm_selected_summary_preserves_whole_negative_sentence_and_real_source():
    from min_agent.artifacts import ArtifactStore
    from min_agent.budget import BudgetLedger, BudgetLimits
    from min_agent.compaction import Compactor
    from tests.fakes.model import ScriptedModel
    store = SessionStore(); context = store.create(uuid4())
    model = ScriptedModel([ModelResponse(finish_reason="stop", content='{"selections":[{"unit_id":0,"category":"constraints"}]}')])
    budget = BudgetLedger(BudgetLimits(model_calls=3, tool_calls=0)).start_segment(BudgetLimits(model_calls=3, tool_calls=0))
    compactor = Compactor(model, store, context, budget)
    state = {"version": 1, "projections": {}, "summary": None, "covered": 0, "covered_hashes": []}
    result = compactor.compact_history(state, [Message(role="user", content="不要删除原文。")], ["hash"], 1)
    assert result["summary"]["constraints"] == ["不要删除原文。"]
    citation = result["summary"]["source_references"][0]
    original = ArtifactStore(store, context).get(citation["artifact_id"])
    assert original["content"][citation["start"]:citation["end"]] == "不要删除原文。"


def test_protected_condition_over_budget_fails_without_publishing_head_text():
    store = SessionStore(); context = store.create(uuid4())
    manager = ContextManager(store, context, ArtifactStore(store, context))
    text = "禁止操作" + "无法可靠分句" * 1000
    with pytest.raises(ContextLimit): manager.prepare(tool_history(text))
    assert store.extension(context, "context", {}).get("projections", {}) == {}


def test_tool_summary_failures_open_circuit_and_keep_original_once():
    from min_agent.budget import BudgetLedger, BudgetLimits
    from min_agent.compression_selector import GroundedSelector
    from tests.fakes.model import ScriptedModel
    store = SessionStore(); context = store.create(uuid4()); artifacts = ArtifactStore(store, context)
    model = ScriptedModel([ModelResponse(finish_reason="stop", content='{"selections":[{"unit_id":999}]}')] * 3)
    ledger = BudgetLedger(BudgetLimits(model_calls=5, tool_calls=0))
    selector = GroundedSelector(model, ledger.start_segment(BudgetLimits(model_calls=5, tool_calls=0)))
    manager = ContextManager(store, context, artifacts, selector=selector)
    messages = tool_history("网站导航\n" * 500 + "退款仅在7天内允许。")
    for _ in range(3):
        with pytest.raises(ContextLimit): manager.prepare(messages)
    assert len(model.calls) == 2 and ledger.usage.model_calls == 2
    saved = store.extension(context, "artifacts", {})
    assert len(saved) == 1 and next(iter(saved.values()))["content"] == messages[-1].content


def test_selector_does_not_use_closing_reserve_or_make_half_coverage_calls():
    from min_agent.budget import BudgetLedger, BudgetLimits
    from min_agent.compression_selector import GroundedSelector
    from tests.fakes.model import ScriptedModel
    model = ScriptedModel([])
    ledger = BudgetLedger(BudgetLimits(model_calls=1, tool_calls=0))
    selector = GroundedSelector(model, ledger.start_segment(BudgetLimits(model_calls=1, tool_calls=0)))
    with pytest.raises(ValueError, match="收尾"):
        selector.select([{"quote": "完整原句。"}], "任务")
    assert model.calls == [] and ledger.usage.model_calls == 0


def test_structured_view_reads_fresh_state_and_keeps_paired_tool_evidence():
    from min_agent.memory_state import SessionStateTable
    from min_agent.tools.todo import TodoService
    store = SessionStore(); context = store.create(uuid4()); todos = TodoService(store)
    item = todos.add(context, "当前待办")
    task = store.begin(context)
    first = tool_history("退款仅在7天内允许。")
    first[0] = Message(role="user", content="背景材料。\n" * 1200 + "不要删除原文。")
    store.append(context, task.task_id, first + [Message(role="assistant", content="已核对")])
    store.finish(context, task.task_id, status="completed")
    messages = store.snapshot(context).messages + [Message(role="user", content="上一轮"),
        Message(role="assistant", content="上一轮回答"), Message(role="user", content="当前任务")]
    manager = ContextManager(store, context, ArtifactStore(store, context), window=22000,
        output_reserve=1000, state_reader=SessionStateTable(store, context, todos).read)
    # 迫使实际走结构化层，且不要用旧归档快照充当当前状态。
    first_visible = manager.prepare(messages)
    memory = json.loads(first_visible[0].content)["untrusted_session_memory"]
    events = memory["episodes"][0]["events"]
    request = next(event for event in events if event["kind"] == "tool_requested")
    result = next(event for event in events if event["kind"] == "tool_result")
    assert request["call_id"] == result["call_id"] == "read"
    assert json.loads(first_visible[-1].content)["current_runtime_state"]["todos"]
    todos.delete(context, item.id)
    next_visible = manager.prepare(messages)
    assert next_visible[0].content == first_visible[0].content
    assert json.loads(next_visible[-1].content)["current_runtime_state"]["todos"] == []


def test_application_uses_isolated_tool_selector_and_parent_ledger(tmp_path):
    from min_agent.application import HarnessApplication
    class Model:
        def __init__(self): self.selected = 0; self.main = 0
        def generate(self, messages, *, tools=None):
            if tools is None:
                rows = json.loads(messages[-1].content)["units"]
                self.selected += 1
                return ModelResponse(finish_reason="stop", content=json.dumps({"selections": [
                    {"unit_id": row["unit_id"], "category": "constraints"}
                    for row in rows if "7天" in row["text"]]}))
            self.main += 1
            projected = next(json.loads(message.content) for message in messages if message.role == "tool")
            assert projected["tool_envelope"]["ok"] is True
            assert any("7天内且未使用" in row["quote"] for row in projected["excerpts"])
            return ModelResponse(finish_reason="stop", content="退款需在7天内且未使用。")
    model = Model(); harness = HarnessApplication(tmp_path / "compress.sqlite3", model=model)
    try:
        identity = harness.create_session()["session_id"]; context = harness.context(identity)
        task = harness.sessions.begin(context)
        text = "网站导航\n" * 500 + "退款仅在购买后7天内且未使用时允许。"
        harness.sessions.append(context, task.task_id, tool_history(json.dumps({"ok": True, "result": text}, ensure_ascii=False))
            + [Message(role="assistant", content="已核对")])
        harness.sessions.finish(context, task.task_id, status="completed")
        harness.submit(identity, "刚才的退款条件呢？"); harness.wait(identity)
        snapshot = harness.sessions.snapshot(context)
        assert snapshot.messages[-1].content == "退款需在7天内且未使用。"
        assert model.selected == model.main == 1
        from min_agent.budget_persistence import SessionBudgets
        usage = SessionBudgets(harness.sessions).open(context, snapshot.active_budget_id).usage
        assert usage.model_calls == 2
        assert usage.by_purpose["compaction"].model_calls == 1
    finally:
        harness.close()


def test_json_tool_error_status_and_decoded_quotes_keep_separate_real_sources():
    store = SessionStore(); context = store.create(uuid4()); artifacts = ArtifactStore(store, context)
    body = "网站导航\n" * 500 + "不要重复执行本次操作。"
    raw = json.dumps({"ok": False, "result": body,
        "error": {"code": "write_unknown", "execution_state": "unknown", "recovery": "ask_user"}}, ensure_ascii=False)
    projected = ContextManager(store, context, artifacts).prepare(tool_history(raw))
    summary = json.loads(projected[-1].content)
    assert summary["tool_envelope"] == {"ok": False, "error": {
        "code": "write_unknown", "execution_state": "unknown", "recovery": "ask_user"}}
    assert artifacts.get(summary["raw_artifact_id"])["content"] == raw
    assert artifacts.get(summary["artifact_id"])["content"] == body
    for excerpt in summary["excerpts"]:
        assert body[excerpt["start"]:excerpt["end"]] == excerpt["quote"]


def test_recompressing_summary_keeps_reference_to_original_not_just_previous_summary():
    from min_agent.budget import BudgetLedger, BudgetLimits
    from min_agent.compaction import Compactor
    from tests.fakes.model import ScriptedModel
    store = SessionStore(); context = store.create(uuid4())
    response = ModelResponse(finish_reason="stop", content='{"selections":[{"unit_id":0,"category":"constraints"}]}')
    model = ScriptedModel([ModelResponse(finish_reason="stop",
        content='{"selections":[{"unit_id":1,"category":"constraints"}]}'), response])
    budget = BudgetLedger(BudgetLimits(model_calls=4, tool_calls=0)).start_segment(BudgetLimits(model_calls=4, tool_calls=0))
    compactor = Compactor(model, store, context, budget)
    initial = {"version": 1, "summary": None, "covered": 0, "covered_hashes": [], "projections": {}}
    once = compactor.compact_history(initial, [Message(role="user", content="普通背景。不要删除原文。")], ["hash"], 1)
    first_source = once["summary"]["source_references"][0]
    twice = compactor.compact_memory(once)
    second_source = twice["summary"]["source_references"][0]
    assert second_source["artifact_id"] == first_source["artifact_id"]
    assert (second_source["start"], second_source["end"]) == (first_source["start"], first_source["end"])
    assert second_source["source_kind"] == "unverified_summary"
    from min_agent.compaction import CompactionBlocked
    second_source["end"] += 1
    with pytest.raises(CompactionBlocked):
        compactor.compact_memory(twice)
    assert len(model.calls) == 2  # 坏来源在调用模型前拒绝。


def test_explicit_artifact_page_is_visible_without_being_summarized_again():
    class MustNotSelect:
        def select(self, *_): raise AssertionError("显式回查不能再次摘要")
    store = SessionStore(); context = store.create(uuid4())
    messages = tool_history("用于核对的完整原文。" * 150)
    messages[1].tool_calls[0] = ToolCall(id="read", name="read_artifact", arguments={"start": 0, "limit": 4000})
    manager = ContextManager(store, context, ArtifactStore(store, context), selector=MustNotSelect())
    assert manager.prepare(messages)[-1].content == messages[-1].content
