"""B1：真实应用入口的公开 Todo 目标登记、冻结、观察、反馈与评分。"""
import sqlite3
from pathlib import Path

import pytest

from min_agent.application import HarnessApplication
from min_agent.models import ModelResponse, ToolCall
from min_agent.public_feedback import PublicGoal, PublicGoalSpec


class Scripted:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def generate(self, messages, *, tools=None):
        self.calls += 1
        return self.responses.pop(0)


def response_tool(title="第一项"):
    return ModelResponse(finish_reason="tool_calls",
                         tool_calls=[ToolCall(id="call-1", name="todo_add",
                                              arguments={"title": title, "due_date": None})])


def response_stop():
    return ModelResponse(finish_reason="stop", content="已完成")


def goal(expected=1, goal_id="goal-001"):
    return PublicGoal(public_goal_id=goal_id, kind="TODO_COUNT", source_type="USER_REQUEST",
                      source_ref="user-confirmed-goal-1", expected_count=expected)


def app_with_goal(tmp_path, model, expected=1):
    app = HarnessApplication(tmp_path / "app.sqlite3", model=model, memory_mode="off",
                             public_goals_enabled=True)
    sid = app.create_session()["session_id"]
    context = app.context(sid)
    proposal = app.propose(context, {
        "objective": "记录一条合成待办", "constraints": [],
        "completion_criteria": ["记录一条合成待办"],
        "actions": [{"operation": "todo.add",
                     "parameters": {"title": "第一项", "due_date": None}}],
    })
    app.confirm(sid, proposal.proposal_id)
    app.set_mode(sid, "build")
    app.register_public_goals(sid, proposal.proposal_id, [goal(expected)])
    return app, sid, context, proposal


def test_real_application_goal_freeze_todo_feedback_and_score(tmp_path):
    app = None
    try:
        app, sid, context, proposal = app_with_goal(
            tmp_path, Scripted([response_tool(), response_stop()]))
        app.submit(sid, "请记录")
        app.wait(sid)
        binding = app.public_goal_registry.current_binding(context)
        assert binding is not None
        assert binding.proposal_id == str(proposal.proposal_id)
        assert binding.spec.scope.task_id == binding.attempt_id
        feedback = app.public_feedback(sid)
        assert feedback.feedback_status == "READY", feedback
        assert feedback.public_task_status == "MET", feedback
        assert feedback.goal_progress.met == 1 and feedback.goal_progress.total == 1
        assert feedback.execution_authorization == "NOT_GRANTED"
        score = app.public_score(sid)
        assert score.score_status == "AVAILABLE", score
        assert score.progress.met == 1 and score.progress.lower_numerator == 100
        assert score.execution_authorization == "NOT_GRANTED"
        assert app.snapshot(sid)["todos"][0]["item"]["title"] == "第一项"
    finally:
        if app is not None:
            app.close()


def test_fake_success_without_todo_is_not_met(tmp_path):
    app = None
    try:
        app, sid, context, proposal = app_with_goal(
            tmp_path, Scripted([response_stop()]))
        app.submit(sid, "请记录")
        app.wait(sid)
        feedback = app.public_feedback(sid)
        assert feedback.feedback_status == "READY", feedback
        assert feedback.public_task_status == "NOT_MET", feedback
        assert feedback.goal_progress.not_met == 1
        score = app.public_score(sid)
        assert score.score_status == "AVAILABLE" and score.progress.not_met == 1
        assert not any(value in str(feedback.model_dump(mode="json"))
                       for value in (str(context.owner_id), str(context.session_id)))
    finally:
        if app is not None:
            app.close()


def test_exact_two_goal_with_one_record_is_not_met_not_fifty_percent(tmp_path):
    app = None
    try:
        app, sid, context, proposal = app_with_goal(
            tmp_path, Scripted([response_tool(), response_stop()]), expected=2)
        app.submit(sid, "请记录两条")
        app.wait(sid)
        feedback = app.public_feedback(sid)
        assert feedback.public_task_status == "NOT_MET"
        score = app.public_score(sid)
        assert score.progress.not_met == 1 and score.progress.total == 1
        assert score.progress.lower_numerator == 0 and score.progress.upper_numerator == 0
    finally:
        if app is not None:
            app.close()


def test_legal_resume_creates_new_binding_and_keeps_met(tmp_path):
    app = None
    try:
        app = HarnessApplication(tmp_path / "resume.sqlite3",
                                 model=Scripted([response_tool(), response_tool(), response_stop()]),
                                 memory_mode="off", public_goals_enabled=True)
        sid = app.create_session()["session_id"]
        context = app.context(sid)
        proposal = app.propose(context, {
            "objective": "记录一条合成待办", "constraints": [],
            "completion_criteria": ["记录一条合成待办"],
            "actions": [{"operation": "todo.add",
                         "parameters": {"title": "第一项", "due_date": None}}],
        })
        app.confirm(sid, proposal.proposal_id)
        app.set_mode(sid, "plan")
        app.register_public_goals(sid, proposal.proposal_id, [goal(1)])
        app.submit(sid, "计划阶段")
        app.wait(sid)
        paused = app.sessions.snapshot(context).task
        assert paused.status == "waiting_user"
        first = app.public_goal_registry.current_binding(context)
        app.set_mode(sid, "build")
        app.register_public_goals(sid, proposal.proposal_id, [goal(1)])
        app.resume(sid, task_id=str(paused.task_id))
        app.wait(sid)
        second = app.public_goal_registry.current_binding(context)
        assert first.binding_id != second.binding_id
        assert second.attempt_id != first.attempt_id
        assert app.public_feedback(sid).public_task_status == "MET"
    finally:
        if app is not None:
            app.close()


def test_enabled_without_freeze_returns_withheld_not_met(tmp_path):
    app = HarnessApplication(tmp_path / "no-goal.sqlite3", model=Scripted([response_stop()]),
                             memory_mode="off", public_goals_enabled=True)
    try:
        sid = app.create_session()["session_id"]
        app.submit(sid, "没有公开目标")
        app.wait(sid)
        feedback = app.public_feedback(sid)
        assert feedback.feedback_status == "WITHHELD"
        assert feedback.goal_progress is None
        assert app.public_score(sid).score_status == "WITHHELD"
    finally:
        app.close()


def test_public_goals_default_off_does_not_change_normal_run(tmp_path):
    app = HarnessApplication(tmp_path / "off.sqlite3", model=Scripted([response_stop()]),
                             memory_mode="off")
    try:
        sid = app.create_session()["session_id"]
        app.submit(sid, "普通聊天")
        app.wait(sid)
        assert app.public_feedback(sid) is None
        assert app.public_score(sid) is None
        assert "public_goal_request" not in app.sessions.snapshot(app.context(sid)).extensions
    finally:
        app.close()
