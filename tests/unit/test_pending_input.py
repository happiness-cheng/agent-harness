"""QWEN-UX-001：输入可见性与中断恢复的离线测试。"""

import json

from min_agent.application import HarnessApplication
from min_agent.contracts.models import ModelResponse


class _FakeModel:
    def generate(self, messages, *, tools=None):
        return ModelResponse(finish_reason="stop", content="收到")


def _app(path):
    return HarnessApplication(path, model=_FakeModel())


def test_snapshot_exposes_pending_input(tmp_path):
    harness = _app(tmp_path / "pending.sqlite3")
    try:
        identity = harness.create_session("A")["session_id"]
        context = harness.context(identity)
        harness.sessions.set_extension(context, "pending_input", "正在处理的输入")

        assert harness.snapshot(identity)["pending_input"] == "正在处理的输入"
    finally:
        harness.close()


def test_completed_turn_clears_pending_and_persists_once(tmp_path):
    harness = _app(tmp_path / "turn.sqlite3")
    try:
        identity = harness.create_session("A")["session_id"]
        harness.submit(identity, "你好")
        harness.wait(identity, timeout=30)
        snapshot = harness.snapshot(identity)

        assert snapshot["pending_input"] is None
        assert [m["role"] for m in snapshot["messages"]] == ["user", "assistant"]
        assert [m["content"] for m in snapshot["messages"]] == ["你好", "收到"]
    finally:
        harness.close()


def test_crashed_run_input_is_restored_after_restart(tmp_path):
    database = tmp_path / "crash.sqlite3"
    first = _app(database)
    try:
        identity = first.create_session("A")["session_id"]
        context = first.context(identity)
        task = first.sessions.begin(context)
        first.sessions.set_extension(context, "pending_input", "崩溃前的那句话")
    finally:
        first.close()

    second = _app(database)
    try:
        snapshot = second.snapshot(identity)
        assert snapshot["task"]["status"] == "partial"
        assert snapshot["task"]["error_code"] == "interrupted"
        assert snapshot["pending_input"] is None
        messages = snapshot["messages"]
        assert [m["role"] for m in messages] == ["user"]
        assert messages[0]["content"] == "崩溃前的那句话"
        assert task.task_id  # 原任务仍在库中，只是被标记为中断
    finally:
        second.close()


def test_recovery_leaves_normal_history_untouched(tmp_path):
    database = tmp_path / "clean.sqlite3"
    first = _app(database)
    try:
        identity = first.create_session("A")["session_id"]
        first.submit(identity, "正常一轮")
        first.wait(identity, timeout=30)
    finally:
        first.close()

    second = _app(database)
    try:
        snapshot = second.snapshot(identity)
        assert [m["content"] for m in snapshot["messages"]] == ["正常一轮", "收到"]
        assert snapshot["task"]["status"] == "completed"
        assert snapshot["pending_input"] is None
    finally:
        second.close()