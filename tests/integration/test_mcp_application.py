"""应用层接线：MCP 工具只在 connect 之后进入真实任务的消息与工具列表。"""

import sys
from pathlib import Path

from min_agent.application import HarnessApplication
from min_agent.contracts.models import ModelResponse, ToolCall
from min_agent.mcp.spec import MCPServerSpec
from tests.fakes.model import ScriptedModel

SERVER = Path(__file__).resolve().parents[1] / "fakes" / "mcp_server.py"


def fake_spec():
    return MCPServerSpec(
        name="fake", description="Fake MCP server for tests.",
        command=sys.executable, args=["-u", str(SERVER)], timeout_seconds=10,
    )


def test_application_wires_mcp_progressively_and_persists_results(tmp_path):
    model = ScriptedModel([
        ModelResponse(finish_reason="tool_calls", tool_calls=[
            ToolCall(id="c1", name="connect_mcp", arguments={"name": "fake"}),
        ]),
        ModelResponse(finish_reason="tool_calls", tool_calls=[
            ToolCall(id="c2", name="mcp__fake__echo", arguments={"message": "hello"}),
        ]),
        ModelResponse(finish_reason="stop", content="完成"),
    ])
    app = HarnessApplication(tmp_path / "mcp.sqlite3", model=model, mcp_servers=(fake_spec(),))
    try:
        identity = app.create_session()["session_id"]
        app.submit(identity, "连接 fake 并回显")
        app.wait(identity, timeout=30)
        saved = app.snapshot(identity)
    finally:
        app.close()

    assert saved["task"]["status"] == "completed", saved["task"].get("error_code")
    first_names = [schema["name"] for schema in model.tool_schemas[0]]
    assert "connect_mcp" in first_names
    assert not any(name.startswith("mcp__fake__") for name in first_names)
    system = model.calls[0][0].content
    assert "Fake MCP server for tests." in system and "Echo the message back." not in system

    second_names = [schema["name"] for schema in model.tool_schemas[1]]
    assert "mcp__fake__echo" in second_names
    tool_messages = [message for message in saved["messages"] if message["role"] == "tool"]
    assert any("echo:hello" in message["content"] for message in tool_messages)
