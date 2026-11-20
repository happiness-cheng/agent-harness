"""MCP 服务器目录：后台默认连接、发现工具、并入 ToolRegistry、生成元数据段。

元数据披露：连接成功后 SystemPrompt 段只公开工具 ``name + description``；
工具以 prompt_only 方式注册（可调用、不进 tools 数组），完整 schema 在
调用入口校验。command/args/env 永远不出现在模型可见文本里。
"""

import threading
import weakref
from dataclasses import dataclass
from typing import Callable

from min_agent.ledger.resource_budget import estimate_tokens
from min_agent.mcp.client import MCPError, StdioMCPClient
from min_agent.mcp.spec import MCPServerSpec, normalize_tool_name, prefixed_tool_name

MCP_SECTION_TOKEN_LIMIT = 600


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
        self._bg_started = False
        self._state: dict[str, dict] = {}

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
            result = {}
            for name, spec in self._specs.items():
                connection = self._connections.get(name)
                if connection is not None:
                    result[name] = {
                        "state": "connected",
                        "tools": [tool.name for tool in connection.tools],
                        "policy": spec.policy,
                    }
                else:
                    result[name] = {**self._state.get(name, {"state": "idle"}),
                                    "policy": spec.policy}
            return result

    # ---------------------------------------------------------------- lifecycle
    def connect_background(self) -> None:
        """启动时后台逐个连接；不阻塞调用方，失败记状态供 connect_mcp 重试。"""
        with self._lock:
            if self._closed or self._bg_started or not self._specs:
                return
            self._bg_started = True
            for name in self._specs:
                self._state[name] = {"state": "connecting"}
        threading.Thread(target=self._connect_all, daemon=True).start()

    def _connect_all(self) -> None:
        for name in list(self._specs):
            with self._lock:
                if self._closed:
                    return
            try:
                self.connect(name)
            except MCPError as error:
                with self._lock:
                    self._state[name] = {"state": "failed", "error": str(error)}
            except Exception:
                # 异常文本可能含命令/环境细节，只记固定码，不回显给模型或界面。
                with self._lock:
                    self._state[name] = {"state": "failed", "error": "connect_failed"}

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
            self._state[name] = {"state": "connected"}
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
    def bind(self, registry, confirmations=None) -> None:
        """把已连接服务器的工具以 prompt_only 方式绑定；重复调用只补缺。"""
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
                registry.register(build_mcp_tool(connection, tool, confirmations))
                bound.add(name)

    # ---------------------------------------------------------------- prompt
    def prompt_section(self) -> str:
        """只暴露工具 name + description；不暴露 command/args/env 与完整 schema。

        超 token 上限降级为只放名字，再超按序截断；未连接服务器清单一并计入
        上限（追加后复核），且区分「正在连接 / 失败 / 未连接」，避免模型在
        后台连接尚未完成时误以为必须调 connect_mcp。
        """
        with self._lock:
            available = [spec for name, spec in self._specs.items()
                         if name not in self._connections]
            connected = list(self._connections.values())
            states = {spec.name: dict(self._state.get(spec.name, {"state": "idle"}))
                      for spec in available}
        if not available and not connected:
            return ""
        header = "## MCP 工具（按名直接调用）"
        lines = []
        for connection in connected:
            confirm = connection.spec.policy == "confirm"
            for tool in connection.tools:
                name = prefixed_tool_name(connection.spec.name, tool.name)
                description = " ".join(tool.description.split())
                suffix = "（调用需用户在界面确认）" if confirm else ""
                lines.append(f"- {name}：{description}{suffix}")
        footer = ("参数按工具说明传入；调用时才按完整 schema 校验。"
                  "MCP 调用是否允许由宿主策略与用户确认决定，与工作模式无关，任何模式都可发起。"
                  "标记需确认的工具会挂起等待用户批准，不要重复发起同一调用。")
        variants = ["\n".join([header, *lines, footer])]
        variants.append("\n".join([header, *[line.split("：")[0] for line in lines], footer]))
        kept = []
        for line in lines:
            name = line.split("：")[0]
            candidate = "\n".join([header, *kept, name, "（清单超长，已截断）", footer])
            if estimate_tokens(candidate) > MCP_SECTION_TOKEN_LIMIT:
                break
            kept.append(name)
        variants.append("\n".join([header, *kept, "（清单超长，已截断）", footer]))
        for variant in variants:
            for detailed in (True, False):
                candidate = self._append_available(variant, available, states, detailed=detailed)
                if estimate_tokens(candidate) <= MCP_SECTION_TOKEN_LIMIT:
                    return candidate
        fallback = "\n".join([header, "（工具清单超长，已省略；可直接按名调用已知工具）", footer])
        for detailed in (True, False):
            candidate = self._append_available(fallback, available, states, detailed=detailed)
            if estimate_tokens(candidate) <= MCP_SECTION_TOKEN_LIMIT:
                return candidate
        return "\n".join([header, "（清单超长已省略）", footer])

    @staticmethod
    def _append_available(section: str, available, states, *, detailed: bool) -> str:
        if not available:
            return section
        lines = [section, "尚未就绪的服务器："]
        for spec in available:
            state = states.get(spec.name, {}).get("state", "idle")
            if state == "connecting":
                text = "正在后台连接，稍后自动可用；不必调用 connect_mcp"
            elif state == "failed":
                text = "连接失败，可调用 connect_mcp 重试"
            else:
                text = "尚未连接，可调用 connect_mcp 重试"
            if detailed and spec.description:
                text += "：" + " ".join(spec.description.split())
            lines.append(f"- {spec.name}：{text}")
        return "\n".join(lines)
