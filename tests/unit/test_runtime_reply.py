import pytest

from min_agent.models import Message, ModelResponse, ToolCall
from min_agent.runtime import Runtime
from tests.fakes.model import ScriptedModel


def test_direct_reply_calls_model_once_and_preserves_input_and_answer():
    response = ModelResponse(finish_reason="stop", content="  你好，这是回答。  ")
    model = ScriptedModel([response])

    result = Runtime(model).run("  请介绍自己。  ")

    assert result.status == "completed"
    assert result.response == response
    assert result.error_code is None
    assert model.calls == [[Message(role="user", content="  请介绍自己。  ")]]


@pytest.mark.parametrize(
    ("response", "status", "error_code"),
    [
        (ModelResponse(finish_reason="length", content="尚未写完"), "partial", "output_limit"),
        (ModelResponse(finish_reason="length"), "partial", "output_limit"),
        (ModelResponse(finish_reason="refusal", content="无法回答"), "failed", "model_refusal"),
        (ModelResponse(finish_reason="error", content="部分输出"), "failed", "model_error"),
        (
            ModelResponse(
                finish_reason="tool_calls",
                content="需要计算",
                tool_calls=[ToolCall(id="c1", name="calculator", arguments={"expression": "1/0"})],
            ),
            "partial",
            "tool_execution_not_supported",
        ),
        (
            ModelResponse(
                finish_reason="length",
                tool_calls=[ToolCall(id="c2", name="calculator", arguments={"expression": "2+2"})],
            ),
            "partial",
            "output_limit",
        ),
    ],
)
def test_non_stop_response_preserves_output_without_claiming_completion(response, status, error_code):
    model = ScriptedModel([response])

    result = Runtime(model).run("请处理")

    assert result.status == status
    assert result.error_code == error_code
    assert result.response == response
    assert len(model.calls) == 1


@pytest.mark.parametrize("exception_type", [TimeoutError, ConnectionError, RuntimeError])
def test_model_exception_returns_safe_failure_without_retry(exception_type):
    sensitive_detail = "仅用于测试的私密诊断正文"
    model = ScriptedModel([exception_type(sensitive_detail)])

    result = Runtime(model).run("你好")

    assert result.status == "failed"
    assert result.error_code == "model_call_failed"
    assert result.response is None
    assert sensitive_detail not in repr(result)
    assert len(model.calls) == 1


@pytest.mark.parametrize("text", ["", " ", "\n\t"])
def test_blank_input_is_rejected_before_calling_model(text):
    model = ScriptedModel([])

    with pytest.raises(ValueError):
        Runtime(model).run(text)

    assert model.calls == []


@pytest.mark.parametrize("text", [None, 123, True, ["你好"]])
def test_non_string_input_is_rejected_before_calling_model(text):
    model = ScriptedModel([])

    with pytest.raises(TypeError):
        Runtime(model).run(text)

    assert model.calls == []


def test_process_interrupt_is_not_converted_to_model_failure():
    model = ScriptedModel([KeyboardInterrupt()])

    with pytest.raises(KeyboardInterrupt):
        Runtime(model).run("你好")

    assert len(model.calls) == 1


def test_repeated_runs_have_independent_inputs_and_follow_script_not_keywords():
    model = ScriptedModel([
        ModelResponse(finish_reason="stop", content="脚本第一项"),
        ModelResponse(finish_reason="stop", content="脚本第二项"),
    ])
    runtime = Runtime(model)

    first = runtime.run("请计算 1+1")
    second = runtime.run("完全相同的词也不会决定脚本结果")

    assert first.response.content == "脚本第一项"
    assert second.response.content == "脚本第二项"
    assert model.calls == [
        [Message(role="user", content="请计算 1+1")],
        [Message(role="user", content="完全相同的词也不会决定脚本结果")],
    ]


@pytest.mark.parametrize("response", [None, "回答", {"finish_reason": "stop", "content": "回答"}])
def test_adapter_must_return_model_response_not_an_unvalidated_value(response):
    class InvalidResponseModel:
        def generate(self, messages):
            return response

    result = Runtime(InvalidResponseModel()).run("你好")

    assert result.status == "failed"
    assert result.error_code == "invalid_model_response"
    assert result.response is None


@pytest.mark.parametrize("mutation", ["blank_stop", "wrong_content_type", "empty_call_id", "nonfinite_argument"])
def test_mutated_invalid_response_is_revalidated_at_runtime_boundary(mutation):
    if mutation in {"blank_stop", "wrong_content_type"}:
        response = ModelResponse(finish_reason="stop", content="原来合法")
        response.content = " " if mutation == "blank_stop" else 123
    else:
        response = ModelResponse(
            finish_reason="tool_calls",
            tool_calls=[ToolCall(id="c1", name="calculator", arguments={"expression": "1+1"})],
        )
        if mutation == "empty_call_id":
            response.tool_calls[0].id = ""
        else:
            response.tool_calls[0].arguments["value"] = float("inf")
    model = ScriptedModel([response])

    result = Runtime(model).run("你好")

    assert result.status == "failed"
    assert result.error_code == "invalid_model_response"
    assert result.response is None
    assert len(model.calls) == 1


def test_result_is_a_deep_snapshot_of_adapter_response():
    response = ModelResponse(
        finish_reason="tool_calls",
        tool_calls=[ToolCall(id="c1", name="example", arguments={"items": [1]})],
        reasoning_summary="模型公开摘要",
    )
    model = ScriptedModel([response])

    result = Runtime(model).run("你好")
    response.tool_calls[0].arguments["items"].append(2)
    response.reasoning_summary = "已修改"

    assert result.response.tool_calls[0].arguments == {"items": [1]}
    assert result.response.reasoning_summary == "模型公开摘要"
    result.response.tool_calls[0].arguments["items"].append(3)
    assert response.tool_calls[0].arguments == {"items": [1, 2]}
