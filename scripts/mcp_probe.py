"""手工探针：连接配置里的真实 MCP 服务器，发现工具并可选调用一次。

只做 MCP 协议验证，不调用任何模型、不写会话数据库、不发聊天内容。
"""

import argparse
import json
import sys

from min_agent.mcp.catalog import MCPServerCatalog
from min_agent.mcp.client import MCPError
from min_agent.mcp.spec import load_mcp_config


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="真实 MCP 服务器连通性探针")
    parser.add_argument("--config", required=True, help="MCP 配置 JSON")
    parser.add_argument("--server", help="服务器名；默认取配置中的第一个")
    parser.add_argument("--call", help="要调用的工具名（可选）")
    parser.add_argument("--args", default="{}", help="工具参数 JSON，默认 {}")
    options = parser.parse_args(argv)

    servers = load_mcp_config(options.config)
    if not servers:
        print(json.dumps({"ok": False, "error": "配置中没有 servers"}, ensure_ascii=False))
        return 1
    name = options.server or servers[0].name
    catalog = MCPServerCatalog(servers)
    try:
        connection = catalog.connect(name)
        result = {
            "ok": True,
            "server": connection.spec.name,
            "tool_count": len(connection.tools),
            "tools": [
                {"name": tool.name, "description": tool.description,
                 "required": tool.schema.get("required", [])}
                for tool in connection.tools
            ],
        }
        if options.call:
            arguments = json.loads(options.args)
            if not isinstance(arguments, dict):
                raise ValueError("--args 必须是 JSON 对象")
            result["call"] = {
                "tool": options.call,
                "result": connection.client.call_tool(options.call, arguments),
            }
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (MCPError, ValueError, OSError) as error:
        print(json.dumps({"ok": False, "error": f"{type(error).__name__}: {error}"},
                         ensure_ascii=False))
        return 1
    finally:
        catalog.close()


if __name__ == "__main__":
    sys.exit(main())
