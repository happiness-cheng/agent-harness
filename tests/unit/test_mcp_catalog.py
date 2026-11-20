"""MCP 目录契约：后台连接、prompt_only 披露、调用时校验、confirm 无入口即拒绝。"""

import json
import sys
import time
from pathlib import Path

import pytest

from min_agent.contracts.models import ToolCall
from min_agent.ledger.resource_budget import estimate_tokens
from min_agent.mcp.catalog import MCP_SECTION_TOKEN_LIMIT, MCPServerCatalog
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


def catalog_with(*specs, **kwargs):
    return MCPServerCatalog(tuple(specs), **kwargs)


class FakeClient:
    """不起子进程的假客户端；用于状态机与提示词上限测试。"""

    def __init__(self, tools=(), *, fail=None):
        self._tools = list(tools)
        self._fail = fail
        self.healthy = True
        self.closed = False

    def start(self):
        if self._fail is not None:
            raise self._fail

    def list_tools(self):
        return self._tools

    def close(self):
        self.closed = True
        self.healthy = False

    def call_tool(self, name, arguments):
        return f"called:{name}"


def fake_discovered(count, description="short"):
    return [{"name": f"tool_{index}", "description": description,
             "inputSchema": {"type": "object"}} for index in range(count)]


def wait_state(catalog, name, state, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if catalog.status()[name]["state"] == state:
            return
        time.sleep(0.02)
    raise AssertionError(f"{name} 未在 {timeout}s 内进入 {state}：{catalog.status()}")


def test_prompt_only_tools_are_executable_but_absent_from_schemas():
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

        # 连接后工具仍不进 tools 数组：元数据只在 SystemPrompt 段披露。
        assert [schema["name"] for schema in registry.schemas()] == ["connect_mcp"]
        assert registry.get("mcp__fake__echo").prompt_only is True
        section = catalog.prompt_section()
        assert "mcp__fake__echo" in section and "Echo the message back." in section
        assert sys.executable not in section and str(SERVER) not in section

        result = registry.execute(ToolCall(id="c2", name="mcp__fake__echo",
                                           arguments={"message": "hi"}))
        assert result == {"ok": True, "result": "echo:hi"}
        assert registry.execute(ToolCall(id="c3", name="mcp__fake__add",
                                         arguments={"a": 2, "b": 4})) == {
            "ok": True, "result": "6"}

        # 完整 schema 在调用入口校验：缺 required 参数本地拒绝，不到服务器。
        bad = registry.execute(ToolCall(id="c4", name="mcp__fake__echo", arguments={}))
        assert bad["ok"] is False
        assert bad["error"]["code"] == "invalid_arguments"
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
        assert [schema["name"] for schema in registry.schemas()] == ["connect_mcp"]
        assert registry.execute(ToolCall(id="c1", name="mcp__fake__echo",
                                         arguments={"message": "once"})) == {
            "ok": True, "result": "echo:once"}
    finally:
        catalog.close()


def test_confirm_policy_without_session_entry_refuses_without_calling_server():
    catalog = catalog_with(fake_spec(policy="confirm"))
    registry = ToolRegistry()
    try:
        register_mcp_tools(registry, catalog)
        registry.execute(ToolCall(id="c1", name="connect_mcp", arguments={"name": "fake"}))
        result = registry.execute(ToolCall(id="c2", name="mcp__fake__echo",
                                           arguments={"message": "hi"}))
        assert result["ok"] is False
        assert result["error"]["code"] == "confirmation_unavailable"
        assert result["error"]["refused_by_user"] is False
        assert "未执行" in result["error"]["message"]
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
        # 全部已连接：新 registry 无需 connect_mcp，工具可调用但不进 schemas。
        assert second.schemas() == []
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


def test_background_connect_reaches_connected_and_reports_tools():
    client = FakeClient(fake_discovered(2, description="Echo the message back."))
    catalog = catalog_with(fake_spec(), client_factory=lambda spec: client)
    try:
        assert catalog.status()["fake"]["state"] == "idle"
        catalog.connect_background()
        catalog.connect_background()  # 重复启动不产生第二次连接
        wait_state(catalog, "fake", "connected")
        status = catalog.status()["fake"]
        assert status["tools"] == ["tool_0", "tool_1"]
        assert status["policy"] == "allow"
        assert catalog.connected() == ("fake",)
    finally:
        catalog.close()


def test_background_failure_hides_command_details_and_allows_retry():
    attempts = []

    def factory(spec):
        attempts.append(spec.name)
        if len(attempts) == 1:
            raise RuntimeError(f"secret detail {spec.command}")
        return FakeClient(fake_discovered(1))

    catalog = catalog_with(fake_spec(), client_factory=factory)
    registry = ToolRegistry()
    try:
        catalog.connect_background()
        wait_state(catalog, "fake", "failed")
        status = catalog.status()["fake"]
        assert status["error"] == "connect_failed"
        assert "secret" not in json.dumps(status, ensure_ascii=False)
        # 失败后 connect_mcp 仍可重试；重试成功即转入 connected。
        register_mcp_tools(registry, catalog)
        outcome = registry.execute(ToolCall(id="c1", name="connect_mcp",
                                            arguments={"name": "fake"}))
        assert outcome["ok"] is True
        assert catalog.status()["fake"]["state"] == "connected"
    finally:
        catalog.close()


def test_background_mcp_error_message_is_kept_as_state():
    catalog = catalog_with(
        fake_spec(), client_factory=lambda spec: FakeClient(fail=MCPError("handshake failed")))
    try:
        catalog.connect_background()
        wait_state(catalog, "fake", "failed")
        assert catalog.status()["fake"]["error"] == "handshake failed"
    finally:
        catalog.close()


def test_prompt_section_over_limit_falls_back_to_names_only():
    tools = fake_discovered(30, description="long description word " * 25)
    catalog = catalog_with(fake_spec(), client_factory=lambda spec: FakeClient(tools))
    try:
        catalog.connect("fake")
        section = catalog.prompt_section()
        assert estimate_tokens(section) <= MCP_SECTION_TOKEN_LIMIT
        assert "long description word" not in section
        for index in range(30):
            assert f"mcp__fake__tool_{index}" in section
    finally:
        catalog.close()


def test_prompt_section_names_only_over_limit_is_truncated_with_marker():
    tools = fake_discovered(300)
    catalog = catalog_with(fake_spec(), client_factory=lambda spec: FakeClient(tools))
    try:
        catalog.connect("fake")
        section = catalog.prompt_section()
        assert estimate_tokens(section) <= MCP_SECTION_TOKEN_LIMIT
        assert "（清单超长，已截断）" in section
        assert "mcp__fake__tool_0" in section
        assert "mcp__fake__tool_299" not in section
    finally:
        catalog.close()


def test_prompt_section_lists_unconnected_servers_for_retry():
    catalog = catalog_with(fake_spec())
    try:
        section = catalog.prompt_section()
        assert "fake" in section and "Fake MCP server for tests." in section
        assert "connect_mcp" in section
        assert sys.executable not in section
    finally:
        catalog.close()


def test_server_error_is_ok_false_not_success_text():
    class FailingClient(FakeClient):
        def call_tool(self, name, arguments):
            raise MCPError("server exploded")

    catalog = catalog_with(
        fake_spec(), client_factory=lambda spec: FailingClient(fake_discovered(1)))
    registry = ToolRegistry()
    try:
        catalog.connect("fake")
        catalog.bind(registry)
        outcome = registry.execute(ToolCall(
            id="c1", name="mcp__fake__tool_0", arguments={}))
        assert outcome["ok"] is False
        assert outcome["error"]["code"] == "tool_execution_failed"
        assert outcome["error"]["execution_state"] == "unknown"
        assert "MCP error" not in outcome.get("result", "")
    finally:
        catalog.close()


def test_connecting_state_and_available_list_stay_within_token_limit():
    spec = fake_spec(description="很" * 500)
    catalog = catalog_with(spec)
    try:
        catalog._state["fake"] = {"state": "connecting"}
        section = catalog.prompt_section()
        assert estimate_tokens(section) <= MCP_SECTION_TOKEN_LIMIT
        assert "正在后台连接" in section
        assert "很" * 20 not in section
    finally:
        catalog.close()
