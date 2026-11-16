"""千问适配器的离线契约测试；全部使用MockTransport，不产生任何外部请求。"""

import json

import httpx
import pytest

from min_agent.application import HarnessApplication
from min_agent.llm.client import LLMError
from min_agent.llm.qianwen import (
    DECLARED_LIMITS,
    QianwenCatalog,
    QianwenChatModel,
    QianwenSettings,
    build_messages,
    build_tools,
    parse_chat_completion,
)
from min_agent.contracts.models import Message, ModelResponse, ProviderState, ToolCall
from min_agent.runtime import Runtime


def completion(*, content="好的", calls=None, reason="stop", usage=None, reasoning=None):
    message = {"role": "assistant", "content": content}
    if calls is not None:
        message["tool_calls"] = calls
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    body = {"id": "chat-1", "object": "chat.completion",
            "choices": [{"index": 0, "message": message, "finish_reason": reason}]}
    if usage is not None:
        body["usage"] = usage
    return body


def function_call(call_id, name="calculator", arguments=None):
    item = {"type": "function", "function": {
        "name": name,
        "arguments": json.dumps(arguments or {"expression": "1+1"}, ensure_ascii=False),
    }}
    if call_id is not None:
        item["id"] = call_id
    return item


def settings(**options):
    return QianwenSettings(api_key="sk-test-key", **options)


def test_parse_plain_answer():
    result = parse_chat_completion(completion(content="你好"))

    assert result.finish_reason == "stop"
    assert result.content == "你好"
    assert result.tool_calls == []


def test_parse_tool_calls_keeps_order_and_arguments():
    result = parse_chat_completion(completion(content="", reason="tool_calls", calls=[
        function_call("call_a", arguments={"expression": "1+1"}),
        function_call("call_b", name="search", arguments={"query": "天气"}),
    ]))

    assert result.finish_reason == "tool_calls"
    assert [call.id for call in result.tool_calls] == ["call_a", "call_b"]
    assert [call.name for call in result.tool_calls] == ["calculator", "search"]
    assert result.tool_calls[1].arguments == {"query": "天气"}


def test_parse_truncated_response_does_not_expose_tool_calls():
    result = parse_chat_completion(completion(content="写到一半", reason="length", calls=[
        function_call("call_a"),
    ]))

    assert result.finish_reason == "length"
    assert result.tool_calls == []


def test_parse_filtered_response_is_refusal():
    result = parse_chat_completion(completion(content="", reason="content_filter"))

    assert result.finish_reason == "refusal"


@pytest.mark.parametrize("body", [
    {},
    {"choices": []},
    {"choices": [{"message": {"content": None, "tool_calls": [
        {"function": {"name": "calculator", "arguments": "not-json"}}]}, "finish_reason": "tool_calls"}]},
    {"error": {"message": "boom"}},
])
def test_parse_rejects_malformed_bodies(body):
    with pytest.raises(ValueError):
        parse_chat_completion(body)


def test_parse_empty_stop_is_error_not_success():
    result = parse_chat_completion(completion(content="   "))

    assert result.finish_reason == "error"


def test_reasoning_only_response_is_not_dropped():
    result = parse_chat_completion(completion(content="", reasoning="思考后的结论"))

    assert result.finish_reason == "stop"
    assert result.content == "思考后的结论"


def test_build_messages_pairs_tool_calls_with_results():
    messages = [
        Message(role="user", content="算一下"),
        Message(role="assistant", content="", tool_calls=[ToolCall(id="c1", name="calculator",
            arguments={"expression": "1+1"})]),
        Message(role="tool", content='{"ok": true, "result": "2"}', tool_call_id="c1"),
    ]

    converted = build_messages(messages)

    assert converted[0] == {"role": "user", "content": "算一下"}
    assert converted[1]["tool_calls"][0]["function"]["name"] == "calculator"
    assert json.loads(converted[1]["tool_calls"][0]["function"]["arguments"]) == {"expression": "1+1"}
    assert converted[2] == {"role": "tool", "tool_call_id": "c1",
                            "content": '{"ok": true, "result": "2"}'}


def test_build_messages_rejects_missing_or_duplicate_tool_results():
    orphan = [Message(role="tool", content="x", tool_call_id="missing")]
    with pytest.raises(ValueError):
        build_messages(orphan)

    call = Message(role="assistant", content="", tool_calls=[ToolCall(id="c1", name="f", arguments={})])
    duplicated = [call, Message(role="tool", content="a", tool_call_id="c1"),
                  Message(role="tool", content="b", tool_call_id="c1")]
    with pytest.raises(ValueError):
        build_messages(duplicated)

    unanswered = [call, Message(role="user", content="继续")]
    with pytest.raises(ValueError):
        build_messages(unanswered)


def test_build_messages_refuses_foreign_provider_state():
    message = Message(role="assistant", content="旧回答", provider_state=ProviderState(
        provider="deepseek.responses", model="deepseek-flash", response_model="m", output=[]))
    with pytest.raises(ValueError):
        build_messages([message])


def test_build_tools_maps_internal_schema():
    converted = build_tools([{"name": "calculator", "description": "算术",
                              "parameters": {"type": "object", "properties": {}}}])

    assert converted == [{"type": "function", "function": {
        "name": "calculator", "description": "算术",
        "parameters": {"type": "object", "properties": {}}}}]


def test_generate_records_usage_and_sends_expected_body():
    seen = {}

    def handler(request):
        seen["json"] = json.loads(request.content)
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json=completion(content="答案", usage={
            "prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}))

    model = QianwenChatModel(settings(), transport=httpx.MockTransport(handler))
    before = model.request_count
    response = model.generate([Message(role="user", content="问")],
                              tools=[{"name": "calculator", "description": "算术",
                                      "parameters": {"type": "object", "properties": {}}}])

    assert response.content == "答案"
    assert seen["json"]["model"] == "qwen3.8-flash"
    assert seen["json"]["stream"] is False
    assert seen["json"]["max_tokens"] == 4096
    assert seen["json"]["tools"][0]["type"] == "function"
    assert seen["auth"].startswith("Bearer ")
    assert model.request_count == before + 1
    assert model.usage[-1] == {"request": before + 1, "prompt_tokens": 11,
                               "completion_tokens": 7, "total_tokens": 18}


def test_generate_maps_http_402_without_leaking_body():
    secret = "仅测试的余额提示正文"

    def handler(request):
        return httpx.Response(402, json={"error": {"message": secret, "type": "insufficient_balance_error"}})

    model = QianwenChatModel(settings(), transport=httpx.MockTransport(handler))

    with pytest.raises(LLMError) as caught:
        model.generate([Message(role="user", content="问")])

    assert caught.value.code == "http_402"
    assert caught.value.diagnostics["http_status"] == 402
    assert secret not in str(caught.value)


def test_generate_maps_network_failure():
    def handler(request):
        raise httpx.ConnectError("boom")

    model = QianwenChatModel(settings(), transport=httpx.MockTransport(handler))

    with pytest.raises(LLMError) as caught:
        model.generate([Message(role="user", content="问")])

    assert caught.value.code == "network_error"


def test_settings_reject_unknown_model_and_excess_output():
    with pytest.raises(ValueError):
        # qwen3.8-flash 声明输出上限 32768；40000 必须在构造期被拒。
        QianwenSettings(api_key="k", max_output_tokens=40000)
    with pytest.raises(ValueError):
        QianwenSettings(api_key="k", model="gpt-4o")
    with pytest.raises(ValueError):
        QianwenCatalog(lambda model: settings(model=model), models=("gpt-4o",))


def test_context_limits_are_declared_values():
    model = QianwenChatModel(settings(model="qwen3.7-plus"))
    limits = model.context_limits()

    assert (limits.context_window, limits.max_output_tokens) == DECLARED_LIMITS["qwen3.7-plus"]
    assert limits.source == "declared:qianwen-table"


def test_catalog_rejects_unknown_model_before_any_instance():
    catalog = QianwenCatalog(lambda model: settings(model=model))

    with pytest.raises(ValueError):
        catalog.validate("gpt-4o")
    with pytest.raises(ValueError):
        catalog.model_for("gpt-4o")
    assert catalog.model_for() is catalog.model_for(catalog.default_model)


def test_runtime_surfaces_provider_code_but_keeps_fixed_trace_code():
    class Failing:
        def generate(self, messages):
            raise LLMError("http_402")

    runtime = Runtime(Failing())
    result = runtime.run("输入")

    assert result.status == "failed"
    assert result.error_code == "http_402"
    assert [event.error_code for event in runtime.trace.events] == ["model_call_failed"] * 2


def test_runtime_keeps_generic_exception_code():
    class Failing:
        def generate(self, messages):
            raise RuntimeError("私密正文")

    runtime = Runtime(Failing())
    result = runtime.run("输入")

    assert result.error_code == "model_call_failed"


class _FakeModel:
    def __init__(self, name):
        self.name = name
        self._settings = type("S", (), {"model": name})()

    def generate(self, messages, *, tools=None):
        return ModelResponse(finish_reason="stop", content=self.name)


def test_session_model_binding_isolated_between_sessions(tmp_path):
    catalog = QianwenCatalog(lambda model: settings(model=model))
    harness = HarnessApplication(tmp_path / "qwen-selector.sqlite3", model=_FakeModel("qwen3.8-flash"),
                                 model_catalog=catalog)
    try:
        first = harness.create_session("A")["session_id"]
        second = harness.create_session("B")["session_id"]

        assert harness.snapshot(first)["model"]["current"] == "qwen3.8-flash"
        harness.set_model(first, "qwen3.8-max")
        harness.set_model(second, "qwen3.7-plus")

        assert harness.model_factory(harness.context(first))._settings.model == "qwen3.8-max"
        assert harness.model_factory(harness.context(second))._settings.model == "qwen3.7-plus"
        assert harness.snapshot(first)["model"]["current"] == "qwen3.8-max"
        assert harness.snapshot(second)["model"]["current"] == "qwen3.7-plus"
    finally:
        harness.close()


def test_switching_model_while_running_is_refused(tmp_path):
    catalog = QianwenCatalog(lambda model: settings(model=model))
    harness = HarnessApplication(tmp_path / "qwen-selector-running.sqlite3", model=_FakeModel("qwen3.8-flash"),
                                 model_catalog=catalog)
    try:
        identity = harness.create_session("A")["session_id"]
        harness._running[identity] = object()
        with pytest.raises(RuntimeError):
            harness.set_model(identity, "qwen3.8-max")
        assert harness.snapshot(identity)["model"]["current"] == "qwen3.8-flash"
    finally:
        harness._running.clear()
        harness.close()


def test_unknown_model_is_refused_without_creating_instance(tmp_path):
    catalog = QianwenCatalog(lambda model: settings(model=model))
    harness = HarnessApplication(tmp_path / "qwen-selector-unknown.sqlite3", model=_FakeModel("qwen3.8-flash"),
                                 model_catalog=catalog)
    try:
        identity = harness.create_session("A")["session_id"]
        with pytest.raises(ValueError):
            harness.set_model(identity, "gpt-4o")
        assert catalog._instances == {}
    finally:
        harness.close()