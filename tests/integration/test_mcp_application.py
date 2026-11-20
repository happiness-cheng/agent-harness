"""应用层接线：后台默认连接、元数据只进 SystemPrompt、confirm 真排队与回填。"""

import json
import sys
import time
from pathlib import Path

from min_agent.application import HarnessApplication
from min_agent.contracts.models import ModelResponse, ToolCall
from min_agent.mcp.spec import MCPServerSpec
from tests.fakes.model import ScriptedModel

SERVER = Path(__file__).resolve().parents[1] / "fakes" / "mcp_server.py"


def fake_spec(**overrides):
    payload = dict(name="fake", description="Fake MCP server for tests.",
                   command=sys.executable, args=["-u", str(SERVER)], timeout_seconds=10)
    payload.update(overrides)
    return MCPServerSpec(**payload)


def wait_connected(app, identity, name="fake", timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = app.snapshot(identity)["mcp"].get(name, {})
        if status.get("state") == "connected":
            return
        time.sleep(0.05)
    raise AssertionError(f"MCP 服务器 {name} 未在 {timeout}s 内后台连接成功")


def echo_call(call_id="c1", message="hello"):
    return ModelResponse(finish_reason="tool_calls", tool_calls=[
        ToolCall(id=call_id, name="mcp__fake__echo", arguments={"message": message}),
    ])


def test_application_connects_in_background_and_discloses_metadata_only(tmp_path):
    model = ScriptedModel([echo_call(), ModelResponse(finish_reason="stop", content="完成")])
    app = HarnessApplication(tmp_path / "mcp.sqlite3", model=model, mcp_servers=(fake_spec(),))
    try:
        identity = app.create_session()["session_id"]
        wait_connected(app, identity)
        app.submit(identity, "用 fake 回显 hello")
        app.wait(identity, timeout=30)
        saved = app.snapshot(identity)
    finally:
        app.close()

    assert saved["task"]["status"] == "completed", saved["task"].get("error_code")
    # MCP 工具从不进入模型 tools 数组；元数据只在 SystemPrompt 段。
    for schemas in model.tool_schemas:
        assert not any(schema["name"].startswith("mcp__") for schema in schemas)
    system = model.calls[0][0].content
    assert "mcp__fake__echo" in system and "Echo the message back." in system
    assert sys.executable not in system
    tool_messages = [message for message in saved["messages"] if message["role"] == "tool"]
    assert any("echo:hello" in message["content"] for message in tool_messages)
    assert saved["mcp"]["fake"]["state"] == "connected"


def test_confirm_policy_suspends_then_approve_resumes_and_executes(tmp_path):
    model = ScriptedModel([
        echo_call("c1"), echo_call("c2"), ModelResponse(finish_reason="stop", content="完成"),
    ])
    app = HarnessApplication(tmp_path / "mcp.sqlite3", model=model,
                             mcp_servers=(fake_spec(policy="confirm"),))
    try:
        identity = app.create_session()["session_id"]
        wait_connected(app, identity)
        app.submit(identity, "用 fake 回显 hello")
        app.wait(identity, timeout=30)
        suspended = app.snapshot(identity)
        assert suspended["task"]["status"] == "waiting_user"
        # 挂起时未执行：没有 echo 结果，只有一条 pending 记录。
        assert not any("echo:hello" in message["content"]
                       for message in suspended["messages"] if message["role"] == "tool")
        pending = suspended["mcp_confirmations"]
        assert len(pending) == 1 and pending[0]["status"] == "pending"
        assert pending[0]["prefixed"] == "mcp__fake__echo"
        assert pending[0]["arguments"] == {"message": "hello"}

        app.mcp_confirm(identity, pending[0]["pending_id"], "approve")
        app.wait(identity, timeout=30)
        saved = app.snapshot(identity)
    finally:
        app.close()

    assert saved["task"]["status"] == "completed", saved["task"].get("error_code")
    tool_messages = [message for message in saved["messages"] if message["role"] == "tool"]
    assert any(json.loads(message["content"]) == {"ok": True, "result": "echo:hello"}
               for message in tool_messages)
    assert saved["mcp_confirmations"] == []  # 批准已消费，不再 pending


def test_confirm_policy_refusal_returns_explicit_error_not_ok(tmp_path):
    model = ScriptedModel([
        echo_call("c1"), echo_call("c2"), ModelResponse(finish_reason="stop", content="已放弃"),
    ])
    app = HarnessApplication(tmp_path / "mcp.sqlite3", model=model,
                             mcp_servers=(fake_spec(policy="confirm"),))
    try:
        identity = app.create_session()["session_id"]
        wait_connected(app, identity)
        app.submit(identity, "用 fake 回显 hello")
        app.wait(identity, timeout=30)
        pending = app.snapshot(identity)["mcp_confirmations"]
        assert len(pending) == 1

        app.mcp_confirm(identity, pending[0]["pending_id"], "refuse")
        app.wait(identity, timeout=30)
        saved = app.snapshot(identity)
    finally:
        app.close()

    # 拒绝按请求错误回填，本轮以 partial/tool_request_failed 收尾（既有运行时语义）。
    assert saved["task"]["status"] == "partial"
    assert saved["task"]["error_code"] == "tool_request_failed"
    assert not any("echo:hello" in message["content"] for message in saved["messages"])
    tool_messages = [message for message in saved["messages"] if message["role"] == "tool"]
    refused = [json.loads(message["content"]) for message in tool_messages
               if not json.loads(message["content"])["ok"]]
    assert refused, "拒绝必须显式回填 ok:false，不能包成 ok:true"
    assert refused[-1]["error"]["code"] == "tool_request_failed"
    assert refused[-1]["error"]["refused_by_user"] is True
    assert saved["mcp_confirmations"] == []


def test_web_mcp_confirm_endpoint_requires_csrf_and_approves_pending(tmp_path):
    from fastapi.testclient import TestClient
    from min_agent.web.app import create_app

    model = ScriptedModel([
        echo_call("c1"), echo_call("c2"), ModelResponse(finish_reason="stop", content="完成"),
    ])
    app = HarnessApplication(tmp_path / "mcp-web.sqlite3", model=model,
                             mcp_servers=(fake_spec(policy="confirm"),))
    try:
        client = TestClient(create_app(app), base_url="http://127.0.0.1")
        headers = {"X-Harness-CSRF": client.get("/api/bootstrap").json()["csrf"]}
        identity = client.post("/api/sessions", json={"label": "mcp-web"},
                               headers=headers).json()["session_id"]
        wait_connected(app, identity)
        assert client.post(f"/api/sessions/{identity}/input",
                           json={"text": "回显 hello"}, headers=headers).status_code == 202
        app.wait(identity, timeout=30)
        pending = client.get(f"/api/sessions/{identity}").json()["mcp_confirmations"]
        assert len(pending) == 1
        payload = {"pending_id": pending[0]["pending_id"], "decision": "approve"}
        # 无 CSRF 拒绝；非法 decision 被请求模型拒绝。
        assert client.post(f"/api/sessions/{identity}/mcp-confirm",
                           json=payload).status_code == 403
        assert client.post(f"/api/sessions/{identity}/mcp-confirm",
                           json={**payload, "decision": "maybe"},
                           headers=headers).status_code == 422
        decided = client.post(f"/api/sessions/{identity}/mcp-confirm",
                              json=payload, headers=headers)
        assert decided.status_code == 200
        assert decided.json()["status"] == "approved"
        app.wait(identity, timeout=30)
        saved = client.get(f"/api/sessions/{identity}").json()
    finally:
        app.close()

    assert saved["task"]["status"] == "completed", saved["task"].get("error_code")
    assert any("echo:hello" in message["content"]
               for message in saved["messages"] if message["role"] == "tool")
    assert saved["mcp_confirmations"] == []
