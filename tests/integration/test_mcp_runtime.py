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


def test_messages_show_tool_metadata_in_prompt_but_not_in_tools_array():
    catalog = MCPServerCatalog((fake_spec(),))
    store = SessionStore()
    context = store.create(uuid4())
    registry = ToolRegistry(sessions=store, context=context)
    model = ScriptedModel([
        ModelResponse(finish_reason="tool_calls", tool_calls=[
            ToolCall(id="c1", name="mcp__fake__echo", arguments={"message": "hello"}),
        ]),
        ModelResponse(finish_reason="stop", content="done"),
    ])
    try:
        catalog.connect("fake")  # 模拟后台连接已完成
        register_mcp_tools(registry, catalog)
        runtime = Runtime(model, registry, system_prompt=SystemPrompt(
            "BASE_RULES", (catalog.prompt_section,)))
        result = runtime.run_session("请用 fake 回显 hello", store=store, context=context)
    finally:
        catalog.close()

    assert result.status == "completed", result.error_code
    assert len(model.calls) == 2

    # system 只公开工具名和说明；tools 数组里没有 MCP 工具，也没有 command/env。
    system = model.calls[0][0]
    assert system.role == "system" and system.content.startswith("BASE_RULES")
    assert "mcp__fake__echo" in system.content and "Echo the message back." in system.content
    assert sys.executable not in system.content and str(SERVER) not in system.content
    assert tool_names(model.tool_schemas[0]) == []

    # MCP 工具结果按原顺序回到历史。
    tool_messages = [message for message in model.calls[1] if message.role == "tool"]
    assert json.loads(tool_messages[-1].content) == {"ok": True, "result": "echo:hello"}


def test_mcp_arguments_are_rejected_locally_before_reaching_server():
    catalog = MCPServerCatalog((fake_spec(),))
    store = SessionStore()
    context = store.create(uuid4())
    registry = ToolRegistry(sessions=store, context=context)
    model = ScriptedModel([
        ModelResponse(finish_reason="tool_calls", tool_calls=[
            ToolCall(id="c1", name="connect_mcp", arguments={"name": "fake"}),
        ]),
        ModelResponse(finish_reason="tool_calls", tool_calls=[
            ToolCall(id="c2", name="mcp__fake__add", arguments={"a": 1}),
            ToolCall(id="c3", name="mcp__fake__add", arguments={"a": 1, "b": "3"}),
        ]),
        ModelResponse(finish_reason="stop", content="done"),
    ])
    try:
        register_mcp_tools(registry, catalog)
        result = Runtime(model, registry).run_session(
            "请连接 fake 并计算", store=store, context=context)
    finally:
        catalog.close()

    assert result.status == "partial" and result.error_code == "tool_request_failed"
    tool_messages = [message for message in model.calls[2] if message.role == "tool"]
    missing, wrong_type = (json.loads(message.content) for message in tool_messages[-2:])
    assert missing["error"]["code"] == "invalid_arguments"
    assert missing["error"]["issues"] == [{"field": "b", "reason": "缺少必需参数"}]
    assert wrong_type["error"]["code"] == "invalid_arguments"
    assert wrong_type["error"]["issues"] == [{"field": "b", "reason": "类型应为 integer"}]
    assert "MCP error" not in tool_messages[-2].content
    assert "MCP error" not in tool_messages[-1].content
