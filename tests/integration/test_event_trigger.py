"""GOV-001-S8：事件触发（预授权 + 独立预算 + 审计）。"""

from datetime import UTC, datetime, timedelta

import pytest

from min_agent.application import HarnessApplication
from min_agent.contracts.models import ModelResponse, ToolCall
from min_agent.tools.gate import ToolGate
from tests.fakes.model import ScriptedModel


@pytest.fixture()
def env(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = HarnessApplication(tmp_path / "event.sqlite3", workspace_root=workspace,
                             generic_tools=True, gate_policy="interactive")
    context = app.context(app.create_session("event")["session_id"])
    app.sessions.begin(context)
    try:
        yield app, context, workspace
    finally:
        app.close()


def call(name, **arguments):
    return ToolCall(id=f"call-{name}", name=name, arguments=arguments)


def test_unattended_preauth_match_allows_medium(env):
    app, context, workspace = env
    gate = ToolGate(app.sessions, context, workspace, policy="unattended",
                    preauth=[{"tool": "bash", "command": "python script.py"}])
    decision = gate.check(call("bash", command="python script.py"))
    assert decision.allowed and decision.decider == "preauth"


def test_unattended_preauth_mismatch_denies_medium(env):
    app, context, workspace = env
    gate = ToolGate(app.sessions, context, workspace, policy="unattended",
                    preauth=[{"tool": "bash", "command": "python other.py"}])
    decision = gate.check(call("bash", command="python script.py"))
    assert not decision.allowed


def test_unattended_high_denied_even_with_preauth(env):
    app, context, workspace = env
    gate = ToolGate(app.sessions, context, workspace, policy="unattended",
                    preauth=[{"tool": "bash", "command": "rm a.txt"}])
    decision = gate.check(call("bash", command="rm a.txt"))
    assert not decision.allowed  # 高危一律拒


def test_schedule_and_tick_fires_due_event(env):
    app, context, workspace = env
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    event = app.schedule_event(str(context.session_id), "跑一下", past,
                               preauth=[{"tool": "bash", "command": "ls"}])
    assert event["status"] == "pending"
    fired = app.tick_events()
    assert event["event_id"] in fired
    events = app.sessions.extension(context, "scheduled_events", {})
    assert events[event["event_id"]]["status"] == "fired"
    audit = app.sessions.extension(context, "gate_audit", [])
    assert any(r["decider"] == "scheduler" and r.get("event_id") == event["event_id"]
               for r in audit)


def test_tick_does_not_fire_future_event(env):
    app, context, workspace = env
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    app.schedule_event(str(context.session_id), "以后跑", future)
    assert app.tick_events() == []


def test_event_run_uses_unattended_and_budget(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # 模型调一次中风险 bash（无预授权）→ 无人值守拒绝；再 stop。
    model = ScriptedModel([
        ModelResponse(finish_reason="tool_calls", tool_calls=[
            ToolCall(id="c1", name="bash", arguments={"command": "python script.py"})]),
        ModelResponse(finish_reason="stop", content="好"),
    ])
    app = HarnessApplication(tmp_path / "event-run.sqlite3", workspace_root=workspace,
                             generic_tools=True, gate_policy="interactive", model=model,
                             total_models=40)
    try:
        session_id = app.create_session("ev")["session_id"]
        past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        app.schedule_event(session_id, "跑脚本", past, max_models=5)  # 无预授权
        app.tick_events()
        app.wait(session_id, timeout=30)
        context = app.context(session_id)
        snapshot = app.sessions.snapshot(context)
        audit = app.sessions.extension(context, "gate_audit", [])
    finally:
        app.close()
    # 中风险无预授权 → 无人值守拒绝（decider rule）。
    assert any(r["decider"] == "rule" and not r["allowed"] and r["level"] == "medium"
               for r in audit)
    assert any("gate_denied" in (m.content or "") for m in snapshot.messages if m.role == "tool")