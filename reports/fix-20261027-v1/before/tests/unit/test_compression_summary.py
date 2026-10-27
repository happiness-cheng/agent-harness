import json

import pytest

from min_agent.budget import BudgetLedger, BudgetLimits
from min_agent.models import ModelResponse
from tests.fakes.model import ScriptedModel


def result(text="收货7天内且未拆封可退款。", ids=None):
    return ModelResponse(finish_reason="stop", content=json.dumps({"items": [
        {"text": text, "source_ids": [0, 1] if ids is None else ids, "category": "constraints"}]}, ensure_ascii=False))


def fixture(script, *, calls=8, **kwargs):
    from min_agent.compression_summary import SemanticCompressor
    model = ScriptedModel(script)
    ledger = BudgetLedger(BudgetLimits(model_calls=calls, tool_calls=0))
    budget = ledger.start_segment(BudgetLimits(model_calls=calls, tool_calls=0))
    return SemanticCompressor(model, budget, **kwargs), model, ledger


def units():
    return [{"unit_id": 0, "quote": "收货后七天内可以申请退款。"},
        {"unit_id": 1, "quote": "已经拆封的商品不支持退款。"}]


def test_semantic_summary_can_merge_related_sentences_and_keeps_all_evidence_ids():
    compressor, model, ledger = fixture([result()])
    items = compressor.compress(units(), "怎么退款", max_bytes=800)
    assert items[0]["text"] == "收货7天内且未拆封可退款。"
    assert items[0]["source_ids"] == [0, 1]
    request = json.loads(model.calls[0][-1].content)
    assert [row["text"] for row in request["units"]] == [unit["quote"] for unit in units()]
    assert model.tool_schemas == [[]]
    assert ledger.usage.by_purpose["compaction"].model_calls == 1


def test_invalid_source_id_is_repaired_once_using_complete_original_material():
    compressor, model, ledger = fixture([result(ids=[999]), result()])
    items = compressor.compress(units(), "怎么退款", max_bytes=800)
    assert items[0]["source_ids"] == [0, 1]
    assert len(model.calls) == ledger.usage.model_calls == 2
    assert json.loads(model.calls[1][-1].content)["feedback"]


def test_repeated_missing_coverage_stops_after_one_repair():
    compressor, model, ledger = fixture([result(ids=[0])] * 4)
    with pytest.raises(ValueError):
        compressor.compress(units(), "怎么退款", max_bytes=800)
    assert len(model.calls) == ledger.usage.model_calls == 2


def test_size_validation_counts_the_complete_rendered_result():
    render = lambda items: json.dumps({"metadata": "x" * 240, "items": items}, ensure_ascii=False)
    compressor, model, _ = fixture([result("摘要" * 30), result("未拆封且7天内退款")])
    items = compressor.compress(units(), "退款", max_bytes=400, render=render)
    assert len(render(items).encode()) <= 400
    assert len(model.calls) == 2


def test_final_call_reserve_and_metadata_overflow_do_not_start_model():
    compressor, model, ledger = fixture([], calls=1)
    with pytest.raises(ValueError, match="预算"):
        compressor.compress(units(), "退款", max_bytes=800)
    assert model.calls == [] and ledger.usage.model_calls == 0
    compressor, model, ledger = fixture([])
    with pytest.raises(ValueError, match="元数据"):
        compressor.compress(units(), "退款", max_bytes=10, render=lambda _: "x" * 20)
    assert model.calls == [] and ledger.usage.model_calls == 0


def test_user_stop_does_not_launch_semantic_compression():
    class Stopped:
        def snapshot(self): return {"stop": True}
    compressor, model, ledger = fixture([], control=Stopped())
    with pytest.raises(ValueError, match="停止"):
        compressor.compress(units(), "退款", max_bytes=800)
    assert model.calls == [] and ledger.usage.model_calls == 0


@pytest.mark.parametrize("strict_render", [False, True])
def test_long_selected_input_is_fully_chunked_then_merged_with_original_ids(strict_render):
    from min_agent.compression_summary import SemanticCompressor
    class Model:
        def __init__(self): self.seen = []; self.calls = 0; self.merges = 0
        def generate(self, messages):
            self.calls += 1
            rows = json.loads(messages[-1].content)["units"]
            ids = sorted({identity for row in rows for identity in row["source_ids"]})
            if all(row["source_kind"] == "unverified_summary" for row in rows):
                self.merges += 1
                text = "汇总所有相关段落。"
            else:
                self.seen.extend(ids)
                text = "分块摘要" + "内容" * 65
            return result(text, ids)
    model = Model()
    ledger = BudgetLedger(BudgetLimits(model_calls=10, tool_calls=0))
    compressor = SemanticCompressor(model, ledger.start_segment(BudgetLimits(model_calls=10, tool_calls=0)), input_window=14000)
    source = [{"unit_id": index, "quote": f"段落{index}: " + "detail " * 180} for index in range(8)]
    def render(items):
        if strict_render and len(items) > 1:
            raise ValueError("中间结果项目数过多，需要汇总")
        return json.dumps({"items": items}, ensure_ascii=False)
    items = compressor.compress(source, "对比全部段落", max_bytes=700, render=render)
    assert sorted(model.seen) == list(range(8))
    assert model.merges == 1 and 2 < model.calls <= 4
    assert items[0]["source_ids"] == list(range(8))
    assert ledger.usage.by_purpose["compaction"].model_calls == model.calls


def test_chunk_budget_is_checked_before_processing_any_block():
    compressor, model, ledger = fixture([], calls=2, input_window=14000)
    source = [{"unit_id": index, "quote": f"段落{index}: " + "detail " * 180} for index in range(8)]
    with pytest.raises(ValueError, match="全部块"):
        compressor.compress(source, "对比全部段落", max_bytes=700)
    assert model.calls == [] and ledger.usage.model_calls == 0


def test_single_unit_too_large_does_not_get_silently_truncated():
    compressor, model, ledger = fixture([], input_window=8000)
    with pytest.raises(ValueError, match="单条原句"):
        compressor.compress([{"unit_id": 0, "quote": "完整原句" * 1000}], "核对", max_bytes=800)
    assert model.calls == [] and ledger.usage.model_calls == 0


def test_configured_call_limit_also_bounds_repair():
    compressor, model, ledger = fixture([result(ids=[999])] * 3, max_calls=1)
    with pytest.raises(ValueError, match="预算"):
        compressor.compress(units(), "退款", max_bytes=800)
    assert len(model.calls) == ledger.usage.model_calls == 1
