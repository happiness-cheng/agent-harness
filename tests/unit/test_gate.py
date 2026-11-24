"""执行前分级门（gate.ToolGate）的离线判据：规则→小模型→人，fail-closed，审计。"""

import json
from pathlib import Path

import pytest

from min_agent.application import HarnessApplication
from min_agent.contracts.models import Message, ModelResponse, ToolCall
from min_agent.session.task_state import AuthorizationRequired
from min_agent.tools.gate import GateConfirmations, ToolGate, redact_command


@pytest.fixture()
def env(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = HarnessApplication(tmp_path / "gate.sqlite3", workspace_root=workspace,
                             generic_tools=True)
    context = app.context(app.create_session("gate")["session_id"])
    app.sessions.begin(context)  # 活动任务，确认排队需要逻辑任务
    try:
        yield app, context, workspace
    finally:
        app.close()


def call(name, **arguments):
    return ToolCall(id=f"call-{name}", name=name, arguments=arguments)


class StubApproval:
    """按预设返回审批 JSON；可设失败。"""

    def __init__(self, decision="allow", reason="ok", fail=False):
        self.decision = decision
        self.reason = reason
        self.fail = fail
        self.calls = 0

    def generate(self, messages, *, tools=None):
        self.calls += 1
        if self.fail:
            raise RuntimeError("model down")
        return ModelResponse(finish_reason="stop", content=json.dumps(
            {"decision": self.decision, "reason": self.reason}))


def gate_for(env, policy="interactive", approval=None):
    app, context, workspace = env
    from min_agent.tools.generic import GenericWorkspace
    ws = GenericWorkspace(workspace)
    return ToolGate(app.sessions, context, workspace, policy=policy,
                    approval_model=approval, workspace=ws), ws


def test_low_risk_read_allowed(env):
    gate, _ = gate_for(env)
    decision = gate.check(call("read", path="a.txt"))
    assert decision.allowed and decision.level == "low" and decision.decider == "rule"


def test_rule_fast_fail_credential_denied(env):
    gate, _ = gate_for(env)
    decision = gate.check(call("bash", command="cat ~/.ssh/id_rsa"))
    assert not decision.allowed and decision.decider == "rule"


def test_rule_fast_fail_path_escape_denied(env):
    gate, _ = gate_for(env)
    decision = gate.check(call("bash", command="cat /etc/passwd"))
    assert not decision.allowed and decision.decider == "rule"


def test_rule_fast_fail_parse_error_denied(env):
    gate, _ = gate_for(env)
    decision = gate.check(call("bash", command="echo 'unclosed"))
    assert not decision.allowed and decision.decider == "rule"


def test_medium_small_model_allow(env):
    approval = StubApproval(decision="allow")
    gate, _ = gate_for(env, approval=approval)
    decision = gate.check(call("bash", command="python script.py"))
    assert decision.allowed and decision.decider == "small_model"
    assert approval.calls == 1


def test_medium_small_model_deny(env):
    gate, _ = gate_for(env, approval=StubApproval(decision="deny", reason="太危险"))
    decision = gate.check(call("bash", command="python script.py"))
    assert not decision.allowed and decision.decider == "small_model"
    assert "太危险" in decision.reason


def test_medium_model_failure_escalates_to_human(env):
    gate, _ = gate_for(env, approval=StubApproval(fail=True))
    with pytest.raises(AuthorizationRequired):
        gate.check(call("bash", command="python script.py"))
    # 升级人工后应有待确认记录。
    assert len(gate.pending()) == 1


def test_medium_no_model_escalates_to_human(env):
    gate, _ = gate_for(env, approval=None)
    with pytest.raises(AuthorizationRequired):
        gate.check(call("bash", command="python script.py"))


def test_circuit_breaker_opens_after_three_failures(env):
    gate, _ = gate_for(env, approval=StubApproval(fail=True))
    for _ in range(3):
        with pytest.raises(AuthorizationRequired):
            gate.check(call("bash", command="python script.py"))
    assert gate._breaker_open is True
    # 熔断后即使模型恢复，也直接升级人工，不再调模型。
    approval = StubApproval(decision="allow")
    gate._approval_model = approval
    with pytest.raises(AuthorizationRequired):
        gate.check(call("bash", command="python script.py"))
    assert approval.calls == 0


def test_high_risk_rm_requires_human_then_approve_clears(env):
    app, context, workspace = env
    gate, ws = gate_for(env)
    (workspace / "victim.txt").write_text("x", encoding="utf-8")
    command = "rm victim.txt"
    with pytest.raises(AuthorizationRequired):
        gate.check(call("bash", command=command))
    pending = gate.pending()
    assert len(pending) == 1 and pending[0]["level"] == "high"
    # 用户批准。
    GateConfirmations(app.sessions, context).decide(
        __import__("uuid").UUID(pending[0]["pending_id"]), True)
    decision = gate.check(call("bash", command=command))
    assert decision.allowed and decision.decider == "user"
    assert decision.clear_destructive is True
    assert command in ws.cleared_commands
    # bash 取出放行标记后真的执行删除。
    result = json.loads(ws.bash(command))
    assert result["ok"] is True
    assert not (workspace / "victim.txt").exists()
    assert command not in ws.cleared_commands  # 一次性


def test_high_risk_refused_denies(env):
    app, context, workspace = env
    gate, _ = gate_for(env)
    command = "rm victim.txt"
    with pytest.raises(AuthorizationRequired):
        gate.check(call("bash", command=command))
    pending = gate.pending()[0]
    GateConfirmations(app.sessions, context).decide(
        __import__("uuid").UUID(pending["pending_id"]), False)
    decision = gate.check(call("bash", command=command))
    assert not decision.allowed and decision.decider == "user"


def test_high_risk_git_push_requires_human(env):
    gate, _ = gate_for(env)
    with pytest.raises(AuthorizationRequired):
        gate.check(call("bash", command="git push origin main"))
    assert gate.pending()[0]["level"] == "high"


def test_unattended_medium_and_high_denied(env):
    gate, _ = gate_for(env, policy="unattended", approval=StubApproval())
    assert not gate.check(call("bash", command="python script.py")).allowed
    assert not gate.check(call("bash", command="rm a.txt")).allowed


def test_off_policy_allows_everything(env):
    gate, _ = gate_for(env, policy="off")
    assert gate.check(call("bash", command="python script.py")).allowed
    # 规则快速失败仍生效（off 只跳过分级，不跳过禁令）。
    assert not gate.check(call("bash", command="cat ~/.ssh/id_rsa")).allowed


def test_audit_written_and_redacted(env):
    app, context, _ = env
    gate, _ = gate_for(env, approval=StubApproval())
    gate.check(call("bash", command="echo API_KEY=sk-supersecretvalue123"))
    audit = app.sessions.extension(context, "gate_audit", [])
    assert len(audit) == 1
    record = audit[0]
    assert record["tool"] == "bash"
    assert "command_sha256" in record and len(record["command_sha256"]) == 64
    assert "sk-supersecretvalue123" not in record["command_redacted"]


def test_redact_command_masks_tokens_and_truncates():
    text = redact_command("export TOKEN=ghp_abcdefghijklmnop1234")
    assert "ghp_abcdefghijklmnop1234" not in text
    long = redact_command("x" * 5000)
    assert len(long) < 5000 and long.endswith("...[truncated]")