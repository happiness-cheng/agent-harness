import pytest
from pydantic import ValidationError

from min_agent.models import Message, ModelResponse, ToolCall


@pytest.fixture
def calculator_call():
    return ToolCall(id="call-1", name="calculator", arguments={"expression": "1+2"})


@pytest.mark.parametrize("role", ["system", "user", "assistant", "tool"])
def test_message_preserves_role_content_and_tool_result_id(role):
    call_id = "call-1" if role == "tool" else None

    message = Message(role=role, content="  原文保持不变  ", tool_call_id=call_id)

    assert message.role == role
    assert message.content == "  原文保持不变  "
    assert message.tool_call_id == call_id


@pytest.mark.parametrize(
    "payload",
    [
        {"content": "你好"},
        {"role": "user"},
        {"role": "unknown", "content": "你好"},
        {"role": "user", "content": 123},
        {"role": "tool", "content": "3"},
        {"role": "tool", "content": "3", "tool_call_id": " "},
        {"role": "user", "content": "你好", "tool_call_id": "call-1"},
        {"role": "user", "content": "你好", "session_id": "other-session"},
    ],
)
def test_message_rejects_missing_invalid_or_unexpected_fields(payload):
    with pytest.raises(ValidationError):
        Message.model_validate(payload)


def test_tool_call_preserves_json_arguments_without_evaluating_them():
    arguments = {"items": [1, "2", True, None, {"value": 1.5}]}

    call = ToolCall(id="call-1", name="calculator", arguments=arguments)

    assert call.arguments == arguments


@pytest.mark.parametrize(
    "changes",
    [
        {"id": ""},
        {"id": " "},
        {"name": " "},
        {"arguments": '{"expression":"1+2"}'},
        {"arguments": []},
        {"arguments": {"value": object()}},
        {"arguments": {"value": float("nan")}},
        {"arguments": {"nested": [float("inf")]}},
        {"unexpected": "value"},
    ],
)
def test_tool_call_rejects_invalid_identifiers_and_non_json_objects(changes):
    payload = {"id": "call-1", "name": "calculator", "arguments": {}}
    payload.update(changes)

    with pytest.raises(ValidationError):
        ToolCall.model_validate(payload)


@pytest.mark.parametrize("missing", ["id", "name", "arguments"])
def test_tool_call_requires_all_fields(missing):
    payload = {"id": "call-1", "name": "calculator", "arguments": {}}
    payload.pop(missing)

    with pytest.raises(ValidationError):
        ToolCall.model_validate(payload)


def test_completed_text_response_has_no_tool_calls():
    response = ModelResponse(finish_reason="stop", content="答案是 3")

    assert response.content == "答案是 3"
    assert response.finish_reason == "stop"
    assert response.tool_calls == []
    assert response.reasoning_summary is None


def test_tool_response_can_include_text_multiple_calls_and_public_summary(calculator_call):
    second = ToolCall(id="call-2", name="search", arguments={"query": "Python"})

    response = ModelResponse(
        finish_reason="tool_calls",
        content="先计算，再查资料。",
        tool_calls=[calculator_call, second],
        reasoning_summary="需要两项证据。",
    )

    assert response.content == "先计算，再查资料。"
    assert [call.id for call in response.tool_calls] == ["call-1", "call-2"]
    assert response.reasoning_summary == "需要两项证据。"


@pytest.mark.parametrize("reason", ["length", "refusal", "error"])
def test_non_success_reason_preserves_partial_output(reason, calculator_call):
    response = ModelResponse(
        finish_reason=reason, content="未完成内容", tool_calls=[calculator_call]
    )

    assert response.finish_reason == reason
    assert response.content == "未完成内容"
    assert response.tool_calls == [calculator_call]


@pytest.mark.parametrize(
    "payload",
    [
        {"content": "缺少结束原因"},
        {"finish_reason": "unknown", "content": "内容"},
        {"finish_reason": "stop"},
        {"finish_reason": "stop", "content": " "},
        {"finish_reason": "tool_calls"},
        {"finish_reason": "stop", "content": 123},
        {"finish_reason": "stop", "content": "内容", "reasoning_summary": 123},
        {"finish_reason": "stop", "content": "内容", "hidden_thoughts": "额外字段"},
    ],
)
def test_response_rejects_invalid_or_ambiguous_payload(payload):
    with pytest.raises(ValidationError):
        ModelResponse.model_validate(payload)


def test_stop_cannot_hide_a_tool_request(calculator_call):
    with pytest.raises(ValidationError):
        ModelResponse(finish_reason="stop", content="完成", tool_calls=[calculator_call])


def test_duplicate_tool_call_ids_are_rejected(calculator_call):
    with pytest.raises(ValidationError):
        ModelResponse(finish_reason="tool_calls", tool_calls=[calculator_call, calculator_call])


def test_response_default_lists_do_not_share_state(calculator_call):
    first = ModelResponse(finish_reason="length")
    second = ModelResponse(finish_reason="length")

    first.tool_calls.append(calculator_call)

    assert second.tool_calls == []


def test_response_json_round_trip_keeps_call_identity_and_arguments(calculator_call):
    response = ModelResponse(finish_reason="tool_calls", tool_calls=[calculator_call])

    restored = ModelResponse.model_validate_json(response.model_dump_json())

    assert restored == response
    assert restored.tool_calls[0].id == "call-1"
    assert restored.tool_calls[0].arguments == {"expression": "1+2"}
