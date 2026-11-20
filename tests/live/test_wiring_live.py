"""WIRING-003 live smoke：本机 Ollama 上的自动记忆候选与真实多 Agent 委派。

默认跳过；只在本机 Ollama 已启动且显式设置环境变量时运行，不联网、不使用付费模型。
"""

import json
import os

import pytest

from min_agent.agents.factory import build_multi_agent_factory
from min_agent.application import HarnessApplication
from min_agent.llm.ollama import OllamaModel, OllamaSettings, require_model

pytestmark = pytest.mark.live


def live_model():
    if os.environ.get("AGENT_HARNESS_LIVE_OLLAMA") != "1":
        pytest.skip("未显式启用已授权的本机 Ollama live 验收")
    settings = OllamaSettings(
        model=os.environ.get("AGENT_HARNESS_OLLAMA_MODEL", "qwen3.5:4b"),
        base_url=os.environ.get("AGENT_HARNESS_OLLAMA_URL", "http://127.0.0.1:11434"),
        num_ctx=32768, max_output_tokens=1024, timeout_seconds=240.0,
    )
    require_model(settings)
    return OllamaModel(settings)


def test_ollama_auto_proposes_memory_candidate_after_remember_directive(tmp_path):
    if os.environ.get("AGENT_HARNESS_LIVE_MEMORY") != "1":
        pytest.skip("未显式启用本机 Ollama 记忆 live 验收")
    model = live_model()
    app = HarnessApplication(tmp_path / "memory-live.sqlite3", model=model,
                             memory_mode="active")
    try:
        session = app.create_session(mode="build")["session_id"]
        app.submit(session, "记住：我的代码示例偏好是 Python 3.13。")
        app.wait(session, timeout=300)
        saved = app.snapshot(session)
        assert saved["task"]["status"] == "completed", saved["task"].get("error_code")
        candidates = saved["long_memory"]["candidates"]
        assert len(candidates) == 1, candidates
        assert "Python 3.13" in candidates[0]["content"]
        context = app.context(session)
        pending = app.memory.list_pending(context)
        review = app.memory.review(context, pending[0].id)
        app.memory.confirm(context, pending[0].id, review_token=review["review_token"])
        assert len(app.memory.document(context).memories) == 1
    finally:
        app.close()
        model.close()


def test_ollama_real_multi_agent_delegation_stays_within_budget(tmp_path):
    if os.environ.get("AGENT_HARNESS_LIVE_MULTI_AGENT") != "1":
        pytest.skip("未显式启用本机 Ollama 多 Agent live 验收")
    model = live_model()
    app = HarnessApplication(tmp_path / "multi-live.sqlite3", model=model,
                             memory_mode="off", enable_multi_agent=True,
                             multi_agent_service_factory=build_multi_agent_factory())
    try:
        session = app.create_session()["session_id"]
        app.submit(session,
                   "必须调用 delegate_task 工具，把这件事交给子 Agent 处理；不要自己直接计算。"
                   "委派目标：拆成两到三个算术子任务，先算 23 乘以 17，再算 5 加 9，"
                   "最后把两个结果相加。所有子任务都使用 calculator 角色，"
                   "每个算术子任务都必须调用 calculator 工具。")
        app.wait(session, timeout=600)
        saved = app.snapshot(session)
        assert saved["task"]["status"] in {"completed", "partial"}
        payload = None
        for message in saved["messages"]:
            if message["role"] != "tool":
                continue
            try:
                outer = json.loads(message["content"])
                inner = json.loads(outer["result"])
            except (KeyError, TypeError, ValueError):
                continue
            if isinstance(inner, dict) and "plan" in inner:
                payload = inner
                break
        assert payload is not None, "delegate_task 没有返回工具结果"
        assert payload["plan"] is not None
        assert 1 <= len(payload["plan"]["subtasks"]) <= 3
        assert payload["results"]
        usage = saved["budget"]["usage"]
        assert usage["model_calls"] > 1 and usage["tool_calls"] >= 1
        print(json.dumps({"status": payload["status"],
                          "subtasks": [row["status"] + "/" + row["verification"]
                                       for row in payload["results"]],
                          "usage": usage}, ensure_ascii=False))
    finally:
        app.close()
        model.close()