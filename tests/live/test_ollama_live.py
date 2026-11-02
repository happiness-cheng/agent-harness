"""显式授权后才运行的本机Ollama验收；默认跳过，不下载模型。"""

import json
import os

import pytest

from min_agent.llm.ollama import OllamaModel, OllamaSettings, require_model
from min_agent.runtime import Runtime
from min_agent.tools.calculator import CALCULATOR_TOOL
from min_agent.tools.registry import ToolDefinition, ToolRegistry


@pytest.mark.live
def test_ollama_local_plain_answer_and_calculator():
    if os.environ.get("AGENT_HARNESS_LIVE_OLLAMA") != "1":
        pytest.skip("未显式启用已授权的本机Ollama验收")
    settings = OllamaSettings(
        model=os.environ.get("AGENT_HARNESS_OLLAMA_MODEL", "qwen3.5:4b"),
        base_url=os.environ.get("AGENT_HARNESS_OLLAMA_URL", "http://127.0.0.1:11434"),
    )
    require_model(settings)
    with OllamaModel(settings) as model:
        plain = Runtime(model).run("用两句话介绍什么是 Agent。")
        assert plain.status == "completed" and plain.response.content.strip()

        executed = []

        def tracked(expression):
            result = CALCULATOR_TOOL.handler(expression=expression)
            executed.append({"expression": expression, "result": result})
            return result

        tools = ToolRegistry()
        tools.register(ToolDefinition("calculator", CALCULATOR_TOOL.description,
                                      CALCULATOR_TOOL.parameters, tracked))
        result = Runtime(model, tools=tools).run(
            "请使用 calculator 工具计算 37 乘以 19，并告诉我结果。")
        usage = model.usage
    assert result.status == "completed", result
    assert executed and executed[0]["result"] == "703"
    assert "703" in result.response.content
    assert model.request_count <= 3
    # 只输出脱敏的耗时计数，便于人工核对真实调用。
    print(json.dumps([{key: record.get(key) for key in ("request", "prompt_eval_count", "eval_count")}
                      for record in usage], ensure_ascii=False))
