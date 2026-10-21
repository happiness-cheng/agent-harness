import json
from dataclasses import replace

import pytest
from pydantic import BaseModel, ConfigDict, field_validator

from min_agent.models import ModelResponse, ToolCall
from min_agent.runtime import Runtime
from min_agent.tools.calculator import CALCULATOR_TOOL
from min_agent.tools.registry import ToolRegistry
from tests.fakes.model import ScriptedModel


def call(call_id, expression="1+2", name="calculator"):
    return ToolCall(id=call_id, name=name, arguments={"expression": expression})


def request(*calls):
    return ModelResponse(finish_reason="tool_calls", tool_calls=list(calls))


def calculator_registry():
    registry = ToolRegistry()
    registry.register(CALCULATOR_TOOL)
    return registry


def test_two_calculator_calls_produce_two_ordered_results_before_followup():
    first, second = call("a", "0.1+0.2"), call("b", "(2+3)*4")
    model = ScriptedModel([request(first, second), ModelResponse(finish_reason="stop", content="0.3和20")])

    result = Runtime(model, tools=calculator_registry()).run("分别计算")

    assert result.status == "completed"
    assert len(model.calls) == 2
    assert [message.role for message in model.calls[1]] == ["user", "assistant", "tool", "tool"]
    assert model.calls[1][1].tool_calls == [first, second]
    assert [(m.tool_call_id, json.loads(m.content)) for m in model.calls[1][2:]] == [
        ("a", {"ok": True, "result": "0.3"}), ("b", {"ok": True, "result": "20"}),
    ]


@pytest.mark.parametrize("problem", ["unknown_tool", "wrong_type", "missing", "extra", "blank"])
def test_request_error_is_returned_by_id_and_model_can_submit_corrected_call(problem):
    invalid = call("bad")
    if problem == "unknown_tool":
        invalid.name = "not_registered"
    else:
        invalid.arguments = {
            "wrong_type": {"expression": 123}, "missing": {},
            "extra": {"expression": "1+2", "unexpected": True}, "blank": {"expression": " "},
        }[problem]
    model = ScriptedModel([
        request(invalid), request(call("fixed", "2+3")),
        ModelResponse(finish_reason="stop", content="修正后是5"),
    ])

    result = Runtime(model, tools=calculator_registry()).run("计算")

    assert result.status == "completed"
    assert len(model.calls) == 3
    error_message = model.calls[1][-1]
    assert error_message.role == "tool"
    assert error_message.tool_call_id == "bad"
    error_result = json.loads(error_message.content)
    assert error_result["ok"] is False
    error = error_result["error"]
    assert error["code"] == ("unknown_tool" if problem == "unknown_tool" else "invalid_arguments")
    assert error["execution_state"] == "not_started"
    assert error["recovery"] == "correct_request_with_new_id"
    if problem == "unknown_tool":
        assert error["available_tools"] == ["calculator"]
    else:
        assert error["expected_schema"] == CALCULATOR_TOOL.parameters.model_json_schema()
        assert error["issues"][0]["field"] == ("<extra>" if problem == "extra" else "expression")
    assert model.calls[2][-1].tool_call_id == "fixed"
    assert json.loads(model.calls[2][-1].content) == {"ok": True, "result": "5"}


def test_registry_execute_validates_before_handler_and_returns_json_compatible_result():
    executed = []
    registry = ToolRegistry()
    registry.register(replace(CALCULATOR_TOOL, handler=lambda **kwargs: executed.append(kwargs) or "3"))

    invalid = registry.execute(ToolCall(id="bad", name="calculator", arguments={"expression": 123}))
    assert invalid["ok"] is False
    assert executed == []
    success = registry.execute(call("ok"))

    assert success == {"ok": True, "result": "3"}
    assert executed == [{"expression": "1+2"}]
    json.dumps(invalid, allow_nan=False)


def test_mixed_batch_keeps_all_results_and_executes_valid_tools_serially():
    executed = []

    def handler(label):
        def calculate(expression):
            executed.append((label, expression))
            return CALCULATOR_TOOL.handler(expression=expression)
        return calculate

    registry = ToolRegistry()
    registry.register(replace(CALCULATOR_TOOL, handler=handler("first")))
    registry.register(replace(CALCULATOR_TOOL, name="other", handler=handler("second")))
    invalid = ToolCall(id="bad", name="calculator", arguments={})
    model = ScriptedModel([
        request(call("a"), call("missing", name="unknown"), invalid, call("b", "2+2", "other")),
        ModelResponse(finish_reason="refusal", content="两项错误尚未修正"),
    ])

    result = Runtime(model, tools=registry).run("执行这些计算")

    assert result.status == "failed"
    assert result.error_code == "model_refusal"
    assert executed == [("first", "1+2"), ("second", "2+2")]
    assert [m.tool_call_id for m in model.calls[1][2:]] == ["a", "missing", "bad", "b"]
    assert [json.loads(m.content)["ok"] for m in model.calls[1][2:]] == [True, False, False, True]
    assert len(model.calls[1][1].tool_calls) == 4


def test_default_repair_limit_allows_two_error_followups_then_stops():
    model = ScriptedModel([request(call(str(i), name="missing")) for i in range(4)])

    result = Runtime(model, tools=calculator_registry()).run("输入")

    assert result.status == "partial"
    assert result.error_code == "tool_repair_limit"
    assert len(model.calls) == 3


@pytest.mark.parametrize("limit", [-1, True, False, 1.5, "2", None])
def test_invalid_repair_limit_is_rejected_before_model_calls(limit):
    model = ScriptedModel([])

    with pytest.raises((TypeError, ValueError)):
        Runtime(model, tools=calculator_registry(), max_tool_error_rounds=limit)

    assert model.calls == []


def test_zero_repair_limit_does_not_block_success_but_stops_first_request_error():
    good = ScriptedModel([request(call("good")), ModelResponse(finish_reason="stop", content="3")])
    bad = ScriptedModel([request(call("bad", name="missing"))])

    assert Runtime(good, tools=calculator_registry(), max_tool_error_rounds=0).run("输入").status == "completed"
    result = Runtime(bad, tools=calculator_registry(), max_tool_error_rounds=0).run("输入")

    assert result.status == "partial"
    assert result.error_code == "tool_repair_limit"
    assert len(good.calls) == 2
    assert len(bad.calls) == 1


def test_successful_batch_does_not_reset_total_error_repair_count():
    model = ScriptedModel([
        request(call("bad-1", name="missing")), request(call("good")),
        request(call("bad-2", name="missing")), ModelResponse(finish_reason="stop", content="不应继续"),
    ])

    result = Runtime(model, tools=calculator_registry(), max_tool_error_rounds=1).run("输入")

    assert result.status == "partial"
    assert result.error_code == "tool_repair_limit"
    assert len(model.calls) == 3


def test_many_request_errors_in_one_batch_count_as_one_repair_round():
    model = ScriptedModel([
        request(call("bad-1", name="missing"), call("bad-2", name="missing")),
        request(call("fixed-1"), call("fixed-2", "2+2")),
        ModelResponse(finish_reason="stop", content="3和4"),
    ])

    result = Runtime(model, tools=calculator_registry(), max_tool_error_rounds=1).run("输入")

    assert result.status == "completed"
    assert len(model.calls) == 3
    assert len(model.calls[1]) == 4


def test_stop_after_unrepaired_request_error_does_not_claim_completion():
    model = ScriptedModel([
        request(call("bad", name="missing")), ModelResponse(finish_reason="stop", content="已经完成"),
    ])

    result = Runtime(model, tools=calculator_registry()).run("输入")

    assert result.status == "partial"
    assert result.error_code == "tool_request_failed"
    assert result.response.content == "已经完成"
    assert len(model.calls) == 2


def test_duplicate_id_anywhere_in_next_batch_prevents_all_new_executions():
    executed = []
    registry = ToolRegistry()
    registry.register(replace(CALCULATOR_TOOL, handler=lambda **kwargs: executed.append(kwargs) or "3"))
    model = ScriptedModel([request(call("used")), request(call("new"), call("used"))])

    result = Runtime(model, tools=registry).run("输入")

    assert result.status == "failed"
    assert result.error_code == "duplicate_tool_call_id"
    assert executed == [{"expression": "1+2"}]
    assert len(model.calls) == 2


def test_handler_exception_mid_batch_stops_remaining_calls_without_replaying_success():
    executed = []

    def handler(expression):
        executed.append(expression)
        if expression == "fail":
            raise RuntimeError("仅测试的执行中异常正文")
        return "3"

    registry = ToolRegistry()
    registry.register(replace(CALCULATOR_TOOL, handler=handler))
    model = ScriptedModel([request(call("a"), call("b", "fail"), call("c", "4+5"))])

    result = Runtime(model, tools=registry).run("输入")

    assert result.status == "failed"
    assert result.error_code == "tool_execution_failed"
    assert executed == ["1+2", "fail"]
    assert "仅测试的执行中异常正文" not in repr(result)
    assert len(model.calls) == 1


def test_validation_error_does_not_echo_input_unknown_field_names_or_custom_exception_text():
    private = "仅测试的私密校验内容"

    class Arguments(BaseModel):
        model_config = ConfigDict(strict=True, extra="forbid")
        expression: str

        @field_validator("expression")
        @classmethod
        def reject(cls, value):
            raise ValueError(private + value)

    executed = []
    registry = ToolRegistry()
    registry.register(replace(CALCULATOR_TOOL, parameters=Arguments, handler=lambda **kwargs: executed.append(kwargs)))
    bad = ToolCall(id="bad", name="calculator", arguments={"expression": private, private: private})
    model = ScriptedModel([request(bad), ModelResponse(finish_reason="refusal", content="需要修正")])

    result = Runtime(model, tools=registry).run("输入")

    assert result.status == "failed"
    error_text = model.calls[1][-1].content
    assert private not in error_text
    assert {issue["field"] for issue in json.loads(error_text)["error"]["issues"]} == {"expression", "<extra>"}
    assert executed == []


def test_corrected_request_must_use_new_id_even_if_original_did_not_execute():
    executed = []
    registry = ToolRegistry()
    registry.register(replace(CALCULATOR_TOOL, handler=lambda **kwargs: executed.append(kwargs) or "3"))
    model = ScriptedModel([request(call("same", name="missing")), request(call("same"))])

    result = Runtime(model, tools=registry).run("输入")

    assert result.status == "failed"
    assert result.error_code == "duplicate_tool_call_id"
    assert executed == []
    assert len(model.calls) == 2


def test_repair_count_and_pending_error_state_are_separate_for_each_run():
    script = [request(call("bad", name="missing")), request(call("fixed")),
              ModelResponse(finish_reason="stop", content="3")]
    model = ScriptedModel(script + script)
    runtime = Runtime(model, tools=calculator_registry(), max_tool_error_rounds=1)

    assert runtime.run("第一次").status == "completed"
    assert runtime.run("第二次").status == "completed"
    assert len(model.calls) == 6
    assert len(model.calls[3]) == 1
    assert model.calls[3][0].content == "第二次"


def test_duplicate_ids_in_mutated_response_are_rejected_before_any_execution():
    executed = []
    registry = ToolRegistry()
    registry.register(replace(CALCULATOR_TOOL, handler=lambda **kwargs: executed.append(kwargs) or "3"))
    response = request(call("same"))
    response.tool_calls.append(call("same"))
    model = ScriptedModel([response])

    result = Runtime(model, tools=registry).run("输入")

    assert result.status == "failed"
    assert result.error_code == "invalid_model_response"
    assert executed == []


def test_validation_error_raised_inside_handler_is_execution_failure():
    def handler(expression):
        CALCULATOR_TOOL.parameters(expression=123)

    registry = ToolRegistry()
    registry.register(replace(CALCULATOR_TOOL, handler=handler))
    model = ScriptedModel([request(call("started"))])

    result = Runtime(model, tools=registry).run("输入")

    assert result.status == "failed"
    assert result.error_code == "tool_execution_failed"
    assert len(model.calls) == 1


def test_repair_limit_is_checked_after_finishing_current_batch():
    executed = []
    registry = ToolRegistry()
    registry.register(replace(CALCULATOR_TOOL, handler=lambda **kwargs: executed.append(kwargs) or "3"))
    model = ScriptedModel([request(call("bad", name="missing"), call("good"))])

    result = Runtime(model, tools=registry, max_tool_error_rounds=0).run("输入")

    assert result.status == "partial"
    assert result.error_code == "tool_repair_limit"
    assert executed == [{"expression": "1+2"}]
    assert len(model.calls) == 1
