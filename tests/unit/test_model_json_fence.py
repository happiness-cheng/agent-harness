"""真实模型 JSON 围栏容错：只去壳，不放松字段校验。"""

import json

import pytest

from min_agent.compression.model_json import strip_code_fence
from min_agent.compression.selector import GroundedSelector
from min_agent.contracts.models import ModelResponse
from min_agent.ledger.budget import BudgetLedger, BudgetLimits


def test_strip_code_fence_accepts_common_wrappers():
    assert strip_code_fence('{"a": 1}') == '{"a": 1}'
    assert strip_code_fence('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert strip_code_fence('```\n{"a": 1}\n```') == '{"a": 1}'
    assert strip_code_fence('  ```JSON\n{"a": 1}\n```  ') == '{"a": 1}'
    with pytest.raises(TypeError):
        strip_code_fence(None)


def test_selector_accepts_fenced_json_from_real_model():
    class Fenced:
        def generate(self, messages):
            rows = json.loads(messages[-1].content)["units"]
            body = json.dumps({"selections": [
                {"unit_id": row["unit_id"], "category": "facts"} for row in rows]})
            return ModelResponse(finish_reason="stop",
                                 content="```json\n" + body + "\n```")

    ledger = BudgetLedger(BudgetLimits(model_calls=20, tool_calls=0))
    budget = ledger.start_segment(BudgetLimits(model_calls=20, tool_calls=0))
    chosen = GroundedSelector(Fenced(), budget, input_window=8000).select(
        [{"quote": "第一段完整原句。"}], "核对背景")
    assert chosen == [(0, "facts")]


def test_selector_still_rejects_invalid_json():
    class Broken:
        def generate(self, messages):
            return ModelResponse(finish_reason="stop", content="```json\nnot json\n```")

    ledger = BudgetLedger(BudgetLimits(model_calls=20, tool_calls=0))
    budget = ledger.start_segment(BudgetLimits(model_calls=20, tool_calls=0))
    with pytest.raises(ValueError):
        GroundedSelector(Broken(), budget, input_window=8000).select(
            [{"quote": "第一段完整原句。"}], "核对背景")