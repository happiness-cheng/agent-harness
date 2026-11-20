"""WIRING-003：会话结束自动提出记忆候选，确认前不入库、不注入。"""

import json

from fastapi.testclient import TestClient

from min_agent.application import HarnessApplication
from min_agent.contracts.models import ModelResponse, ToolCall
from min_agent.web.app import create_app


class StopModel:
    def __init__(self, content="好的。"):
        self.content = content
        self.requests = []

    def generate(self, messages, *, tools=None):
        self.requests.append(messages)
        return ModelResponse(finish_reason="stop", content=self.content)


class RecallModel:
    """第二轮按真实工具序列检索已确认记忆，并从动态投影里读出正文。"""

    def __init__(self):
        self.requests = []

    def generate(self, messages, *, tools=None):
        self.requests.append([(m.role, m.content) for m in messages])
        users = [m.content for m in messages
                 if m.role == "user" and not m.content.startswith("{")]
        if users and users[-1].startswith("记住"):
            return ModelResponse(finish_reason="stop", content="已登记待确认候选。")
        tool_messages = [m for m in messages if m.role == "tool"]
        if not tool_messages:
            return ModelResponse(finish_reason="tool_calls", tool_calls=[ToolCall(
                id="search-1", name="search_long_memory",
                arguments={"question": "我的 Python 代码示例偏好"})])
        if len(tool_messages) == 1:
            ids = json.loads(json.loads(tool_messages[-1].content)["result"])["memory_ids"]
            return ModelResponse(finish_reason="tool_calls", tool_calls=[ToolCall(
                id="read-1", name="read_long_memory_evidence",
                arguments={"memory_ids": ids})])
        view = json.loads(messages[-1].content)["untrusted_user_memory"]
        entry = view["evidence"][0]
        return ModelResponse(
            finish_reason="stop",
            content="你的偏好是：" + entry["quotes"][0]["text"]
                    + " [memory:" + entry["source"] + "]")


def test_remember_directive_becomes_pending_candidate_and_confirm_via_api(tmp_path):
    model = StopModel()
    app = HarnessApplication(tmp_path / "mem.sqlite3", model=model, memory_mode="active")
    try:
        session = app.create_session(mode="build")["session_id"]
        app.submit(session, "记住：讲代码时先给 Python 短例子。")
        app.wait(session, timeout=30)
        saved = app.snapshot(session)
        assert saved["task"]["status"] == "completed", saved["task"].get("error_code")
        assert len(saved["long_memory"]["candidates"]) == 1
        assert saved["long_memory"]["candidates"][0]["content"] == "讲代码时先给 Python 短例子。"
        # 确认前不进入正式库，也不改变任务状态。
        assert app.memory.document(app.context(session)).memories == {}

        with TestClient(create_app(app), base_url="http://127.0.0.1") as client:
            headers = {"X-Harness-CSRF": client.get("/api/bootstrap").json()["csrf"]}
            review = client.get(f"/api/sessions/{session}/memory/candidates/"
                                f"{saved['long_memory']['candidates'][0]['id']}")
            assert review.status_code == 200
            token = review.json()["review_token"]
            confirmed = client.post(f"/api/sessions/{session}/memory/confirm",
                                    json={"candidate_id": saved["long_memory"]["candidates"][0]["id"],
                                          "review_token": token}, headers=headers)
            assert confirmed.status_code == 200
            # TestClient 退出时 lifespan 会关闭 harness，所以断言放在 with 内。
            assert len(app.memory.document(app.context(session)).memories) == 1
            assert app.snapshot(session)["long_memory"]["candidates"] == []
    finally:
        app.close()


def test_auto_proposal_ignores_assistant_text_and_plain_user_messages(tmp_path):
    model = StopModel("我记住了：这只是助手说的话。")
    app = HarnessApplication(tmp_path / "mem.sqlite3", model=model, memory_mode="active")
    try:
        session = app.create_session(mode="build")["session_id"]
        app.submit(session, "你好。")
        app.wait(session, timeout=30)
        saved = app.snapshot(session)
        assert saved["task"]["status"] == "completed"
        assert saved["long_memory"]["candidates"] == []
        assert app.memory.document(app.context(session)).memories == {}
    finally:
        app.close()


def test_memory_off_never_auto_proposes(tmp_path):
    app = HarnessApplication(tmp_path / "mem.sqlite3", model=StopModel(), memory_mode="off")
    try:
        session = app.create_session(mode="build")["session_id"]
        app.submit(session, "记住：这条不应进入候选。")
        app.wait(session, timeout=30)
        assert app.snapshot(session)["long_memory"]["candidates"] == []
        assert app.memory.list_pending(app.context(session)) == []
    finally:
        app.close()


def test_confirmed_memory_is_injected_into_next_real_request(tmp_path):
    model = RecallModel()
    app = HarnessApplication(tmp_path / "mem.sqlite3", model=model, memory_mode="active")
    try:
        session = app.create_session(mode="build")["session_id"]
        app.submit(session, "记住：讲代码时先给 Python 短例子。")
        app.wait(session, timeout=30)
        context = app.context(session)
        pending = app.memory.list_pending(context)
        assert len(pending) == 1
        review = app.memory.review(context, pending[0].id)
        app.memory.confirm(context, pending[0].id, review_token=review["review_token"])

        app.submit(session, "请回忆我的 Python 代码示例偏好。")
        app.wait(session, timeout=30)
        saved = app.snapshot(session)
        assert saved["task"]["status"] == "completed", saved["task"].get("error_code")
        assert saved["long_memory"]["citation"]["status"] == "valid"
        last_request = model.requests[-1]
        assert any("Python 短例子" in content for _, content in last_request)
        assert "Python 短例子" in saved["messages"][-1]["content"]
        # 正文只存在于动态投影，不写回永久消息历史。
        assert all("untrusted_user_memory" not in (message["content"] or "")
                   for message in saved["messages"])
    finally:
        app.close()