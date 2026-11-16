"""MCP 服务器目录：按需连接、发现工具、并入 ToolRegistry、生成提示词段。

渐进式披露：连接前只公开服务器 ``name + description``；连接成功后，
该服务器的工具才以 ``mcp__server__tool`` 的名字进入工具列表。
"""

import threading
import weakref
from dataclasses import dataclass
from typing import Callable

from min_agent.mcp.client import MCPError, StdioMCPClient
from min_agent.mcp.spec import MCPServerSpec, normalize_tool_name, prefixed_tool_name


@dataclass(frozen=True)
class MCPTool:
    """发现到的 MCP 工具；schema 保留服务器原样，不重写。"""

    server: str
    name: str
    description: str
    schema: dict


@dataclass(frozen=True)
class MCPConnection:
    spec: MCPServerSpec
    client: object
    tools: tuple[MCPTool, ...]


class MCPServerCatalog:
    """一个会话内的 MCP 状态；连接持久到会话关闭，工具绑定可重复执行。"""

    def __init__(
        self,
        specs: tuple[MCPServerSpec, ...] | list[MCPServerSpec],
        *,
        client_factory: Callable[[MCPServerSpec], object] | None = None,
    ) -> None:
        names = [spec.name for spec in specs]
        if len(set(names)) != len(names):
            raise ValueError("MCP 服务器名重复")
        self._specs = {spec.name: spec for spec in specs}
        self._factory = client_factory or StdioMCPClient
        self._connections: dict[str, MCPConnection] = {}
        # 每个 ToolRegistry 自己记一份已绑定名字；同一 catalog 会被多个任务复用。
        self._bound: "weakref.WeakKeyDictionary[object, set[str]]" = weakref.WeakKeyDictionary()
        self._lock = threading.RLock()
        self._closed = False

    # ---------------------------------------------------------------- queries
    def available(self) -> tuple[MCPServerSpec, ...]:
        return tuple(self._specs[name] for name in self._specs)

    def connected(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._connections)

    def tools(self) -> tuple[MCPTool, ...]:
        with self._lock:
            return tuple(tool for conn in self._connections.values() for tool in conn.tools)

    def status(self) -> dict[str, dict[str, object]]:
        with self._lock:
            return {
                name: {
                    "connected": True,
                    "tools": [tool.name for tool in connection.tools],
                    "policy": connection.spec.policy,
                }
                for name, connection in self._connections.items()
            }

    # ---------------------------------------------------------------- lifecycle
    def connect(self, name: str) -> MCPConnection:
        with self._lock:
            if self._closed:
                raise MCPError("MCP 目录已关闭")
            if name in self._connections:
                existing = self._connections[name]
                if getattr(existing.client, "healthy", False):
                    return existing
                # 进程退出或协议损坏：关闭旧连接，下一次绑定时重新注册到新 registry。
                existing.client.close()
                del self._connections[name]
            spec = self._specs.get(name)
            if spec is None:
                raise MCPError(f"未知 MCP 服务器：{name}")
            client = self._factory(spec)
            try:
                client.start()
                discovered = client.list_tools()
                tools = self._build_tools(name, discovered)
            except BaseException:
                client.close()
                raise
            connection = MCPConnection(spec=spec, client=client, tools=tools)
            self._connections[name] = connection
            return connection

    @staticmethod
    def _build_tools(server: str, discovered: list[dict]) -> tuple[MCPTool, ...]:
        """发现结果 → MCPTool；规范化后的重名与超长名字在连接时就拒绝。"""
        tools = []
        seen: set[str] = set()
        for item in discovered:
            raw_name = item["name"]
            safe = normalize_tool_name(raw_name)
            if safe in seen:
                raise MCPError(f"MCP 工具规范化后重名：{raw_name}")
            seen.add(safe)
            prefixed_tool_name(server, raw_name)  # 长度/字符集校验，失败即拒绝连接
            tools.append(MCPTool(
                server=server,
                name=raw_name,
                description=str(item.get("description") or ""),
                schema=item["inputSchema"],
            ))
        return tuple(tools)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            connections = list(self._connections.values())
            self._connections.clear()
            self._bound.clear()
        for connection in connections:
            connection.client.close()

    # ---------------------------------------------------------------- registry
    def bind(self, registry) -> None:
        """把已连接服务器的工具绑定进 registry；重复调用只补缺，不重复注册。"""
        from min_agent.mcp.tools import build_mcp_tool

        with self._lock:
            bound = self._bound.setdefault(registry, set())
            pending = []
            for connection in self._connections.values():
                for tool in connection.tools:
                    try:
                        name = prefixed_tool_name(connection.spec.name, tool.name)
                    except ValueError as error:
                        raise MCPError(str(error)) from error
                    if name in bound:
                        continue
                    try:
                        registry.get(name)
                    except KeyError:
                        pass
                    else:
                        raise MCPError(f"MCP 工具名与已有工具冲突：{name}")
                    pending.append((name, connection, tool))
            for name, connection, tool in pending:
                registry.register(build_mcp_tool(connection, tool))
                bound.add(name)

    # ---------------------------------------------------------------- prompt
    def prompt_section(self) -> str:
        """只暴露名称与说明；不暴露 command/args/env，也不引用服务器自述权限。"""
        with self._lock:
            available = [spec for name, spec in self._specs.items() if name not in self._connections]
            connected = list(self._connections.values())
        if not available and not connected:
            return ""
        lines = ["## MCP 服务器（按需连接）"]
        if available:
            lines.append("可用服务器（当前只公开名称与说明，工具尚未并入）：")
            for spec in available:
                lines.append(f"- {spec.name}：{' '.join(spec.description.split())}")
        if connected:
            lines.append("已连接的服务器：")
            for connection in connected:
                lines.append(f"- {connection.spec.name}：{len(connection.tools)} 个工具已并入")
        if available:
            lines.append("需要时调用 connect_mcp(name)；连接成功后其工具才会出现在工具列表。")
        return "\n".join(lines)
