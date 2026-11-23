"""GOV-001-S3：分级门走完整 HarnessApplication 路径（submit→wait→snapshot）。"""

import json

import pytest

from min_agent.application import HarnessApplication
from min_agent.contracts.models import ModelResponse, ToolCall
from tests.fakes.model import ScriptedModel


@pytest.fixture()
def workspace(tmp_path):
    work = tmp_path / "workspace"
    work.mkdir()
    return work


def _tool_messages(snapshot):
    return [m for m in snapshot.messages if m.role == "tool"]


def test_rule_denied_call_writes_gate_denied_tool_message(workspace, tmp_path):
    model = ScriptedModel([
        ModelResponse(finish_reason="tool_calls", tool_calls=[
            ToolCall(id="c1", name="bash", arguments={"command": "cat ~/.ssh/id_rsa"})]),
        ModelResponse(finish_reason="stop", content="被拒了，换安全方式。"),
    ])
    app = HarnessApplication(tmp_path / "gate-deny.sqlite3", workspace_root=workspace,
                             generic_tools=True, gate_policy="interactive", model=model)
    try:
        session_id = app.create_session("deny")["session_id"]
        app.submit(session_id, "读凭据")
        app.wait(session_id, timeout=30)
        snapshot = app.sessions.snapshot(app.context(session_id))
    finally:
        app.close()
    tool_messages = _tool_messages(snapshot)
    assert any("gate_denied" in (m.content or "") for m in tool_messages)
    audit = app.sessions.extension(app.context(session_id), "gate_audit", [])
    assert any(r["decider"] == "rule" and not r["allowed"] for r in audit)


def test_high_risk_call_stops_waiting_user_with_pending(workspace, tmp_path):
    (workspace / "victim.txt").write_text("x", encoding="utf-8")
    model = ScriptedModel([
        ModelResponse(finish_reason="tool_calls", tool_calls=[
            ToolCall(id="c1", name="bash", arguments={"command": "rm victim.txt"})]),
    ])
    app = HarnessApplication(tmp_path / "gate-high.sqlite3", workspace_root=workspace,
                             generic_tools=True, gate_policy="interactive", model=model)
    try:
        session_id = app.create_session("high")["session_id"]
        app.submit(session_id, "删除文件")
        app.wait(session_id, timeout=30)
        snapshot = app.snapshot(session_id)
    finally:
        app.close()
    assert snapshot["task"]["status"] == "waiting_user"
    assert len(snapshot["gate_confirmations"]) == 1
    assert snapshot["gate_confirmations"][0]["level"] == "high"
    # 文件还没被删（停在确认前）。
    assert (workspace / "victim.txt").exists()


def test_gate_off_by_default_in_generic_app(workspace, tmp_path):
    # 不传 gate_policy：默认 off，rm 仍被 gate-v7 字符串门硬拒（blocked_command）。
    model = ScriptedModel([
        ModelResponse(finish_reason="tool_calls", tool_calls=[
            ToolCall(id="c1", name="bash", arguments={"command": "rm victim.txt"})]),
        ModelResponse(finish_reason="stop", content="好"),
    ])
    (workspace / "victim.txt").write_text("x", encoding="utf-8")
    app = HarnessApplication(tmp_path / "gate-off.sqlite3", workspace_root=workspace,
                             generic_tools=True, model=model)
    try:
        session_id = app.create_session("off")["session_id"]
        app.submit(session_id, "删除")
        app.wait(session_id, timeout=30)
        snapshot = app.sessions.snapshot(app.context(session_id))
    finally:
        app.close()
    assert any("blocked_command" in (m.content or "") for m in _tool_messages(snapshot))
    assert (workspace / "victim.txt").exists()