"""MCP 目录与 ToolRegistry 的合并契约：连接前不泄露工具、连接后可用。"""

import json
import sys
from pathlib import Path

import pytest

from min_agent.contracts.models import ToolCall
from min_agent.mcp.catalog import MCPServerCatalog
from min_agent.mcp.client import MCPError
from min_agent.mcp.spec import MCPServerSpec
from min_agent.mcp.tools import register_mcp_tools
from min_agent.tools.registry import ToolRegistry

SERVER = Path(__file__).resolve().parents[1] / "fakes" / "mcp_server.py"


def fake_spec(**overrides):
    payload = dict(name="fake", description="Fake MCP server for tests.",
                   command=sys.executable, args=["-u", str(SERVER)], timeout_seconds=10)
    payload.update(overrides)
    return MCPServerSpec(**payload)


def catalog_with(*specs):
    return MCPServerCatalog(tuple(specs))


def test_before_connect_only_meta_tool_is_exposed_after_connect_tools_merge():
    catalog = catalog_with(fake_spec())
    registry = ToolRegistry()
    try:
        register_mcp_tools(registry, catalog)
        names = [schema["name"] for schema in registry.schemas()]
        assert names == ["connect_mcp"]
        connect_schema = registry.schemas()[0]["parameters"]
        assert "fake" in json.dumps(connect_schema, ensure_ascii=False)

        outcome = registry.execute(ToolCall(id="c1", name="connect_mcp",
                                            arguments={"name": "fake"}))
        assert outcome["ok"] is True
        connected = json.loads(outcome["result"])
        assert connected["connected"] == "fake"
        assert connected["tool_count"] == 2
        assert connected["tools"] == ["mcp__fake__echo", "mcp__fake__add"]

        names = [schema["name"] for schema in registry.schemas()]
        assert names == ["connect_mcp", "mcp__fake__echo", "mcp__fake__add"]
        echo_schema = next(schema for schema in registry.schemas()
                           if schema["name"] == "mcp__fake__echo")
        assert echo_schema["parameters"]["required"] == ["message"]
        assert echo_schema["parameters"]["properties"]["message"]["type"] == "string"
        assert echo_schema["description"].startswith("[MCP fake]")

        result = registry.execute(ToolCall(id="c2", name="mcp__fake__echo",
                                           arguments={"message": "hi"}))
        assert result == {"ok": True, "result": "echo:hi"}
        assert registry.execute(ToolCall(id="c3", name="mcp__fake__add",
                                         arguments={"a": 2, "b": 4})) == {
            "ok": True, "result": "6"}
    finally:
        catalog.close()


def test_connect_is_idempotent_and_bind_is_repeatable():
    catalog = catalog_with(fake_spec())
    registry = ToolRegistry()
    try:
        register_mcp_tools(registry, catalog)
        first = catalog.connect("fake")
        second = catalog.connect("fake")
        assert first is second
        catalog.bind(registry)
        catalog.bind(registry)
        assert len([schema for schema in registry.schemas()
                    if schema["name"].startswith("mcp__fake__")]) == 2
    finally:
        catalog.close()


def test_confirm_policy_refuses_without_calling_server():
    catalog = catalog_with(fake_spec(policy="confirm"))
    registry = ToolRegistry()
    try:
        register_mcp_tools(registry, catalog)
        registry.execute(ToolCall(id="c1", name="connect_mcp", arguments={"name": "fake"}))
        result = registry.execute(ToolCall(id="c2", name="mcp__fake__echo",
                                           arguments={"message": "hi"}))
        assert result["ok"] is True
        assert "需要宿主确认" in result["result"]
    finally:
        catalog.close()


def test_unknown_server_is_a_correctable_request_error():
    catalog = catalog_with(fake_spec())
    registry = ToolRegistry()
    try:
        register_mcp_tools(registry, catalog)
        outcome = registry.execute(ToolCall(id="c1", name="connect_mcp",
                                            arguments={"name": "nope"}))
        assert outcome["ok"] is False
        assert outcome["error"]["code"] == "invalid_arguments"
        with pytest.raises(MCPError):
            catalog.connect("nope")
    finally:
        catalog.close()


def test_catalog_rebinds_connected_tools_into_a_new_registry():
    catalog = catalog_with(fake_spec())
    first, second = ToolRegistry(), ToolRegistry()
    try:
        register_mcp_tools(first, catalog)
        first.execute(ToolCall(id="c1", name="connect_mcp", arguments={"name": "fake"}))
        register_mcp_tools(second, catalog)
        names = [schema["name"] for schema in second.schemas()]
        assert names == ["connect_mcp", "mcp__fake__echo", "mcp__fake__add"]
        assert second.execute(ToolCall(id="c2", name="mcp__fake__echo",
                                       arguments={"message": "again"})) == {
            "ok": True, "result": "echo:again"}
    finally:
        catalog.close()


def test_broken_connection_is_replaced_on_next_connect():
    catalog = catalog_with(fake_spec())
    try:
        first = catalog.connect("fake")
        first.client.close()
        second = catalog.connect("fake")
        assert second is not first
        assert second.client.healthy
        assert second.client.call_tool("echo", {"message": "again"}) == "echo:again"
    finally:
        catalog.close()


def test_normalized_tool_name_collision_is_rejected_at_connect():
    catalog = catalog_with(fake_spec())
    try:
        with pytest.raises(MCPError):
            catalog._build_tools("fake", [
                {"name": "a.b", "description": "", "inputSchema": {"type": "object"}},
                {"name": "a_b", "description": "", "inputSchema": {"type": "object"}},
            ])
    finally:
        catalog.close()


def test_name_collision_is_rejected_before_partial_registration():
    from pydantic import BaseModel, ConfigDict
    from min_agent.tools.registry import ToolDefinition

    class _Args(BaseModel):
        model_config = ConfigDict(extra="allow")

    catalog = catalog_with(fake_spec())
    registry = ToolRegistry()
    try:
        registry.register(ToolDefinition(
            name="mcp__fake__echo", description="collision", parameters=_Args,
            handler=lambda **kwargs: "collision"))
        catalog.connect("fake")
        with pytest.raises(MCPError):
            catalog.bind(registry)
        assert [schema["name"] for schema in registry.schemas()] == ["mcp__fake__echo"]
    finally:
        catalog.close()
