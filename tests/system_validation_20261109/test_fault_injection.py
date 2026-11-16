"""SYSVAL-20261109 — F01–F12 故障注入与硬安全回归（离线合成，不触碰生产库）。

原则：每个用例只操作自己创建的隔离 SQLite/子进程；先保留现场再断言；
权威事实一律从 SQLite/快照读取，不采信模型自然语言。
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path
from threading import Event

import pytest

from min_agent.application import HarnessApplication
from min_agent.ledger.budget import BudgetLimits
from min_agent.contracts.models import Message, ModelResponse, ToolCall
from min_agent.runtime import Runtime
from min_agent.session.store import SessionStore
from min_agent.session.storage import SQLiteStorage
from min_agent.session.task_state import ActionSpec, GoalSpec, TaskController
from min_agent.tools.registry import ToolDefinition, ToolRegistry
from min_agent.tools.todo import TodoService
from pydantic import BaseModel, ConfigDict

from tests.fakes.model import ScriptedModel

ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------- helpers
def add_action(title="交周报"):
    return ActionSpec(operation="todo.add", parameters={"title": title, "due_date": None})


def confirmed_app(tmp_path, model, actions=None, **kwargs):
    harness = HarnessApplication(tmp_path / "app.sqlite3", model=model, **kwargs)
    sid = harness.create_session()["session_id"]
    context = harness.context(sid)
    proposal = harness.propose(context, {
        "objective": "记录待办", "completion_criteria": ["待办存在"],
        "actions": [a.model_dump() for a in (actions or [add_action()])],
    })
    harness.confirm(sid, proposal.proposal_id)
    harness.set_mode(sid, "build")
    return harness, sid, context, proposal


def todos(harness, sid):
    return [v.item for v in TodoService(harness.sessions).list_views(harness.context(sid))]


def tool_messages(harness, sid):
    out = []
    for message in harness.sessions.snapshot(harness.context(sid)).messages:
        if message.role == "tool":
            out.append(json.loads(message.content))
    return out


def integrity(path):
    with sqlite3.connect(path) as conn:
        return conn.execute("PRAGMA integrity_check").fetchone()[0]


def budget_snapshot(harness, sid):
    return harness.snapshot(sid)["budget"]


# --------------------------------------------------------------- F01
def test_f01_model_timeout_is_failed_and_budget_counted(tmp_path):
    model = ScriptedModel([TimeoutError("simulated llm timeout")])
    harness = HarnessApplication(tmp_path / "f01.sqlite3", model=model)
    try:
        sid = harness.create_session()["session_id"]
        harness.submit(sid, "你好")
        harness.wait(sid)
        snap = harness.snapshot(sid)
        assert snap["task"]["status"] == "failed"
        assert snap["task"]["error_code"] == "model_call_failed"
        assert snap["budget"]["usage"]["model_calls"] == 1
        assert snap["budget"]["usage"]["tool_calls"] == 0
        assert todos(harness, sid) == []
        assert integrity(tmp_path / "f01.sqlite3") == "ok"
    finally:
        harness.close()


# --------------------------------------------------------------- F02
def test_f02a_unknown_tool_cannot_execute_or_report_success(tmp_path):
    model = ScriptedModel([
        ModelResponse(finish_reason="tool_calls",
                      tool_calls=[ToolCall(id="c1", name="drop_production_table", arguments={})]),
        ModelResponse(finish_reason="stop", content="已帮你删除数据库"),
    ])
    harness = HarnessApplication(tmp_path / "f02a.sqlite3", model=model)
    try:
        sid = harness.create_session()["session_id"]
        harness.submit(sid, "删除所有数据")
        harness.wait(sid)
        result = tool_messages(harness, sid)
        assert result and result[0]["ok"] is False
        assert result[0]["error"]["code"] == "unknown_tool"
        snap = harness.snapshot(sid)
        assert snap["task"]["status"] == "partial"
        assert snap["task"]["error_code"] == "tool_request_failed"
        assert todos(harness, sid) == []
    finally:
        harness.close()


def test_f02b_invalid_tool_arguments_are_rejected_before_execution(tmp_path):
    model = ScriptedModel([
        ModelResponse(finish_reason="tool_calls",
                      tool_calls=[ToolCall(id="c1", name="calculator", arguments={"expression": 12345})]),
        ModelResponse(finish_reason="stop", content="参数有误"),
    ])
    harness = HarnessApplication(tmp_path / "f02b.sqlite3", model=model)
    try:
        sid = harness.create_session()["session_id"]
        harness.submit(sid, "计算")
        harness.wait(sid)
        result = tool_messages(harness, sid)
        assert result[0]["ok"] is False and result[0]["error"]["code"] == "invalid_arguments"
        assert result[0]["error"]["execution_state"] == "not_started"
    finally:
        harness.close()


def test_f02c_cross_turn_duplicate_call_id_is_refused(tmp_path):
    model = ScriptedModel([
        ModelResponse(finish_reason="tool_calls",
                      tool_calls=[ToolCall(id="dup", name="calculator", arguments={"expression": "1+1"})]),
        ModelResponse(finish_reason="tool_calls",
                      tool_calls=[ToolCall(id="dup", name="calculator", arguments={"expression": "2+2"})]),
    ])
    harness = HarnessApplication(tmp_path / "f02c.sqlite3", model=model)
    try:
        sid = harness.create_session()["session_id"]
        harness.submit(sid, "计算")
        harness.wait(sid)
        snap = harness.snapshot(sid)
        assert snap["task"]["status"] == "failed"
        assert snap["task"]["error_code"] == "duplicate_tool_call_id"
        assert len(tool_messages(harness, sid)) == 1  # 第二个重复 ID 未执行
    finally:
        harness.close()


# --------------------------------------------------------------- F03
def test_f03_tool_exception_is_structured_and_not_completion(tmp_path):
    class Params(BaseModel):
        model_config = ConfigDict(strict=True, extra="forbid")

    registry = ToolRegistry()

    def explode():
        raise RuntimeError("tool backend down")

    registry.register(ToolDefinition("explode", "始终抛出异常的工具", Params, explode))
    model = ScriptedModel([
        ModelResponse(finish_reason="tool_calls", tool_calls=[ToolCall(id="t1", name="explode", arguments={})]),
    ])
    result = Runtime(model, registry, recover_unknown_writes=True).run("执行")
    assert result.status == "failed" and result.error_code == "tool_execution_failed"
    assert result.response is None or result.response.finish_reason == "tool_calls"


# --------------------------------------------------------------- F04
def test_f04_write_failure_before_commit_leaves_no_row(tmp_path, monkeypatch):
    from min_agent.tools.todo import TodoService as Service

    original = Service.add

    def fail_before_commit(self, context, **kwargs):
        raise RuntimeError("crash before commit")

    monkeypatch.setattr(Service, "add", fail_before_commit)
    model = ScriptedModel([
        ModelResponse(finish_reason="tool_calls", tool_calls=[
            ToolCall(id="t1", name="todo_add", arguments={"title": "交周报", "due_date": None})]),
    ])
    harness, sid, context, _ = confirmed_app(tmp_path, model)
    try:
        harness.submit(sid, "添加待办")
        harness.wait(sid)
        snap = harness.snapshot(sid)
        assert snap["task"]["status"] == "failed"
        assert snap["task"]["error_code"] == "tool_execution_failed"
        reached = [m for m in snap["messages"] if m["role"] == "tool"]
        assert reached and json.loads(reached[0]["content"])["error"]["execution_state"] == "unknown"
        assert todos(harness, sid) == []
        assert integrity(tmp_path / "app.sqlite3") == "ok"
    finally:
        monkeypatch.setattr(Service, "add", original)
        harness.close()


# --------------------------------------------------------------- F05
def test_f05_commit_without_receipt_is_unknown_and_never_replayed(tmp_path, monkeypatch):
    """F05：业务已提交但回执未建立，必须进入 UNKNOWN，且不得盲重试。"""
    from min_agent.tools.todo import TodoService as Service

    original = Service.add

    def commit_then_fail(self, context, **kwargs):
        original(self, context, **kwargs)
        raise RuntimeError("crash after commit, before receipt")

    monkeypatch.setattr(Service, "add", commit_then_fail)
    model = ScriptedModel([
        ModelResponse(finish_reason="tool_calls", tool_calls=[
            ToolCall(id="t1", name="todo_add", arguments={"title": "交周报", "due_date": None})]),
    ])
    harness, sid, context, _ = confirmed_app(tmp_path, model)
    observed = {}
    try:
        harness.submit(sid, "添加待办")
        harness.wait(sid)
        snap = harness.snapshot(sid)
        assert snap["task"]["status"] == "failed"
        assert snap["task"]["error_code"] == "tool_execution_failed"
        assert len(todos(harness, sid)) == 1, "业务写入确实发生了"
        assert integrity(tmp_path / "app.sqlite3") == "ok"
        journal = harness.sessions.extension(context, "operations", {})
        unknown = [k for k, v in journal.items() if v.get("status") == "unknown"]
        assert unknown, "必须留下 UNKNOWN 操作记录"
        budget_id = harness.snapshot(sid)["budget"]["id"]
        operation_key = unknown[0]
        assert operation_key.startswith(budget_id), (operation_key, budget_id)
    finally:
        monkeypatch.setattr(Service, "add", original)
        harness.close()

    # 重新打开同一隔离库：写入只发生一次，UNKNOWN 记录保留
    harness2 = HarnessApplication(tmp_path / "app.sqlite3", model=ScriptedModel([]))
    try:
        assert len(todos(harness2, sid)) == 1
        context2 = harness2.context(sid)
        journal2 = harness2.sessions.extension(context2, "operations", {})
        assert any(entry.get("status") == "unknown" for entry in journal2.values())
        from min_agent.tools.todo_tools import todo_registry
        registry = todo_registry(TodoService(harness2.sessions),
                                 TaskController(harness2.sessions), context2)
        raised, outcome = None, None
        try:
            outcome = registry.execute(ToolCall(id="t1", name="todo_add",
                                                arguments={"title": "交周报", "due_date": None}),
                                       operation_id=operation_key)
        except BaseException as error:  # noqa: BLE001 - 记录真实拒绝类型
            raised = type(error).__name__
        observed["replay"] = raised or "returned"
        observed["replay_outcome"] = None if outcome is None else outcome.get("ok")
        # 允许两种安全收敛：显式拒绝，或业务层按 operation_id 派生的确定性 ID 幂等返回。
        assert raised is not None or outcome.get("ok") is True
        assert len(todos(harness2, sid)) == 1, "重放不得产生第二条待办"
        if outcome is not None:
            recorded = json.loads(outcome["result"])
            assert recorded["title"] == "交周报"
            assert recorded["id"] == str(todos(harness2, sid)[0].id)
    finally:
        harness2.close()
    (tmp_path / "f05-observation.json").write_text(json.dumps(observed), encoding="utf-8")


# --------------------------------------------------------------- F06
def test_f06_sqlite_write_lock_does_not_corrupt_records(tmp_path):
    path = tmp_path / "lock.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        sessions = SessionStore(storage=storage)
        import uuid
        context = sessions.create(uuid.uuid4())
        holder = sqlite3.connect(path, timeout=0.2, isolation_level=None)
        holder.execute("BEGIN IMMEDIATE")
        try:
            with pytest.raises(Exception):
                sessions.set_extension(context, "probe", "value")
        finally:
            holder.execute("ROLLBACK")
            holder.close()
        # 锁释放后写入仍然成功，且库未损坏
        sessions.set_extension(context, "probe", "value")
        assert sessions.extension(context, "probe", None) == "value"
    assert integrity(path) == "ok"


# --------------------------------------------------------------- F07
def test_f07_uncommitted_transaction_killed_rolls_back(tmp_path):
    path = tmp_path / "kill.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        SessionStore(storage=storage).create(__import__("uuid").uuid4())
    script = textwrap.dedent(f"""
        import os, sqlite3
        conn = sqlite3.connect(r"{path}", isolation_level=None)
        conn.execute("CREATE TABLE IF NOT EXISTS probe (value TEXT)")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO probe VALUES ('uncommitted')")
        os._exit(9)
    """)
    code = subprocess.run([sys.executable, "-c", script], capture_output=True).returncode
    assert code == 9
    with sqlite3.connect(path) as conn:
        rows = conn.execute("SELECT count(*) FROM probe").fetchone()[0]
        assert rows == 0, "未提交事务必须回滚"
    assert integrity(path) == "ok"


# --------------------------------------------------------------- F08 / F09
def test_f08_f09_interrupted_process_then_directed_resume(tmp_path):
    path = tmp_path / "crash.sqlite3"
    script = textwrap.dedent(f"""
        import os, sys, uuid
        sys.path.insert(0, r"{ROOT / 'src'}")
        from min_agent.application import HarnessApplication
        from min_agent.contracts.models import ModelResponse
        class Block:
            def generate(self, messages, *, tools=None):
                os._exit(11)
        app = HarnessApplication(r"{path}", model=Block())
        sid = app.create_session()["session_id"]
        open(r"{tmp_path / 'sid.txt'}", "w").write(sid)
        app.submit(sid, "开始一个任务")
        import time; time.sleep(30)
    """)
    code = subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=120).returncode
    assert code == 11, "测试进程自身按预期退出"
    sid = (tmp_path / "sid.txt").read_text().strip()
    model = ScriptedModel([ModelResponse(finish_reason="stop", content="已恢复")])
    harness = HarnessApplication(path, model=model)
    try:
        snap = harness.snapshot(sid)
        assert snap["task"]["status"] == "partial"
        assert snap["task"]["error_code"] == "interrupted"
        assert snap["checkpoint"] is not None
        old_task, old_budget = snap["task"]["task_id"], snap["budget"]["id"]
        harness.resume(sid, checkpoint_id=snap["checkpoint"]["checkpoint"]["checkpoint_id"])
        harness.wait(sid)
        after = harness.snapshot(sid)
        assert after["budget"]["id"] == old_budget
        assert after["work_state"]["confirmed_goal_id"] is None
        assert integrity(path) == "ok"
        with pytest.raises(Exception):
            harness.resume(sid, task_id=old_task)
    finally:
        harness.close()


# --------------------------------------------------------------- F10
def test_f10_revoked_permission_blocks_network_before_fetch(tmp_path):
    from min_agent.session.artifacts import ArtifactStore
    from min_agent.research.service import ResearchService
    from min_agent.session.task_state import AuthorizationRequired

    calls = {"search": 0, "read": 0}

    class FakeSearch:
        def search(self, query):
            calls["search"] += 1
            return [{"title": "t", "href": "https://example.org/a", "body": "b"}]

    class FakeReader:
        def read(self, url, **kwargs):
            calls["read"] += 1
            return {"text": "content", "url": url, "start": 0, "end": 7}

    class SearchOnceModel:
        def __init__(self):
            self.calls = 0

        def generate(self, messages, *, tools=None):
            self.calls += 1
            if self.calls == 1:
                return ModelResponse(finish_reason="tool_calls", tool_calls=[
                    ToolCall(id="s1", name="search", arguments={"query": "公开问题"})])
            raise AssertionError("撤权后不应再有模型请求")

    with SQLiteStorage.create_new(tmp_path / "f10.sqlite3") as storage:
        sessions = SessionStore(storage=storage)
        context = sessions.create(__import__("uuid").uuid4())
        task = sessions.begin(context)
        from min_agent.ledger.budget_persistence import SessionBudgets
        budgets = SessionBudgets(sessions)
        budget_id = budgets.create(context, BudgetLimits(model_calls=6, tool_calls=6))
        budget = budgets.open(context, budget_id).start_segment(
            BudgetLimits(model_calls=3, tool_calls=3))
        revoked = Event()
        revoked.set()

        def permission_check():
            if revoked.is_set():
                raise AuthorizationRequired("network_not_authorized")

        model = SearchOnceModel()
        service = ResearchService(model, FakeSearch(), FakeReader(),
                                  ArtifactStore(sessions, context))
        outcome = service.run("查询", budget, permission_check=permission_check)
        assert outcome["status"] in {"waiting_user", "failed", "partial"}, outcome
        assert outcome["status"] != "completed"
        assert calls == {"search": 0, "read": 0}, "撤权后网络调用必须为 0"
        sessions.finish(context, task.task_id, status="cancelled")


# --------------------------------------------------------------- F11
def test_f11_context_overflow_is_partial_not_fabricated(tmp_path):
    model = ScriptedModel([ModelResponse(finish_reason="stop", content="不该被使用")])
    harness = HarnessApplication(tmp_path / "f11.sqlite3", model=model, window=200)
    try:
        sid = harness.create_session()["session_id"]
        harness.submit(sid, "很长的输入" * 200)
        harness.wait(sid)
        snap = harness.snapshot(sid)
        assert snap["task"]["status"] == "partial"
        assert snap["task"]["error_code"] == "context_limit"
        assistant = [m for m in snap["messages"] if m["role"] == "assistant"]
        assert assistant == [], "上下文不足时不得产生编造回答"
        assert model.calls == [], "超窗请求未发送给模型"
    finally:
        harness.close()


# --------------------------------------------------------------- F12
def test_f12_oversized_response_is_rejected(tmp_path):
    import httpx
    from min_agent.llm.client import LLMError
    from min_agent.llm.ollama import OllamaModel, OllamaSettings

    big = b'{"done":true,"message":{"role":"assistant","content":"' + b"x" * (5 * 1024 * 1024) + b'"}}'
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=big))
    settings = OllamaSettings(num_ctx=4096, max_output_tokens=256)
    model = OllamaModel(settings, transport=transport)
    try:
        with pytest.raises(LLMError) as error:
            model.generate([Message(role="user", content="hi")])
        assert error.value.code == "response_too_large"
        assert model.request_count == 1
    finally:
        model.close()
