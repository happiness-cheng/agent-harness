"""MCP 渐进式披露在真实 Runtime 消息里的可见证据（离线模型，不联网）。"""

import json
import sys
from pathlib import Path
from uuid import uuid4

from min_agent.contracts.models import ModelResponse, ToolCall
from min_agent.context.system_prompt import SystemPrompt
from min_agent.mcp.catalog import MCPServerCatalog
from min_agent.mcp.spec import MCPServerSpec
from min_agent.mcp.tools import register_mcp_tools
from min_agent.runtime import Runtime
from min_agent.session.store import SessionStore
from min_agent.tools.registry import ToolRegistry
from tests.fakes.model import ScriptedModel

SERVER = Path(__file__).resolve().parents[1] / "fakes" / "mcp_server.py"


def fake_spec():
    return MCPServerSpec(
        name="fake", description="Fake MCP server for tests.",
        command=sys.executable, args=["-u", str(SERVER)], timeout_seconds=10,
    )


def tool_names(schemas):
    return [schema["name"] for schema in schemas]


def test_messages_show_server_name_and_description_before_connect():
    catalog = MCPServerCatalog((fake_spec(),))
    store = SessionStore()
    context = store.create(uuid4())
    registry = ToolRegistry(sessions=store, context=context)
    model = ScriptedModel([
        ModelResponse(finish_reason="tool_calls", tool_calls=[
            ToolCall(id="c1", name="connect_mcp", arguments={"name": "fake"}),
        ]),
        ModelResponse(finish_reason="tool_calls", tool_calls=[
            ToolCall(id="c2", name="mcp__fake__echo", arguments={"message": "hello"}),
        ]),
        ModelResponse(finish_reason="stop", content="done"),
    ])
    try:
        register_mcp_tools(registry, catalog)
        runtime = Runtime(model, registry, system_prompt=SystemPrompt(
            "BASE_RULES", (catalog.prompt_section,)))
        result = runtime.run_session("请连接 fake 并回显", store=store, context=context)
    finally:
        catalog.close()

    assert result.status == "completed", result.error_code
    assert len(model.calls) == 3

    # 第一轮：system 只公开服务器名和说明；tools 里没有 MCP 工具
    system = model.calls[0][0]
    assert system.role == "system" and system.content.startswith("BASE_RULES")
    assert "fake" in system.content and "Fake MCP server for tests." in system.content
    assert "Echo the message back." not in system.content
    assert tool_names(model.tool_schemas[0]) == ["connect_mcp"]
    assert "fake" in json.dumps(model.tool_schemas[0][0]["parameters"], ensure_ascii=False)

    # 第二轮：connect 结果回填，工具已并入
    assert tool_names(model.tool_schemas[1]) == [
        "connect_mcp", "mcp__fake__echo", "mcp__fake__add"]
    tool_messages = [message for message in model.calls[1] if message.role == "tool"]
    connected = json.loads(tool_messages[0].content)
    assert connected["ok"] is True
    assert json.loads(connected["result"])["connected"] == "fake"

    # 第三轮：MCP 工具结果按原顺序回到历史
    tool_messages = [message for message in model.calls[2] if message.role == "tool"]
    assert json.loads(tool_messages[-1].content) == {"ok": True, "result": "echo:hello"}
