"""离线测试用的最小 MCP stdio 服务器；只实现协议子集，不联网。"""

import json
import sys

TOOLS = [
    {
        "name": "echo",
        "description": "Echo the message back.",
        "inputSchema": {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
    },
    {
        "name": "add",
        "description": "Add two integers.",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        },
    },
]


def send(payload):
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def call(name, arguments):
    if name == "echo":
        return f"echo:{arguments.get('message', '')}"
    if name == "add":
        return str(int(arguments["a"]) + int(arguments["b"]))
    raise ValueError(f"unknown tool {name}")


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        message = json.loads(line)
        if "id" not in message:
            continue
        request_id = message["id"]
        method = message.get("method")
        params = message.get("params") or {}
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": request_id, "result": {
                "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake-mcp", "version": "0.0.1"},
            }})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": request_id,
                  "result": {"tools": TOOLS}})
        elif method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            try:
                text = call(name, arguments)
            except Exception as error:  # noqa: BLE001 - 模拟服务器错误
                send({"jsonrpc": "2.0", "id": request_id, "result": {
                    "content": [{"type": "text", "text": f"{type(error).__name__}: {error}"}],
                    "isError": True,
                }})
            else:
                send({"jsonrpc": "2.0", "id": request_id, "result": {
                    "content": [{"type": "text", "text": text}],
                    "isError": False,
                }})
        elif method == "ping":
            send({"jsonrpc": "2.0", "id": request_id, "result": {}})
        else:
            send({"jsonrpc": "2.0", "id": request_id,
                  "error": {"code": -32601, "message": f"method not found: {method}"}})


if __name__ == "__main__":
    main()
