"""GENERIC-TOOLS-001：四工具模式在生产构造路径上的注册与真实执行。"""

import json
from pathlib import Path

import pytest

from min_agent.application import HarnessApplication
from min_agent.contracts.models import Message, ModelResponse, ToolCall
from min_agent.ledger.budget import BudgetLimits
from min_agent.ledger.budget_persistence import SessionBudgets
from min_agent.runtime import Runtime
from min_agent.runtime.prompt import PromptView
from min_agent.session.events import SessionEvents
from tests.fakes.model import ScriptedModel


@pytest.fixture()
def generic_app(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = HarnessApplication(tmp_path / "generic.sqlite3", workspace_root=workspace,
                             generic_tools=True)
    try:
        yield app
    finally:
        app.close()


def registry_for(app):
    context = app.context(app.create_session("probe")["session_id"])
    budgets = SessionBudgets(app.sessions)
    budget_id = budgets.create(context, BudgetLimits(model_calls=10, tool_calls=10))
    segment = budgets.open(context, budget_id).start_segment(
        BudgetLimits(model_calls=10, tool_calls=10))
    registry = app._registry(context, segment, app.model,
                             SessionEvents(app.sessions, context))
    return registry, context, segment


def test_generic_mode_registers_exactly_four_tools(generic_app):
    registry, _, _ = registry_for(generic_app)

    assert [schema["name"] for schema in registry.schemas()] == [
        "read", "write", "edit", "bash"]


def test_generic_prompt_names_tools_and_real_workspace(generic_app):
    prompt = generic_app._base_prompt()

    assert "read" in prompt and "write" in prompt and "edit" in prompt and "bash" in prompt
    assert str(generic_app.workspace_root) in prompt
    assert "todo_list" not in prompt


def test_generic_mode_runs_write_and_read_through_runtime(generic_app):
    registry, context, segment = registry_for(generic_app)
    model = ScriptedModel([
        ModelResponse(finish_reason="tool_calls", content="写",
                      tool_calls=[ToolCall(id="w", name="write",
                                           arguments={"path": "note.txt",
                                                      "content": "你好\n"})]),
        ModelResponse(finish_reason="tool_calls", content="读",
                      tool_calls=[ToolCall(id="r", name="read",
                                           arguments={"path": "note.txt"})]),
        ModelResponse(finish_reason="stop", content="完成"),
    ])

    result = Runtime(model, registry, budget=segment).run_session(
        "写一个文件再读回来", store=generic_app.sessions, context=context)

    assert result.status == "completed"
    assert (generic_app.workspace_root / "note.txt").read_text(encoding="utf-8") == "你好\n"
    assert [schema["name"] for schema in model.tool_schemas[0]] == [
        "read", "write", "edit", "bash"]
    tool_messages = [message for message in model.calls[-1] if message.role == "tool"]
    assert [json.loads(message.content)["ok"] for message in tool_messages] == [True, True]


def test_generic_mode_runs_full_worker_through_application(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    model = ScriptedModel([
        ModelResponse(finish_reason="tool_calls", content="写",
                      tool_calls=[ToolCall(id="w", name="write",
                                           arguments={"path": "note.txt",
                                                      "content": "hello\n"})]),
        ModelResponse(finish_reason="tool_calls", content="读",
                      tool_calls=[ToolCall(id="r", name="read",
                                           arguments={"path": "note.txt"})]),
        ModelResponse(finish_reason="stop", content="完成"),
    ])
    app = HarnessApplication(tmp_path / "worker.sqlite3", workspace_root=workspace,
                             generic_tools=True, model=model)
    try:
        session_id = app.create_session("worker")["session_id"]
        app.submit(session_id, "写一个文件再读回来")
        app.wait(session_id, timeout=30)
        snapshot = app.sessions.snapshot(app.context(session_id))
    finally:
        app.close()

    assert snapshot.task is not None and snapshot.task.status == "completed"
    assert (workspace / "note.txt").read_text(encoding="utf-8") == "hello\n"
    assert model.calls
    first_request = model.calls[0]
    assert first_request[0].role == "system"
    assert "read" in first_request[0].content and "bash" in first_request[0].content
    assert "todo_list" not in first_request[0].content
    assert [schema["name"] for schema in model.tool_schemas[0]] == [
        "read", "write", "edit", "bash"]


def test_generic_prompt_view_skips_application_state_tail():
    view = PromptView(tools=None, budget=None, system_prompt="SYS", show_state=False)

    messages = view([Message(role="user", content="hi")])

    assert [message.role for message in messages] == ["system", "user"]
    assert messages[0].content == "SYS"
    assert "application_state" not in messages[-1].content


def test_generic_mode_requires_workspace(tmp_path):
    with pytest.raises(ValueError, match="workspace_root"):
        HarnessApplication(tmp_path / "no-workspace.sqlite3", generic_tools=True)


def test_default_mode_does_not_get_generic_tools(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app = HarnessApplication(tmp_path / "default.sqlite3", workspace_root=workspace)
    try:
        registry, _, _ = registry_for(app)
        names = [schema["name"] for schema in registry.schemas()]
    finally:
        app.close()

    assert "todo_list" in names
    assert "read" not in names