"""真实 MCP 服务器接线测试（live；需显式配置与网络/本机包缓存）。

运行示例：
  $env:MCP_LIVE_CONFIG = "reports/mcp-20261117/everything.json"
  $env:MCP_LIVE_TOOL = "echo"
  $env:MCP_LIVE_ARGS = '{"message":"mcp-live"}'
  .venv\\Scripts\\python.exe -X utf8 -m pytest tests/live/test_mcp_live.py -m live -q
"""

import json
import os
from pathlib import Path

import pytest

from min_agent.application import HarnessApplication
from min_agent.contracts.models import ModelResponse, ToolCall
from min_agent.mcp.spec import load_mcp_config
from tests.fakes.model import ScriptedModel

pytestmark = pytest.mark.live


def test_real_mcp_server_is_exposed_only_after_connect(tmp_path):
    config = os.environ.get("MCP_LIVE_CONFIG")
    if not config:
        pytest.skip("未设置 MCP_LIVE_CONFIG，跳过真实 MCP")
    specs = load_mcp_config(Path(config))
    if not specs:
        pytest.skip("MCP 配置没有服务器")
    server = specs[0].name
    tool = os.environ.get("MCP_LIVE_TOOL", "echo")
    arguments = json.loads(os.environ.get("MCP_LIVE_ARGS", '{"message": "mcp-live"}'))
    expected = os.environ.get("MCP_LIVE_EXPECT", "mcp-live")
    prefixed = f"mcp__{server}__{tool}"

    model = ScriptedModel([
        ModelResponse(finish_reason="tool_calls", tool_calls=[
            ToolCall(id="live-connect", name="connect_mcp", arguments={"name": server}),
        ]),
        ModelResponse(finish_reason="tool_calls", tool_calls=[
            ToolCall(id="live-call", name=prefixed, arguments=arguments),
        ]),
        ModelResponse(finish_reason="stop", content="live 完成"),
    ])
    app = HarnessApplication(tmp_path / "mcp-live.sqlite3", model=model, mcp_servers=specs)
    try:
        identity = app.create_session()["session_id"]
        app.submit(identity, "连接真实 MCP 并调用工具")
        app.wait(identity, timeout=180)
        saved = app.snapshot(identity)
    finally:
        app.close()

    assert saved["task"]["status"] == "completed", saved["task"].get("error_code")
    first_names = [schema["name"] for schema in model.tool_schemas[0]]
    assert "connect_mcp" in first_names
    assert not any(name.startswith(f"mcp__{server}__") for name in first_names)
    assert server in model.calls[0][0].content

    second_names = [schema["name"] for schema in model.tool_schemas[1]]
    assert prefixed in second_names
    tool_messages = [message for message in saved["messages"] if message["role"] == "tool"]
    assert any(expected in message["content"] for message in tool_messages), tool_messages[-3:]
