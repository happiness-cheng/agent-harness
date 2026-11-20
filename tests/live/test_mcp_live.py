"""真实 MCP 服务器接线测试（live；需显式配置与网络/本机包缓存）。

运行示例：
  $env:MCP_LIVE_CONFIG = "reports/mcp-20261117/everything.json"
  $env:MCP_LIVE_TOOL = "echo"
  $env:MCP_LIVE_ARGS = '{"message":"mcp-live"}'
  .venv\\Scripts\\python.exe -X utf8 -m pytest tests/live/test_mcp_live.py -m live -q
"""

import json
import os
import time
from pathlib import Path

import pytest

from min_agent.application import HarnessApplication
from min_agent.contracts.models import ModelResponse, ToolCall
from min_agent.mcp.spec import load_mcp_config
from tests.fakes.model import ScriptedModel

pytestmark = pytest.mark.live


def live_setup():
    config = os.environ.get("MCP_LIVE_CONFIG")
    if not config:
        pytest.skip("未设置 MCP_LIVE_CONFIG，跳过真实 MCP")
    specs = load_mcp_config(Path(config))
    if not specs:
        pytest.skip("MCP 配置没有服务器")
    tool = os.environ.get("MCP_LIVE_TOOL", "echo")
    arguments = json.loads(os.environ.get("MCP_LIVE_ARGS", '{"message": "mcp-live"}'))
    expected = os.environ.get("MCP_LIVE_EXPECT", "mcp-live")
    return specs, tool, arguments, expected


def wait_connected(app, identity, name, timeout=180.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = app.snapshot(identity)["mcp"].get(name, {})
        if status.get("state") == "connected":
            return
        if status.get("state") == "failed":
            raise AssertionError(f"后台连接失败：{status}")
        time.sleep(0.2)
    raise AssertionError(f"MCP 服务器 {name} 未在 {timeout}s 内后台连接成功")


def test_real_server_background_connect_and_prompt_only_call(tmp_path):
    specs, tool, arguments, expected = live_setup()
    server = specs[0].name
    prefixed = f"mcp__{server}__{tool}"
    model = ScriptedModel([
        ModelResponse(finish_reason="tool_calls", tool_calls=[
            ToolCall(id="live-call", name=prefixed, arguments=arguments),
        ]),
        ModelResponse(finish_reason="stop", content="live 完成"),
    ])
    app = HarnessApplication(tmp_path / "mcp-live.sqlite3", model=model, mcp_servers=specs)
    try:
        identity = app.create_session()["session_id"]
        wait_connected(app, identity, server)
        app.submit(identity, "调用真实 MCP 工具")
        app.wait(identity, timeout=180)
        saved = app.snapshot(identity)
    finally:
        app.close()

    assert saved["task"]["status"] == "completed", saved["task"].get("error_code")
    # 工具从不进 tools 数组；名字与说明在 SystemPrompt 段。
    for schemas in model.tool_schemas:
        assert not any(schema["name"].startswith("mcp__") for schema in schemas)
    assert prefixed in model.calls[0][0].content
    tool_messages = [message for message in saved["messages"] if message["role"] == "tool"]
    assert any(expected in message["content"] for message in tool_messages), tool_messages[-3:]


def test_real_server_confirm_policy_queues_then_executes_after_approval(tmp_path):
    specs, tool, arguments, expected = live_setup()
    spec = specs[0].model_copy(update={"policy": "confirm"})
    server = spec.name
    prefixed = f"mcp__{server}__{tool}"
    model = ScriptedModel([
        ModelResponse(finish_reason="tool_calls", tool_calls=[
            ToolCall(id="live-1", name=prefixed, arguments=arguments),
        ]),
        ModelResponse(finish_reason="tool_calls", tool_calls=[
            ToolCall(id="live-2", name=prefixed, arguments=arguments),
        ]),
        ModelResponse(finish_reason="stop", content="live 完成"),
    ])
    app = HarnessApplication(tmp_path / "mcp-live-confirm.sqlite3", model=model,
                             mcp_servers=(spec,))
    try:
        identity = app.create_session()["session_id"]
        wait_connected(app, identity, server)
        app.submit(identity, "调用需确认的真实 MCP 工具")
        app.wait(identity, timeout=180)
        suspended = app.snapshot(identity)
        assert suspended["task"]["status"] == "waiting_user"
        assert not any(expected in message["content"]
                       for message in suspended["messages"] if message["role"] == "tool")
        pending = suspended["mcp_confirmations"]
        assert len(pending) == 1 and pending[0]["prefixed"] == prefixed

        app.mcp_confirm(identity, pending[0]["pending_id"], "approve")
        app.wait(identity, timeout=180)
        saved = app.snapshot(identity)
    finally:
        app.close()

    assert saved["task"]["status"] == "completed", saved["task"].get("error_code")
    tool_messages = [message for message in saved["messages"] if message["role"] == "tool"]
    assert any(expected in message["content"] for message in tool_messages), tool_messages[-3:]
    assert saved["mcp_confirmations"] == []
