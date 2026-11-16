"""MCP 渐进式接入：先暴露服务器 name/description，连接后再并入工具。"""

from min_agent.mcp.catalog import MCPConnection, MCPServerCatalog, MCPTool
from min_agent.mcp.client import MCPError, StdioMCPClient
from min_agent.mcp.spec import MCPServerSpec, load_mcp_config, prefixed_tool_name
from min_agent.mcp.tools import register_mcp_tools

__all__ = [
    "MCPConnection", "MCPServerCatalog", "MCPTool", "MCPError", "StdioMCPClient",
    "MCPServerSpec", "load_mcp_config", "prefixed_tool_name", "register_mcp_tools",
]
