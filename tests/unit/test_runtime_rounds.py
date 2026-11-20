"""轮数上限：即使没有预算，主循环也不能被无限工具请求拖住。"""

from dataclasses import replace

import pytest

from min_agent.contracts.models import ModelResponse, ToolCall
from min_agent.runtime import Runtime
from min_agent.tools.calculator import CALCULATOR_TOOL
from min_agent.tools.registry import ToolRegistry
from tests.fakes.model import ScriptedModel


def request(index: int) -> ModelResponse:
    return ModelResponse(
        finish_reason="tool_calls",
        tool_calls=[ToolCall(id=f"c{index}", name="calculator",
                             arguments={"expression": "1+1"})],
    )


def registry() -> ToolRegistry:
    tools = ToolRegistry()
    tools.register(replace(CALCULATOR_TOOL, handler=lambda expression: "2"))
    return tools


def test_round_limit_stops_endless_tool_requests_without_budget():
    model = ScriptedModel([request(index) for index in range(10)])
    runtime = Runtime(model, registry(), max_rounds=3)

    result = runtime.run("重复计算")

    assert result.status == "partial"
    assert result.error_code == "round_limit"
    assert result.response is None
    assert len(model.calls) == 3
    assert [event.error_code for event in runtime.trace.events
            if event.operation == "run"] == ["round_limit"]


def test_round_limit_is_validated():
    model = ScriptedModel([])
    with pytest.raises(TypeError):
        Runtime(model, max_rounds=True)
    with pytest.raises(TypeError):
        Runtime(model, max_rounds=3.0)
    with pytest.raises(ValueError):
        Runtime(model, max_rounds=0)
    with pytest.raises(ValueError):
        Runtime(model, max_rounds=-1)