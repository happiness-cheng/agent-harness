"""GOV-001-S5：ask_user 提问暂停 → 用户回答续跑。"""

import pytest

from min_agent.application import HarnessApplication
from min_agent.contracts.models import ModelResponse, ToolCall
from tests.fakes.model import ScriptedModel


@pytest.fixture()
def workspace(tmp_path):
    work = tmp_path / "workspace"
    work.mkdir()
    return work


def test_ask_user_stops_waiting_user_and_surfaces_question(workspace, tmp_path):
    model = ScriptedModel([
        ModelResponse(finish_reason="tool_calls", tool_calls=[
            ToolCall(id="q1", name="ask_user",
                     arguments={"question": "用哪个目录？", "options": ["a", "b"]})]),
    ])
    app = HarnessApplication(tmp_path / "ask.sqlite3", workspace_root=workspace,
                             generic_tools=True, model=model)
    try:
        session_id = app.create_session("ask")["session_id"]
        app.submit(session_id, "帮我处理")
        app.wait(session_id, timeout=30)
        snapshot = app.snapshot(session_id)
    finally:
        app.close()
    assert snapshot["task"]["status"] == "waiting_user"
    assert snapshot["pending_question"]["question"] == "用哪个目录？"
    assert snapshot["pending_question"]["options"] == ["a", "b"]


def test_ask_user_answer_resumes_and_clears_question(workspace, tmp_path):
    model = ScriptedModel([
        ModelResponse(finish_reason="tool_calls", tool_calls=[
            ToolCall(id="q1", name="ask_user", arguments={"question": "继续吗？"})]),
        ModelResponse(finish_reason="stop", content="好，按你的回答继续。"),
    ])
    app = HarnessApplication(tmp_path / "ask-resume.sqlite3", workspace_root=workspace,
                             generic_tools=True, model=model)
    try:
        session_id = app.create_session("ask")["session_id"]
        app.submit(session_id, "帮我处理")
        app.wait(session_id, timeout=30)
        assert app.snapshot(session_id)["pending_question"] is not None
        task_id = app.snapshot(session_id)["task"]["task_id"]
        app.resume(session_id, task_id=task_id, text="继续")
        app.wait(session_id, timeout=30)
        snapshot = app.snapshot(session_id)
    finally:
        app.close()
    assert snapshot["task"]["status"] == "completed"
    assert snapshot["pending_question"] is None
    # 用户回答进了历史。
    assert any(m.role == "user" and m.content == "继续" for m in
               app.sessions.snapshot(app.context(session_id)).messages)


def test_ask_user_registered_in_generic_mode(workspace, tmp_path):
    app = HarnessApplication(tmp_path / "ask-reg.sqlite3", workspace_root=workspace,
                             generic_tools=True)
    try:
        context = app.context(app.create_session("probe")["session_id"])
        from min_agent.ledger.budget import BudgetLimits
        from min_agent.ledger.budget_persistence import SessionBudgets
        from min_agent.session.events import SessionEvents
        budgets = SessionBudgets(app.sessions)
        budget_id = budgets.create(context, BudgetLimits(model_calls=5, tool_calls=5))
        segment = budgets.open(context, budget_id).start_segment(BudgetLimits(model_calls=5, tool_calls=5))
        registry = app._registry(context, segment, app.model, SessionEvents(app.sessions, context))
        names = [s["name"] for s in registry.schemas()]
    finally:
        app.close()
    assert "ask_user" in names