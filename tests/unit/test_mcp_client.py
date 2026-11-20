"""MCP stdio 客户端：真实子进程、JSON-RPC 握手、发现与调用。"""

import sys
from pathlib import Path

import pytest

from min_agent.mcp.client import MCPError, MCPProtocolVersionError, StdioMCPClient
from min_agent.mcp.spec import MCPServerSpec

SERVER = Path(__file__).resolve().parents[1] / "fakes" / "mcp_server.py"


def fake_spec(**overrides):
    payload = dict(name="fake", description="Fake MCP server for tests.",
                   command=sys.executable, args=["-u", str(SERVER)], timeout_seconds=10)
    payload.update(overrides)
    return MCPServerSpec(**payload)


def test_client_initializes_discovers_and_calls_tools():
    client = StdioMCPClient(fake_spec())
    try:
        info = client.start()
        assert info["serverInfo"]["name"] == "fake-mcp"
        tools = client.list_tools()
        assert [tool["name"] for tool in tools] == ["echo", "add"]
        assert tools[0]["inputSchema"]["required"] == ["message"]
        assert client.call_tool("echo", {"message": "hello"}) == "echo:hello"
        assert client.call_tool("add", {"a": 2, "b": 3}) == "5"
    finally:
        client.close()


def test_client_reports_unknown_method_without_crashing():
    client = StdioMCPClient(fake_spec())
    try:
        client.start()
        with pytest.raises(MCPError):
            client.request("no/such/method", {})
    finally:
        client.close()


def test_client_times_out_and_stays_closed():
    spec = fake_spec(command=sys.executable, args=["-c", "import time; time.sleep(10)"],
                     timeout_seconds=1)
    client = StdioMCPClient(spec)
    with pytest.raises(MCPError):
        client.start()
    client.close()


def test_client_rejects_non_object_tool_schema():
    """服务器返回值不信任：顶层不是对象的 inputSchema 必须拒绝。"""
    script = (
        "import json,sys\n"
        "for line in sys.stdin:\n"
        " m=json.loads(line)\n"
        " if 'id' not in m: continue\n"
        " if m['method']=='initialize':\n"
        "  print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':{'protocolVersion':'2025-06-18'}}),flush=True)\n"
        " elif m['method']=='tools/list':\n"
        "  print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':{'tools':[{'name':'bad','inputSchema':{'type':'string'}}]}}),flush=True)\n"
    )
    spec = fake_spec(args=["-u", "-c", script])
    client = StdioMCPClient(spec)
    try:
        client.start()
        with pytest.raises(MCPError):
            client.list_tools()
    finally:
        client.close()


def initialize_script(body: str) -> str:
    """只处理 initialize 的最小服务器；body 与 ``v=...`` 同缩进（一个空格）。"""
    return (
        "import json,sys\n"
        "for line in sys.stdin:\n"
        " m=json.loads(line)\n"
        " if m.get('method')!='initialize': continue\n"
        " v=m.get('params',{}).get('protocolVersion')\n"
        + body
    )


def test_client_rejects_unknown_protocol_version():
    script = initialize_script(
        " print(json.dumps({'jsonrpc':'2.0','id':m['id'],"
        "'result':{'protocolVersion':'1999-01-01'}}),flush=True)\n"
    )
    client = StdioMCPClient(fake_spec(args=["-u", "-c", script]))
    try:
        with pytest.raises(MCPProtocolVersionError, match="协议版本"):
            client.start()
    finally:
        client.close()


def test_client_falls_back_to_older_version_on_invalid_params():
    script = initialize_script(
        " if v=='2025-06-18':\n"
        "  out={'jsonrpc':'2.0','id':m['id'],'error':"
        "{'code':-32602,'message':'Unsupported protocol version'}}\n"
        " else:\n"
        "  out={'jsonrpc':'2.0','id':m['id'],'result':{'protocolVersion':v}}\n"
        " print(json.dumps(out),flush=True)\n"
    )
    client = StdioMCPClient(fake_spec(args=["-u", "-c", script]))
    try:
        info = client.start()
        assert info["protocolVersion"] == "2024-11-05"
    finally:
        client.close()


def test_client_does_not_fall_back_on_unrelated_rpc_error():
    script = initialize_script(
        " if v=='2025-06-18':\n"
        "  out={'jsonrpc':'2.0','id':m['id'],'error':"
        "{'code':-32601,'message':'method not found'}}\n"
        " else:\n"
        "  out={'jsonrpc':'2.0','id':m['id'],'result':{'protocolVersion':v}}\n"
        " print(json.dumps(out),flush=True)\n"
    )
    client = StdioMCPClient(fake_spec(args=["-u", "-c", script]))
    try:
        with pytest.raises(MCPError, match="method not found"):
            client.start()
    finally:
        client.close()


def test_client_rejects_non_string_protocol_version():
    script = initialize_script(
        " print(json.dumps({'jsonrpc':'2.0','id':m['id'],"
        "'result':{'protocolVersion':2025}}),flush=True)\n"
    )
    client = StdioMCPClient(fake_spec(args=["-u", "-c", script]))
    try:
        with pytest.raises(MCPProtocolVersionError):
            client.start()
    finally:
        client.close()


def test_client_close_reaps_process_and_joins_reader_threads():
    client = StdioMCPClient(fake_spec())
    client.start()
    process = client._process
    threads = list(client._threads)

    client.close()

    assert process.poll() is not None
    assert all(not thread.is_alive() for thread in threads)
    assert not client.healthy
