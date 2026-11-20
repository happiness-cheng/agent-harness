"""把 MCP 服务器与工具映射成现有 ToolRegistry 的 ``ToolDefinition``。

工具以 prompt_only 注册：可调用、不进模型 tools 数组；元数据在 SystemPrompt
的 MCP 段披露，完整 schema 在 registry.execute 入口校验。confirm 策略走
会话级排队（挂起 → 用户批准/拒绝 → 恢复执行/明确拒绝）。
"""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from min_agent.mcp.catalog import MCPConnection, MCPTool
from min_agent.mcp.client import MCPError
from min_agent.mcp.confirm import ConfirmationBacklogError
from min_agent.mcp.spec import prefixed_tool_name
from min_agent.session.task_state import AuthorizationRequired
from min_agent.tools.registry import ToolExecutionFailedError, ToolRefusedError


class _MCPArguments(BaseModel):
    """MCP 参数原样透传；本地只补 raw_schema 中 required/简单类型的校验。"""

    model_config = ConfigDict(extra="allow")


def build_mcp_tool(connection: MCPConnection, tool: MCPTool, confirmations=None):
    """一个已连接工具 → prompt_only ToolDefinition；schema 用服务器原样。"""
    from min_agent.tools.registry import ToolDefinition

    name = prefixed_tool_name(connection.spec.name, tool.name)
    description = f"[MCP {connection.spec.name}] {tool.description}".strip()

    def handler(**arguments):
        if connection.spec.policy == "confirm":
            if confirmations is None:
                raise ToolRefusedError(
                    f"MCP 工具 {tool.name} 为 confirm 策略，但当前没有会话确认入口；未执行。",
                    reason="confirmation_unavailable")
            try:
                decision = confirmations.check_and_consume(name, arguments)
            except PermissionError as error:
                raise ToolRefusedError(str(error), reason="confirmation_unavailable")
            except ConfirmationBacklogError as error:
                raise ToolRefusedError(str(error), reason="backlog_full")
            if decision == "refused":
                raise ToolRefusedError(
                    f"用户已拒绝 MCP 调用 {tool.name}；未执行，不要重试同一调用。",
                    reason="user_refused")
            if decision == "consumed":
                raise ToolRefusedError(
                    f"MCP 调用 {tool.name} 的批准已消费（此前已执行）；不要重复执行。",
                    reason="already_consumed")
            if decision != "approved":
                try:
                    confirmations.request(server=connection.spec.name, tool=tool.name,
                                          prefixed=name, arguments=arguments)
                except ConfirmationBacklogError as error:
                    raise ToolRefusedError(str(error), reason="backlog_full")
                raise AuthorizationRequired("waiting_user")
        try:
            return connection.client.call_tool(tool.name, arguments)
        except MCPError as error:
            # 服务器报错必须走 ok:false，不能包成成功文本让模型自己猜。
            raise ToolExecutionFailedError(f"MCP 工具调用失败：{error}") from error

    return ToolDefinition(
        name=name,
        description=description,
        parameters=_MCPArguments,
        handler=handler,
        raw_schema=tool.schema,
        prompt_only=True,
    )


def _connect_tool(catalog, registry, confirmations=None):
    from min_agent.tools.registry import ToolDefinition

    connected = set(catalog.connected())
    specs = tuple(spec for spec in catalog.available() if spec.name not in connected)
    if not specs:
        return None
    names = tuple(spec.name for spec in specs)
    # Literal 在 JSON Schema 里就是 enum；strict 模式仍接受精确字符串。
    server_names = Literal[*names]
    parameters = create_model(
        "ConnectMCPArguments",
        name=(server_names, Field(description="要连接的 MCP 服务器名")),
        __config__=ConfigDict(strict=True, extra="forbid"),
    )
    summary = "；".join(f"{spec.name}（{' '.join(spec.description.split())}）"
                       for spec in specs)

    def connect(name):
        server_name = str(name)
        try:
            connection = catalog.connect(server_name)
            catalog.bind(registry, confirmations)
        except MCPError as error:
            return json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False)
        return json.dumps({
            "ok": True,
            "connected": connection.spec.name,
            "tool_count": len(connection.tools),
            # 只回名称，详细说明在下一轮提示词段里；避免把大段目录塞进工具结果。
            "tools": [prefixed_tool_name(connection.spec.name, tool.name)
                      for tool in connection.tools[:40]],
            "truncated": len(connection.tools) > 40,
        }, ensure_ascii=False)

    return ToolDefinition(
        name="connect_mcp",
        description=(
            "连接一个尚未连接的 MCP 服务器并发现它的工具（启动时已后台连接成功的不在列）。"
            "连接成功后其工具名与说明出现在系统提示词 MCP 段。可用：" + summary
        ),
        parameters=parameters,
        handler=connect,
    )


def register_mcp_tools(registry, catalog, confirmations=None) -> None:
    """绑定已连接工具（prompt_only）；仅对未连接服务器注册 ``connect_mcp``。"""
    if not catalog.available():
        return
    try:
        registry.get("connect_mcp")
    except KeyError:
        tool = _connect_tool(catalog, registry, confirmations)
        if tool is not None:
            registry.register(tool)
    catalog.bind(registry, confirmations)
