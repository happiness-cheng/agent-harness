"""Ollama Provider 与现有 Runtime/Registry/预算/检查点的离线集成测试。

使用真实 Runtime、真实 SQLite 会话和 MockTransport；不连接本机 Ollama。
"""

import json

import httpx
import pytest

from min_agent.application import HarnessApplication
from min_agent.llm.ollama import OllamaModel, OllamaSettings
from min_agent.runtime import Runtime
from min_agent.tools.calculator import CALCULATOR_TOOL
from min_agent.tools.registry import ToolDefinition, ToolRegistry


def chat(*, content="", calls=None, reason="stop", prompt=4094):
    message = {"role": "assistant", "content": content}
    if calls is not None:
        message["tool_calls"] = calls
    return {
        "model": "qwen3.5:4b", "message": message, "done": True, "done_reason": reason,
        "prompt_eval_count": prompt, "eval_count": 12,
    }


def function_call(call_id, name, arguments):
    return {"id": call_id, "function": {"name": name, "arguments": arguments}}


def calculator_registry(executed):
    def tracked(expression):
        executed.append(expression)
        return "703" if expression.replace(" ", "") == "37*19" else "2"

    registry = ToolRegistry()
    registry.register(ToolDefinition("calculator", CALCULATOR_TOOL.description,
                                    CALCULATOR_TOOL.parameters, tracked))
    return registry


def test_application_tool_loop_records_budget_trace_and_tool_result(tmp_path):
    bodies = []

    def handle(request):
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            return httpx.Response(200, json=chat(calls=[function_call("call_math", "calculator", {"expression": "37*19"})]))
        return httpx.Response(200, json=chat(content="37 乘以 19 等于 703。"))

    harness = HarnessApplication(tmp_path / "ollama-loop.sqlite3", model=OllamaModel(
        OllamaSettings(), transport=httpx.MockTransport(handle)))
    try:
        session = harness.create_session("Ollama计算")["session_id"]
        harness.submit(session, "请使用 calculator 工具计算 37 乘以 19，并告诉我结果。")
        harness.wait(session, timeout=60)
        snapshot = harness.snapshot(session)
    finally:
        harness.close()

    assert snapshot["task"]["status"] == "completed"
    assert snapshot["demo"] is False
    messages = snapshot["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant", "tool", "assistant"]
    assert messages[1]["tool_calls"][0]["name"] == "calculator"
    assert messages[1]["tool_calls"][0]["arguments"] == {"expression": "37*19"}
    assert json.loads(messages[2]["content"]) == {"ok": True, "result": "703"}
    assert messages[3]["content"] == "37 乘以 19 等于 703。"
    assert snapshot["budget"]["usage"]["model_calls"] == 2
    assert snapshot["budget"]["usage"]["tool_calls"] == 1
    operations = [(event["operation"], event["status"]) for event in snapshot["trace"]]
    assert operations.count(("model", "completed")) == 2
    assert ("tool", "completed") in operations
    # 第二轮请求用真实工具名回传结果，而不是内部tool_call_id。
    tool_entries = [message for message in bodies[1]["messages"] if message.get("role") == "tool"]
    assert len(tool_entries) == 1 and tool_entries[0]["tool_name"] == "calculator"
    assert "tool_call_id" not in tool_entries[0]


def test_side_effect_tool_stops_for_authorization_without_writing(tmp_path):
    bodies = []

    def handle(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=chat(calls=[function_call(
            "call_todo", "todo_add", {"title": "未经授权的待办", "due_date": None})]))

    harness = HarnessApplication(tmp_path / "ollama-auth.sqlite3", model=OllamaModel(
        OllamaSettings(), transport=httpx.MockTransport(handle)))
    try:
        session = harness.create_session("Ollama授权")["session_id"]
        harness.submit(session, "帮我创建一个待办：未经授权的待办")
        harness.wait(session, timeout=60)
        snapshot = harness.snapshot(session)
    finally:
        harness.close()

    assert snapshot["task"]["status"] == "waiting_user"
    assert snapshot["todos"] == []
    assert len(bodies) == 1  # 未获授权就不能继续请求模型
    tool_message = json.loads(snapshot["messages"][-1]["content"])
    assert tool_message["ok"] is False
    assert tool_message["error"]["execution_state"] == "not_started"
    assert any(event["operation"] == "tool" and event["status"] == "waiting_user"
               for event in snapshot["trace"])


def test_truncated_prompt_is_a_checkpoint_not_an_answer_and_can_resume(tmp_path):
    bodies = []

    def handle(request):
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            return httpx.Response(200, json=chat(content="被截断前的半句", prompt=8190))
        return httpx.Response(200, json=chat(content="恢复后的完整回答。"))

    harness = HarnessApplication(tmp_path / "ollama-resume.sqlite3", model=OllamaModel(
        OllamaSettings(num_ctx=8192), transport=httpx.MockTransport(handle)))
    try:
        session = harness.create_session("Ollama恢复")["session_id"]
        harness.submit(session, "会被截断的输入")
        harness.wait(session, timeout=60)
        failed = harness.snapshot(session)
        checkpoint = failed["checkpoint"]["checkpoint"]["checkpoint_id"]
        assert failed["task"]["status"] == "partial"
        assert failed["task"]["error_code"] == "context_limit"
        assert failed["messages"][-1]["role"] == "user"  # 截断响应没有写入assistant消息

        harness.resume(session, checkpoint_id=checkpoint)
        harness.wait(session, timeout=60)
        resumed = harness.snapshot(session)
    finally:
        harness.close()

    assert resumed["task"]["status"] == "completed"
    assert resumed["messages"][-1]["content"] == "恢复后的完整回答。"
    assert resumed["checkpoint"]["resumed_by"] is not None
    assert len(bodies) == 2


def test_repeated_tool_call_id_fails_without_executing_twice():
    executed = []
    bodies = []

    def handle(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=chat(calls=[function_call(
            "call_same", "calculator", {"expression": "1+1"})]))

    with OllamaModel(OllamaSettings(), transport=httpx.MockTransport(handle)) as model:
        result = Runtime(model, tools=calculator_registry(executed)).run("重复调用")
    assert result.status == "failed" and result.error_code == "duplicate_tool_call_id"
    assert executed == ["1+1"]
    assert model.request_count == 2


def test_budget_reserve_stops_before_new_tool_work(tmp_path):
    bodies = []

    def handle(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=chat(calls=[function_call(
            "call_math", "calculator", {"expression": "37*19"})]))

    harness = HarnessApplication(tmp_path / "ollama-budget.sqlite3", total_models=1, segment_models=1,
                                 model=OllamaModel(OllamaSettings(), transport=httpx.MockTransport(handle)))
    try:
        session = harness.create_session("Ollama预算")["session_id"]
        harness.submit(session, "算一下37*19")
        harness.wait(session, timeout=60)
        snapshot = harness.snapshot(session)
    finally:
        harness.close()

    # 只有预算内的收尾尝试；没有执行calculator，也没有放行第二轮工具工作。
    assert len(bodies) == 1
    assert snapshot["task"]["status"] == "partial"
    assert snapshot["task"]["error_code"] == "budget_checkpoint"
    assert snapshot["checkpoint"] is not None
    assert not [message for message in snapshot["messages"] if message["role"] == "tool"]
    assert snapshot["budget"]["usage"]["model_calls"] == 1
    assert snapshot["budget"]["usage"]["tool_calls"] == 0


def test_cli_modes_are_explicit_and_conflicting_selection_is_rejected():
    from min_agent.web.__main__ import main
    with pytest.raises(SystemExit) as error:
        main(["--ollama", "--demo"])
    assert error.value.code == 2
    with pytest.raises(SystemExit) as error:
        main(["--demo", "--live"])
    assert error.value.code == 2
