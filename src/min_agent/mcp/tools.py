"""把 MCP 服务器与工具映射成现有 ToolRegistry 的 ``ToolDefinition``。"""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from min_agent.mcp.catalog import MCPConnection, MCPTool
from min_agent.mcp.client import MCPError
from min_agent.mcp.spec import prefixed_tool_name


class _MCPArguments(BaseModel):
    """MCP 参数原样透传；本地只补 raw_schema 中 required/简单类型的校验。"""

    model_config = ConfigDict(extra="allow")


def build_mcp_tool(connection: MCPConnection, tool: MCPTool):
    """一个已连接工具 → ToolDefinition；schema 用服务器原样，不重写。"""
    from min_agent.tools.registry import ToolDefinition

    name = prefixed_tool_name(connection.spec.name, tool.name)
    description = f"[MCP {connection.spec.name}] {tool.description}".strip()

    def handler(**arguments):
        if connection.spec.policy == "confirm":
            return f"MCP 工具 {tool.name} 需要宿主确认；本次未执行。"
        try:
            return connection.client.call_tool(tool.name, arguments)
        except MCPError as error:
            return f"MCP error: {error}"

    return ToolDefinition(
        name=name,
        description=description,
        parameters=_MCPArguments,
        handler=handler,
        raw_schema=tool.schema,
    )


def _connect_tool(catalog, registry):
    from min_agent.tools.registry import ToolDefinition

    names = tuple(spec.name for spec in catalog.available())
    if not names:
        return None
    # Literal 在 JSON Schema 里就是 enum；strict 模式仍接受精确字符串。
    server_names = Literal[*names]
    parameters = create_model(
        "ConnectMCPArguments",
        name=(server_names, Field(description="要连接的 MCP 服务器名")),
        __config__=ConfigDict(strict=True, extra="forbid"),
    )
    summary = "；".join(f"{spec.name}（{' '.join(spec.description.split())}）"
                       for spec in catalog.available())

    def connect(name):
        server_name = str(name)
        try:
            connection = catalog.connect(server_name)
            catalog.bind(registry)
        except MCPError as error:
            return json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False)
        return json.dumps({
            "ok": True,
            "connected": connection.spec.name,
            "tool_count": len(connection.tools),
            # 只回名称，详细说明在下一轮工具 schema 里；避免把大段目录塞进工具结果。
            "tools": [prefixed_tool_name(connection.spec.name, tool.name)
                      for tool in connection.tools[:40]],
            "truncated": len(connection.tools) > 40,
        }, ensure_ascii=False)

    return ToolDefinition(
        name="connect_mcp",
        description=(
            "连接一个已配置的 MCP 服务器并发现它的工具。连接前只公开服务器名称和说明；"
            "连接成功后其工具才会并入工具列表。可用：" + summary
        ),
        parameters=parameters,
        handler=connect,
    )


def register_mcp_tools(registry, catalog) -> None:
    """注册 ``connect_mcp`` 元工具，并绑定已连接工具；没有配置时什么都不做。"""
    if not catalog.available():
        return
    try:
        registry.get("connect_mcp")
    except KeyError:
        tool = _connect_tool(catalog, registry)
        if tool is not None:
            registry.register(tool)
    catalog.bind(registry)
