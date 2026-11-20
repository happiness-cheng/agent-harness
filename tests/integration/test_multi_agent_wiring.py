"""WIRING-003：delegate_task 的生产工厂、共享预算与默认关闭边界。"""

import json

from min_agent.agents.factory import build_multi_agent_factory
from min_agent.application import HarnessApplication
from min_agent.contracts.models import ModelResponse, ToolCall
from tests.fakes.model import ScriptedModel


def plan(*roles):
    return {"subtasks": [
        {"subtask_id": f"s{index}", "role": role,
         "objective": f"子任务 {index}", "worker_profile": "worker",
         "depends_on": [f"s{index - 1}"] if index > 1 else []}
        for index, role in enumerate(roles, start=1)
    ]}


def worker(summary):
    return {"summary": summary, "details": [], "limitations": [],
            "needs_escalation": False, "escalation_reason": ""}


def delegate_call(call_id="d1"):
    return ModelResponse(finish_reason="tool_calls", tool_calls=[
        ToolCall(id=call_id, name="delegate_task", arguments={"objective": "拆两个子任务"})])


def calculator_call(call_id):
    return ModelResponse(finish_reason="tool_calls", tool_calls=[
        ToolCall(id=call_id, name="calculator", arguments={"expression": "1+1"})])


def make_app(tmp_path, script, **kwargs):
    model = ScriptedModel(script)
    app = HarnessApplication(
        tmp_path / "ma.sqlite3", model=model, memory_mode="off",
        enable_multi_agent=True,
        multi_agent_service_factory=build_multi_agent_factory(), **kwargs)
    return app, model


def run(app, model, text="委派任务", timeout=60):
    session = app.create_session()["session_id"]
    app.submit(session, text)
    app.wait(session, timeout=timeout)
    return session, app.snapshot(session)


def delegation_payload(saved):
    tool_message = next(message for message in saved["messages"]
                        if message["role"] == "tool")
    payload = json.loads(tool_message["content"])
    assert payload["ok"] is True
    return json.loads(payload["result"])


def test_delegate_task_runs_two_subtasks_and_reuses_task_budget(tmp_path):
    app, model = make_app(tmp_path, [
        delegate_call(),
        ModelResponse(finish_reason="stop", content=json.dumps(plan("calculator", "calculator"))),
        calculator_call("calc-1"),
        ModelResponse(finish_reason="stop", content=json.dumps(worker("1+1=2"))),
        calculator_call("calc-2"),
        ModelResponse(finish_reason="stop", content=json.dumps(worker("1+1=2"))),
        ModelResponse(finish_reason="stop", content="两个子任务均完成。"),
        ModelResponse(finish_reason="stop", content="最终答复。"),
    ], segment_models=6, segment_tools=12)
    try:
        session, saved = run(app, model)
    finally:
        app.close()

    assert saved["task"]["status"] == "completed", saved["task"].get("error_code")
    payload = delegation_payload(saved)
    assert payload["status"] == "completed"
    assert [row["verification"] for row in payload["results"]] == ["PASS", "PASS"]
    assert [row["tool_calls"] for row in payload["results"]] == [1, 1]
    assert len(payload["plan"]["subtasks"]) == 2
    # 多 Agent 打开时片段复用任务总预算，而不是固定的 6/12。
    assert (app.segment_models, app.segment_tools) == (40, 60)
    assert saved["budget"]["usage"]["model_calls"] == 8
    assert saved["budget"]["usage"]["tool_calls"] == 3
    assert saved["multi_agent"]["status"] == "completed"
    assert "最终答复" in saved["messages"][-1]["content"]


def test_delegate_task_is_not_registered_when_multi_agent_is_off(tmp_path):
    class CheckTools:
        def generate(self, messages, *, tools=None):
            assert "delegate_task" not in {item["name"] for item in tools}
            return ModelResponse(finish_reason="stop", content="普通答复")

    app = HarnessApplication(tmp_path / "plain.sqlite3", model=CheckTools(),
                             memory_mode="off")
    try:
        session = app.create_session()["session_id"]
        app.submit(session, "你好")
        app.wait(session, timeout=30)
        assert app.snapshot(session)["task"]["status"] == "completed"
    finally:
        app.close()


def test_budget_guard_stops_delegation_before_overrun(tmp_path):
    app, model = make_app(tmp_path, [
        delegate_call(),
        ModelResponse(finish_reason="stop", content=json.dumps(plan("calculator", "calculator"))),
        calculator_call("calc-1"),
        ModelResponse(finish_reason="stop", content=json.dumps(worker("1+1=2"))),
        ModelResponse(finish_reason="stop", content="不应发生的汇总。"),
        ModelResponse(finish_reason="stop", content="不应发生的最终答复。"),
    ], total_models=3, total_tools=10)
    try:
        session, saved = run(app, model)
    finally:
        app.close()

    # 第 4 次模型调用在预算门被拒绝，脚本里的后续响应从未被消费。
    assert len(model.calls) == 3
    assert saved["task"]["status"] == "partial"
    assert saved["budget"]["usage"]["model_calls"] == 3