"""Runtime 主循环里过去没有被覆盖的分支（表征测试）。

这些用例在重构前就应对现状通过：它们的作用是把「当前真实行为」钉住，
使 runtime 拆分组件时任何语义漂移都会立刻失败。只通过公共入口
``Runtime.run/run_session`` 观察，不触达内部实现。
"""

import json
from dataclasses import replace
from uuid import UUID, uuid4

from min_agent.session.events import SessionEvents
from min_agent.contracts.models import Message, ModelResponse, ToolCall
from min_agent.runtime import Runtime
from min_agent.session.store import SessionStore
from min_agent.session.task_state import GoalSpec, TaskController
from min_agent.tools.calculator import CALCULATOR_TOOL
from min_agent.tools.registry import ToolRegistry
from tests.fakes.model import ScriptedModel


def call(call_id, expression="0.1+0.2", name="calculator"):
    return ToolCall(id=call_id, name=name, arguments={"expression": expression})


def request(*calls):
    return ModelResponse(finish_reason="tool_calls", tool_calls=list(calls))


def bound_registry(store, context, handler=None):
    tools = ToolRegistry(sessions=store, context=context)
    tools.register(replace(CALCULATOR_TOOL, handler=handler) if handler else CALCULATOR_TOOL)
    return tools


def tool_messages(messages):
    return [message for message in messages if message.role == "tool"]


def steer_messages(messages):
    return [message for message in messages
            if message.role == "user" and message.content.startswith("用户显式引导：")]


def test_steer_mid_batch_blocks_remaining_calls_and_keeps_run_alive():
    store = SessionStore()
    context = store.create(uuid4())
    events = SessionEvents(store, context)
    executed = []

    def handler(expression):
        executed.append(expression)
        events.steer(UUID(events.snapshot()["active"]), "先核对来源", scope_changed=False)
        return "0.3"

    model = ScriptedModel([request(call("a"), call("b")),
                           ModelResponse(finish_reason="stop", content="已按引导调整")])
    result = Runtime(model, bound_registry(store, context, handler), control=events).run_session(
        "计算两项", store=store, context=context)

    assert result.status == "completed" and result.error_code is None
    assert executed == ["0.1+0.2"]
    tool_results = tool_messages(model.calls[1])
    assert [message.tool_call_id for message in tool_results] == ["a", "b"]
    first, second = (json.loads(message.content) for message in tool_results)
    assert first == {"ok": True, "result": "0.3"}
    assert second["error"]["code"] == "steer_pending"
    assert second["error"]["execution_state"] == "not_started"
    assert steer_messages(model.calls[1]) == [Message(role="user", content="用户显式引导：先核对来源")]


def test_stop_mid_batch_pairs_remaining_calls_as_not_started_and_cancels():
    store = SessionStore()
    context = store.create(uuid4())
    events = SessionEvents(store, context)
    executed = []

    def handler(expression):
        executed.append(expression)
        events.stop()
        return "0.3"

    model = ScriptedModel([request(call("a"), call("b"))])
    result = Runtime(model, bound_registry(store, context, handler), control=events).run_session(
        "计算两项", store=store, context=context)

    assert result.status == "cancelled" and result.error_code == "user_stopped"
    assert result.checkpoint is None
    assert executed == ["0.1+0.2"]
    assert len(model.calls) == 1
    tool_results = tool_messages(store.snapshot(context).messages)
    assert [message.tool_call_id for message in tool_results] == ["a", "b"]
    assert json.loads(tool_results[0].content) == {"ok": True, "result": "0.3"}
    assert json.loads(tool_results[1].content) == {
        "ok": False, "error": {"code": "user_stopped", "execution_state": "not_started"}}


def test_progress_pause_stops_batch_and_pairs_remaining_calls():
    executed = []

    class PauseGuard:
        def observe(self, call, outcome):
            return "pause"

    def handler(expression):
        executed.append(expression)
        return "0.3"

    registry = ToolRegistry()
    registry.register(replace(CALCULATOR_TOOL, handler=handler))
    store = SessionStore()
    context = store.create(uuid4())
    model = ScriptedModel([request(call("a"), call("b"))])
    result = Runtime(model, registry, progress_guard=PauseGuard()).run_session(
        "批量计算", store=store, context=context)

    assert result.status == "partial" and result.error_code == "no_progress"
    assert executed == ["0.1+0.2"]
    assert len(model.calls) == 1
    assert json.loads(tool_messages(store.snapshot(context).messages)[1].content) == {
        "ok": False, "error": {"code": "no_progress", "execution_state": "not_started"}}


def test_progress_correction_injects_one_user_message_and_run_continues():
    executed = []
    observations = []

    class CorrectOnce:
        def observe(self, call, outcome):
            observations.append(outcome)
            return "correct" if len(observations) == 1 else "continue"

    def handler(expression):
        executed.append(expression)
        return "0.3"

    registry = ToolRegistry()
    registry.register(replace(CALCULATOR_TOOL, handler=handler))
    model = ScriptedModel([request(call("a")), ModelResponse(finish_reason="stop", content="完成")])
    result = Runtime(model, registry, progress_guard=CorrectOnce(),
                     max_tool_error_rounds=0).run("计算")

    assert result.status == "completed" and result.error_code is None
    assert executed == ["0.1+0.2"]
    correction = [message for message in model.calls[1]
                  if message.role == "user" and message.content.startswith("应用检测到三次相同调用")]
    assert len(correction) == 1
    assert "只允许一次有变化的纠错" in correction[0].content


def test_stop_with_unconfirmed_proposal_waits_for_user():
    store = SessionStore()
    context = store.create(uuid4())
    TaskController(store).propose(context, GoalSpec(
        objective="整理本周记录", completion_criteria=("用户确认目标",)))

    model = ScriptedModel([ModelResponse(finish_reason="stop", content="先确认目标再继续")])
    result = Runtime(model, bound_registry(store, context)).run_session(
        "开始", store=store, context=context)

    assert result.status == "waiting_user" and result.error_code == "goal_unconfirmed"
    assert len(model.calls) == 1


def test_propose_goal_request_ends_batch_as_waiting_user_even_without_execution():
    store = SessionStore()
    context = store.create(uuid4())
    model = ScriptedModel([request(call("plan", name="propose_goal"), call("after"))])

    result = Runtime(model, bound_registry(store, context)).run_session(
        "计划", store=store, context=context)

    assert result.status == "waiting_user" and result.error_code == "goal_unconfirmed"
    tool_results = tool_messages(store.snapshot(context).messages)
    assert [message.tool_call_id for message in tool_results] == ["plan", "after"]
    assert json.loads(tool_results[0].content)["error"]["code"] == "unknown_tool"
    assert json.loads(tool_results[1].content) == {
        "ok": False, "error": {"code": "waiting_user", "execution_state": "not_started"}}
    assert len(model.calls) == 1
