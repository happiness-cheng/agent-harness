import json
from dataclasses import replace

import pytest
from pydantic import ValidationError

from min_agent.models import Message, ModelResponse, ToolCall
from min_agent.runtime import Runtime
from min_agent.tools.calculator import CALCULATOR_TOOL
from min_agent.tools.registry import ToolRegistry
from tests.fakes.model import ScriptedModel


def calculator_request(call_id="calc-1", expression="0.1+0.2"):
    return ModelResponse(
        finish_reason="tool_calls",
        content="先计算",
        tool_calls=[ToolCall(id=call_id, name="calculator", arguments={"expression": expression})],
    )


def test_assistant_tool_message_round_trip_preserves_request_and_optional_text():
    call = calculator_request().tool_calls[0]

    message = Message(role="assistant", content="", tool_calls=[call])

    assert Message.model_validate_json(message.model_dump_json()) == message
    assert message.tool_calls == [call]
    assert message.tool_call_id is None


@pytest.mark.parametrize("role", ["user", "system", "tool"])
def test_only_assistant_can_carry_tool_requests(role):
    with pytest.raises(ValidationError):
        Message(
            role=role,
            content="内容",
            tool_call_id="calc-1" if role == "tool" else None,
            tool_calls=calculator_request().tool_calls,
        )


def test_message_rejects_duplicate_request_ids():
    call = calculator_request().tool_calls[0]

    with pytest.raises(ValidationError):
        Message(role="assistant", content="", tool_calls=[call, call])


def test_message_tool_request_lists_are_independent():
    first = Message(role="assistant", content="第一条")
    second = Message(role="assistant", content="第二条")

    first.tool_calls.append(calculator_request().tool_calls[0])

    assert second.tool_calls == []


def test_calculator_result_is_returned_with_matching_id_before_final_reply():
    registry = ToolRegistry()
    registry.register(CALCULATOR_TOOL)
    request = calculator_request()
    answer = ModelResponse(finish_reason="stop", content="结果是0.3")
    model = ScriptedModel([request, answer])

    result = Runtime(model, tools=registry).run("请计算0.1+0.2")

    assert result.status == "completed"
    assert result.response == answer
    assert result.error_code is None
    assert model.calls[0] == [Message(role="user", content="请计算0.1+0.2")]
    assert model.calls[1][:2] == [
        Message(role="user", content="请计算0.1+0.2"),
        Message(role="assistant", content="先计算", tool_calls=request.tool_calls),
    ]
    tool_message = model.calls[1][2]
    assert len(model.calls[1]) == 3
    assert tool_message.role == "tool"
    assert tool_message.tool_call_id == "calc-1"
    assert json.loads(tool_message.content) == {"ok": True, "result": "0.3"}
    assert model.tool_schemas == [registry.schemas(), registry.schemas()]


def registry_with_calculator(handler=CALCULATOR_TOOL.handler):
    registry = ToolRegistry()
    registry.register(replace(CALCULATOR_TOOL, handler=handler))
    return registry


def test_multiple_rounds_preserve_all_requests_results_and_order():
    first = calculator_request("first", "2+3")
    second = calculator_request("second", "5*4")
    second.content = None
    model = ScriptedModel([first, second, ModelResponse(finish_reason="stop", content="20")])

    result = Runtime(model, tools=registry_with_calculator()).run("先求和再乘4")

    assert result.status == "completed"
    assert result.response.content == "20"
    assert len(model.calls) == 3
    assert [message.role for message in model.calls[2]] == [
        "user", "assistant", "tool", "assistant", "tool",
    ]
    assert [message.tool_call_id for message in model.calls[2] if message.role == "tool"] == [
        "first", "second",
    ]
    assert [json.loads(message.content) for message in model.calls[2] if message.role == "tool"] == [
        {"ok": True, "result": "5"}, {"ok": True, "result": "20"},
    ]
    assert model.calls[2][3].content == ""
    assert model.calls[2][3].tool_calls == second.tool_calls
    assert len(model.calls[0]) == 1


@pytest.mark.parametrize("reason", ["length", "refusal", "error"])
def test_non_tool_finish_never_executes_embedded_requests(reason):
    executed = []
    response = calculator_request()
    response.finish_reason = reason
    model = ScriptedModel([response])

    result = Runtime(model, tools=registry_with_calculator(lambda **kwargs: executed.append(kwargs))).run("输入")

    assert result.status != "completed"
    assert result.response == response
    assert len(model.calls) == 1
    assert executed == []


@pytest.mark.parametrize(
    ("reason", "status", "error_code"),
    [("length", "partial", "output_limit"), ("refusal", "failed", "model_refusal"),
     ("error", "failed", "model_error")],
)
def test_followup_response_uses_same_terminal_rules(reason, status, error_code):
    followup = ModelResponse(finish_reason=reason, content="已有部分回答")
    model = ScriptedModel([calculator_request(), followup])

    result = Runtime(model, tools=registry_with_calculator()).run("输入")

    assert result.status == status
    assert result.error_code == error_code
    assert result.response == followup
    assert len(model.calls) == 2
    assert json.loads(model.calls[1][-1].content) == {"ok": True, "result": "0.3"}


def test_followup_model_exception_stops_without_replaying_tool():
    executed = []

    def handler(expression):
        executed.append(expression)
        return "0.3"

    model = ScriptedModel([calculator_request(), TimeoutError("仅测试的私密诊断")])

    result = Runtime(model, tools=registry_with_calculator(handler)).run("输入")

    assert result.status == "failed"
    assert result.error_code == "model_call_failed"
    assert "仅测试的私密诊断" not in repr(result)
    assert executed == ["0.1+0.2"]
    assert len(model.calls) == 2


def test_two_requests_are_executed_before_followup():
    executed = []
    response = calculator_request()
    response.tool_calls.append(calculator_request("calc-2").tool_calls[0])
    answer = ModelResponse(finish_reason="stop", content="两个计算已返回")
    model = ScriptedModel([response, answer])

    result = Runtime(model, tools=registry_with_calculator(lambda **kwargs: executed.append(kwargs) or "0.3")).run("输入")

    assert result.status == "completed"
    assert result.error_code is None
    assert result.response == answer
    assert executed == [{"expression": "0.1+0.2"}, {"expression": "0.1+0.2"}]
    assert [m.tool_call_id for m in model.calls[1][2:]] == ["calc-1", "calc-2"]
    assert len(model.calls) == 2


def test_repeated_call_id_is_rejected_before_reexecuting_handler():
    executed = []

    def handler(expression):
        executed.append(expression)
        return "0.3"

    model = ScriptedModel([calculator_request(), calculator_request()])

    result = Runtime(model, tools=registry_with_calculator(handler)).run("输入")

    assert result.status == "failed"
    assert result.error_code == "duplicate_tool_call_id"
    assert executed == ["0.1+0.2"]
    assert len(model.calls) == 2


@pytest.mark.parametrize("failure", ["unknown_tool", "invalid_arguments", "division_by_zero", "handler_exception"])
def test_tool_request_errors_are_returned_but_execution_failures_stop(failure):
    response = calculator_request()
    registry = registry_with_calculator()
    executed = []

    def broken_handler(expression):
        executed.append(expression)
        raise RuntimeError("仅测试的handler诊断")

    if failure == "unknown_tool":
        response.tool_calls[0].name = "missing"
    elif failure == "invalid_arguments":
        response.tool_calls[0].arguments = {"expression": 123}
        registry = registry_with_calculator(broken_handler)
    elif failure == "division_by_zero":
        response.tool_calls[0].arguments = {"expression": "1/0"}
    else:
        registry = registry_with_calculator(broken_handler)
    repairable = failure in {"unknown_tool", "invalid_arguments"}
    refusal = ModelResponse(finish_reason="refusal", content="尚未修正")
    model = ScriptedModel([response, refusal] if repairable else [response])

    result = Runtime(model, tools=registry).run("输入")

    assert result.status == "failed"
    assert result.error_code == ("model_refusal" if repairable else "tool_execution_failed")
    assert result.response == (refusal if repairable else response)
    assert "仅测试的handler诊断" not in repr(result)
    assert executed == (["0.1+0.2"] if failure == "handler_exception" else [])
    assert len(model.calls) == (2 if repairable else 1)
    if repairable:
        assert json.loads(model.calls[1][-1].content)["error"]["execution_state"] == "not_started"


@pytest.mark.parametrize("output", [None, 3, {"answer": "0.3"}])
def test_non_text_tool_output_is_not_silently_stringified(output):
    model = ScriptedModel([calculator_request()])

    result = Runtime(model, tools=registry_with_calculator(lambda **kwargs: output)).run("输入")

    assert result.status == "failed"
    assert result.error_code == "invalid_tool_result"
    assert len(model.calls) == 1


def test_adapter_cannot_mutate_retained_history_or_registry_schema():
    class MutatingModel(ScriptedModel):
        def generate(self, messages, *, tools=None):
            response = super().generate(messages, tools=tools)
            messages.clear()
            tools[0]["parameters"]["properties"].clear()
            return response

    registry = registry_with_calculator()
    original_schema = registry.schemas()
    model = MutatingModel([calculator_request(), ModelResponse(finish_reason="stop", content="0.3")])

    result = Runtime(model, tools=registry).run("保留原文")

    assert result.status == "completed"
    assert model.calls[1][0] == Message(role="user", content="保留原文")
    assert len(model.calls[1]) == 3
    assert model.tool_schemas == [original_schema, original_schema]
    assert registry.schemas() == original_schema


def test_new_run_does_not_reuse_history_or_call_id_tracking():
    answer = ModelResponse(finish_reason="stop", content="0.3")
    model = ScriptedModel([calculator_request(), answer, calculator_request(), answer])
    runtime = Runtime(model, tools=registry_with_calculator())

    first = runtime.run("第一次")
    second = runtime.run("第二次")

    assert first.status == second.status == "completed"
    assert model.calls[2] == [Message(role="user", content="第二次")]
    assert model.calls[3][0].content == "第二次"
    assert len(model.calls[3]) == 3


def test_tool_process_interrupt_is_not_swallowed():
    def interrupt(expression):
        raise KeyboardInterrupt()

    model = ScriptedModel([calculator_request()])

    with pytest.raises(KeyboardInterrupt):
        Runtime(model, tools=registry_with_calculator(interrupt)).run("输入")

    assert len(model.calls) == 1


@pytest.mark.parametrize("invalid", [None, "invalid_stop"])
def test_followup_response_is_revalidated_before_any_further_execution(invalid):
    executed = []

    def handler(expression):
        executed.append(expression)
        return "0.3"

    followup = None if invalid is None else ModelResponse.model_construct(
        finish_reason="stop", content="", tool_calls=[],
    )

    class InvalidFollowupModel(ScriptedModel):
        def generate(self, messages, *, tools=None):
            if self.calls:
                self.calls.append([message.model_copy(deep=True) for message in messages])
                return followup
            return super().generate(messages, tools=tools)

    model = InvalidFollowupModel([calculator_request()])

    result = Runtime(model, tools=registry_with_calculator(handler)).run("输入")

    assert result.status == "failed"
    assert result.error_code == "invalid_model_response"
    assert executed == ["0.1+0.2"]
    assert len(model.calls) == 2


def test_empty_injected_registry_does_not_enable_builtin_calculator():
    model = ScriptedModel([calculator_request(), ModelResponse(finish_reason="refusal", content="工具未启用")])

    result = Runtime(model, tools=ToolRegistry()).run("计算")

    assert result.status == "failed"
    assert result.error_code == "model_refusal"
    assert model.tool_schemas == [[], []]
    assert json.loads(model.calls[1][-1].content)["error"]["code"] == "unknown_tool"
    assert len(model.calls) == 2


def test_calculation_words_do_not_execute_tools_when_model_returns_text():
    executed = []
    model = ScriptedModel([ModelResponse(finish_reason="stop", content="脚本直接回答")])

    result = Runtime(model, tools=registry_with_calculator(lambda **kwargs: executed.append(kwargs))).run("计算0.1+0.2")

    assert result.status == "completed"
    assert executed == []
    assert len(model.calls) == 1
