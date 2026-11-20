"""SYSVAL-20261109 — S12/S13/S19/S21 硬安全回归（隔离合成数据）。"""
from __future__ import annotations

import json
import sqlite3
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from min_agent.application import HarnessApplication
from min_agent.session.artifacts import ArtifactStore
from min_agent.contracts.models import Message, ModelResponse, ToolCall
from min_agent.session.task_state import ActionSpec, GoalSpec, TaskController, AuthorizationRequired
from min_agent.web.app import create_app

from tests.fakes.model import ScriptedModel


# ---------------------------------------------------------------- S13
def test_s13_cross_owner_and_cross_session_isolation(tmp_path):
    path = tmp_path / "iso.sqlite3"
    owner_a, owner_b = uuid4(), uuid4()
    app_a = HarnessApplication(path, model=ScriptedModel([ModelResponse(finish_reason="stop", content="A")]),
                               owner_id=owner_a)
    app_b = HarnessApplication(path, model=ScriptedModel([ModelResponse(finish_reason="stop", content="B")]),
                               owner_id=owner_b)
    try:
        sid_a = app_a.create_session("A会话")["session_id"]
        sid_a2 = app_a.create_session("A第二会话")["session_id"]
        ctx_a = app_a.context(sid_a)
        # 另一个 owner 不能访问 A 的会话
        with pytest.raises(PermissionError):
            app_b.context(sid_a)
        with pytest.raises(PermissionError):
            app_b.snapshot(sid_a)
        # 同 owner 不同 session 不能读彼此的 Artifact
        artifact = ArtifactStore(app_a.sessions, ctx_a).save("A的私有原文", source="synthetic")
        with pytest.raises(PermissionError):
            ArtifactStore(app_a.sessions, app_a.context(sid_a2)).read(artifact)
        # 伪造 session_id 不能绕过 Host 检查
        forged = str(uuid4())
        with pytest.raises(PermissionError):
            app_a.snapshot(forged)
        # 另一 owner 的 Session 列表中看不到 A
        assert app_b.list_sessions() == []
        assert len(app_a.list_sessions()) == 2
    finally:
        app_a.close()
        app_b.close()


def test_s13_forged_owner_in_tool_arguments_cannot_bypass(tmp_path):
    """read_session_state 不接受 session_id；多余字段必须被 Schema 拒绝。"""
    model = ScriptedModel([
        ModelResponse(finish_reason="tool_calls", tool_calls=[
            ToolCall(id="c1", name="read_session_state",
                     arguments={"session_id": str(uuid4()), "owner_id": str(uuid4())})]),
        ModelResponse(finish_reason="stop", content="无法读取"),
    ])
    harness = HarnessApplication(tmp_path / "forge.sqlite3", model=model)
    try:
        sid = harness.create_session()["session_id"]
        harness.submit(sid, "读取别的会话状态")
        harness.wait(sid)
        snap = harness.snapshot(sid)
        tool = [json.loads(m["content"]) for m in snap["messages"] if m["role"] == "tool"]
        assert tool and tool[0]["ok"] is False
        assert tool[0]["error"]["code"] == "invalid_arguments"
    finally:
        harness.close()


# ---------------------------------------------------------------- S12
def _seed_message(harness, context, text):
    controller = TaskController(harness.sessions)
    proposal = controller.propose(context, GoalSpec(
        objective="确认记忆", completion_criteria=("审核候选",)))
    controller.confirm(context, proposal.proposal_id)
    controller.select_mode(context, "build")
    task = harness.sessions.begin(context)
    harness.sessions.append(context, task.task_id, [Message(role="user", content=text)])
    harness.sessions.finish(context, task.task_id, status="completed")


def test_s12_deleted_memory_is_not_retrievable_or_delivered(tmp_path):
    class Capture:
        def __init__(self):
            self.requests = []

        def generate(self, messages, *, tools=None):
            self.requests.append(messages)
            return ModelResponse(finish_reason="stop", content="回答")

    model = Capture()
    harness = HarnessApplication(tmp_path / "mem.sqlite3", model=model)
    try:
        sid = harness.create_session()["session_id"]
        context = harness.context(sid)
        _seed_message(harness, context, "记住：我的代号是 ZEBRA-7788。")
        item = harness.memory.remember(context, source_index=0)
        assert item.id in harness.memory.document(context).memories
        harness.memory.delete(context, item.id)
        assert item.id not in harness.memory.document(context).memories
        second = harness.create_session()["session_id"]
        harness.submit(second, "请复述我的代号。")
        harness.wait(second)
        delivered = [r for r in model.requests
                     if any("untrusted_user_memory" in m.content for m in r)]
        assert delivered == [], "删除后不得再向模型送达该记忆"
        blob = json.dumps([[m.content for m in r] for r in model.requests], ensure_ascii=False)
        assert "ZEBRA-7788" not in blob.replace("请复述我的代号。", "")
    finally:
        harness.close()


# ---------------------------------------------------------------- S19
def test_s19_duplicate_action_and_resume_do_not_double_execute(tmp_path):
    """同一已确认动作只允许消费一次；重复 resume 被拒绝；库无重复行。"""
    from min_agent.tools.todo import TodoService
    from min_agent.tools.todo_tools import todo_registry

    model = ScriptedModel([ModelResponse(finish_reason="stop", content="ok")])
    harness = HarnessApplication(tmp_path / "dup.sqlite3", model=model)
    try:
        sid = harness.create_session()["session_id"]
        context = harness.context(sid)
        action = ActionSpec(operation="todo.add",
                            parameters={"title": "唯一动作", "due_date": None})
        proposal = harness.propose(context, {
            "objective": "记录", "completion_criteria": ["完成"],
            "actions": [action.model_dump()]})
        harness.confirm(sid, proposal.proposal_id)
        harness.set_mode(sid, "build")
        task = harness.sessions.begin(context)
        registry = todo_registry(TodoService(harness.sessions),
                                 TaskController(harness.sessions), context)
        call = ToolCall(id="a1", name="todo_add",
                        arguments={"title": "唯一动作", "due_date": None})
        first = registry.execute(call, operation_id=str(uuid4()))
        assert first["ok"] is True
        with pytest.raises((AuthorizationRequired, RuntimeError)):
            registry.execute(ToolCall(id="a2", name="todo_add",
                                      arguments={"title": "唯一动作", "due_date": None}),
                             operation_id=str(uuid4()))
        items = TodoService(harness.sessions).list_items(context)
        assert len(items) == 1, items
        harness.sessions.finish(context, task.task_id, status="completed")
        # 重复 resume 在运行中必须被拒绝
        harness.submit(sid, "再来一次")
        harness.wait(sid)
        with pytest.raises((PermissionError, RuntimeError)):
            harness.resume(sid, task_id=str(task.task_id))
        with sqlite3.connect(tmp_path / "dup.sqlite3") as conn:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        harness.close()


# ---------------------------------------------------------------- S21
def test_s21_web_boundary_rejects_and_still_allows_legitimate(tmp_path):
    harness = HarnessApplication(tmp_path / "web.sqlite3",
                                 model=ScriptedModel([ModelResponse(finish_reason="stop", content="合法答复")]))
    try:
        client = TestClient(create_app(harness), base_url="http://127.0.0.1")
        # 缺 Cookie
        assert client.get("/api/sessions").status_code == 401
        csrf = client.get("/api/bootstrap").json()["csrf"]
        headers = {"x-harness-csrf": csrf}
        # 缺 CSRF
        assert client.post("/api/sessions", json={"label": "x"}).status_code == 403
        # 错误 Origin
        assert client.post("/api/sessions", json={"label": "x"},
                           headers={**headers, "Origin": "https://evil.example"}).status_code == 403
        # 非法 body（未知字段 + 超长）
        assert client.post("/api/sessions", json={"label": "x", "owner_id": "evil"},
                           headers=headers).status_code in {400, 422}
        # 伪造会话 ID
        assert client.get(f"/api/sessions/{uuid4()}").status_code in {403, 404}
        # 合法请求必须成功
        created = client.post("/api/sessions", json={"label": "合法"}, headers=headers)
        assert created.status_code == 201
        sid = created.json()["session_id"]
        assert client.post(f"/api/sessions/{sid}/input", json={"text": "你好"},
                           headers=headers).status_code == 202
        harness.wait(sid)
        saved = client.get(f"/api/sessions/{sid}").json()
        assert saved["messages"][-1]["content"] == "合法答复"
        # 错误 Host 被 TrustedHost 拒绝
        bad_host = TestClient(create_app(harness), base_url="http://evil.example")
        assert bad_host.get("/api/bootstrap").status_code == 400
    finally:
        harness.close()
