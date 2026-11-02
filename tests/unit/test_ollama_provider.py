"""Ollama适配器的离线协议测试；全部使用MockTransport，不依赖本机服务。"""

import json

import httpx
import pytest

from min_agent.llm.client import LLMError
from min_agent.llm.ollama import (
    OllamaModel,
    OllamaSettings,
    available_models,
    build_messages,
    build_tools,
    normalize_base_url,
    parse_chat_response,
    require_model,
)
from min_agent.models import Message, ProviderState, ToolCall
from min_agent.runtime import Runtime
from min_agent.tools.calculator import CALCULATOR_TOOL
from min_agent.tools.registry import ToolDefinition, ToolRegistry


def chat_response(*, content="", calls=None, reason="stop", done=True, prompt=4094, evaluated=12):
    message = {"role": "assistant", "content": content}
    if calls is not None:
        message["tool_calls"] = calls
    return {
        "model": "qwen3.5:4b", "message": message, "done": done, "done_reason": reason,
        "prompt_eval_count": prompt, "eval_count": evaluated,
        "total_duration": 1_000_000_000, "load_duration": 0,
        "prompt_eval_duration": 1_000_000, "eval_duration": 1_000_000,
    }


def function_call(call_id, name="calculator", arguments=None, **extra):
    item = {"function": {"name": name, "arguments": arguments if arguments is not None else {"expression": "1+1"}}}
    if call_id is not None:
        item["id"] = call_id
    item.update(extra)
    return item


def model_with(handler, **options):
    settings = OllamaSettings(**options)
    return OllamaModel(settings, transport=httpx.MockTransport(handler))


def calculator_registry(calls=None):
    def tracked(expression):
        if calls is not None:
            calls.append(expression)
        return "703" if expression.replace(" ", "") == "37*19" else "0"

    registry = ToolRegistry()
    registry.register(ToolDefinition("calculator", CALCULATOR_TOOL.description,
                                    CALCULATOR_TOOL.parameters, tracked))
    return registry


# T01 普通文本消息转换
def test_text_roles_are_mapped_in_order():
    messages = [
        Message(role="system", content="系统指令"),
        Message(role="user", content="你好"),
        Message(role="assistant", content="在"),
        Message(role="user", content="继续"),
    ]
    assert build_messages(messages) == [
        {"role": "system", "content": "系统指令"},
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "在"},
        {"role": "user", "content": "继续"},
    ]


def test_foreign_provider_state_is_not_forwarded_to_ollama():
    state = ProviderState(provider="openai.responses", model="gpt-6.1-sol",
                          response_model="gpt-6.1-sol", output=[{"type": "message"}])
    message = Message(role="assistant", content="原回答", provider_state=state)
    assert build_messages([message]) == [{"role": "assistant", "content": "原回答"}]
    with model_with(lambda request: httpx.Response(200, json=chat_response(content="好"))) as model:
        model.generate([message])
        body = model._build_body([message], None)
        assert body["messages"] == [{"role": "assistant", "content": "原回答"}]
        assert "provider_state" not in body["messages"][0]


def test_provider_posts_native_chat_request_without_credentials_or_headers():
    captured = []

    def handle(request):
        captured.append(request)
        return httpx.Response(200, json=chat_response(content="连接成功"))

    with model_with(handle) as model:
        response = model.generate([Message(role="user", content="只回复连接成功")])

    assert response.finish_reason == "stop" and response.content == "连接成功"
    request = captured[0]
    assert str(request.url) == "http://127.0.0.1:11434/api/chat"
    assert request.method == "POST"
    assert "authorization" not in {name.lower() for name in request.headers}
    body = json.loads(request.content)
    assert body["model"] == "qwen3.5:4b"
    assert body["stream"] is False and body["think"] is False
    assert body["keep_alive"] == "30m"
    assert body["options"] == {"num_ctx": 32768, "num_predict": 1024, "temperature": 0.0}
    assert "tools" not in body


# T02 工具Schema传递
def test_tool_schema_keeps_name_parameters_and_required():
    captured = {}

    def handle(request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=chat_response(content="好"))

    tools = ToolRegistry()
    tools.register(CALCULATOR_TOOL)
    with model_with(handle) as model:
        model.generate([Message(role="user", content="算")], tools=tools.schemas())

    entry = captured["tools"][0]
    assert entry["type"] == "function"
    assert entry["function"]["name"] == "calculator"
    assert entry["function"]["description"] == CALCULATOR_TOOL.description
    schema = entry["function"]["parameters"]
    assert schema["type"] == "object"
    assert schema["required"] == ["expression"]
    assert schema["properties"]["expression"]["type"] == "string"


@pytest.mark.parametrize("tool", [
    {"name": "bad name", "description": "x", "parameters": {"type": "object"}},
    {"name": "dup", "description": "", "parameters": {"type": "object"}},
    {"name": "bad", "description": "x", "parameters": {"type": "array"}},
    {"name": "extra", "description": "x", "parameters": {"type": "object"}, "handler": "no"},
])
def test_invalid_tool_schemas_are_rejected(tool):
    with pytest.raises((ValueError, TypeError)):
        build_tools([tool])


# T03 单工具调用
def test_single_tool_call_keeps_structured_arguments():
    raw = chat_response(calls=[function_call("call_abc")])
    response = parse_chat_response(raw)
    assert response.finish_reason == "tool_calls"
    assert response.content is None
    assert response.tool_calls == [ToolCall(id="call_abc", name="calculator", arguments={"expression": "1+1"})]


def test_string_encoded_arguments_are_parsed_as_strict_json():
    raw = chat_response(calls=[function_call("call_json", arguments='{"expression": "37*19"}')])
    calls = parse_chat_response(raw).tool_calls
    assert calls[0].arguments == {"expression": "37*19"}


# T04 多工具调用
def test_multiple_tool_calls_keep_order_and_unique_ids():
    raw = chat_response(calls=[function_call("call_a", arguments={"expression": "1+1"}),
                               function_call("call_b", arguments={"expression": "2+2"})])
    response = parse_chat_response(raw)
    assert response.finish_reason == "tool_calls"
    assert [call.id for call in response.tool_calls] == ["call_a", "call_b"]
    assert [call.arguments for call in response.tool_calls] == [{"expression": "1+1"}, {"expression": "2+2"}]


def test_duplicate_returned_ids_inside_one_response_are_made_unique():
    raw = chat_response(calls=[function_call("call_same"), function_call("call_same")])
    calls = parse_chat_response(raw).tool_calls
    assert calls[0].id == "call_same"
    assert len({call.id for call in calls}) == 2
    assert calls[1].id.startswith("ollama-")


# T05 工具结果回传
def test_tool_result_is_mapped_to_tool_name_from_history():
    history = [
        Message(role="assistant", content="", tool_calls=[
            ToolCall(id="call_1", name="calculator", arguments={"expression": "1+1"}),
        ]),
        Message(role="tool", tool_call_id="call_1", content='{"ok": true, "result": "2"}'),
        Message(role="user", content="继续"),
    ]
    converted = build_messages(history)
    assert converted[0]["tool_calls"] == [
        {"type": "function", "function": {"name": "calculator", "arguments": {"expression": "1+1"}}},
    ]
    assert converted[1] == {"role": "tool", "content": '{"ok": true, "result": "2"}', "tool_name": "calculator"}
    assert converted[2] == {"role": "user", "content": "继续"}


def test_second_round_request_maps_tool_result_after_real_runtime_execution():
    bodies = []

    def handle(request):
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            return httpx.Response(200, json=chat_response(calls=[function_call("call_math", arguments={"expression": "37*19"})]))
        return httpx.Response(200, json=chat_response(content="结果是703"))

    executed = []
    with model_with(handle) as model:
        result = Runtime(model, tools=calculator_registry(executed)).run("用工具计算37*19")
    assert result.status == "completed" and executed == ["37*19"]
    tool_message = bodies[1]["messages"][-1]
    assert tool_message["role"] == "tool" and tool_message["tool_name"] == "calculator"
    assert "tool_call_id" not in tool_message
    assert json.loads(tool_message["content"])["ok"] is True


@pytest.mark.parametrize("history", [
    [Message(role="tool", tool_call_id="missing", content="x")],
    [Message(role="assistant", content="", tool_calls=[ToolCall(id="call_1", name="calculator", arguments={})]),
     Message(role="tool", tool_call_id="call_1", content="一"),
     Message(role="tool", tool_call_id="call_1", content="二")],
    [Message(role="assistant", content="", tool_calls=[ToolCall(id="call_1", name="calculator", arguments={})]),
     Message(role="user", content="还没回传结果")],
])
def test_unknown_duplicate_or_missing_tool_results_fail_safely(history):
    with pytest.raises(ValueError):
        build_messages(history)


# T06 缺少原生工具ID
def test_missing_native_id_generates_persistable_unique_internal_ids():
    raw = chat_response(calls=[function_call(None), function_call(None)])
    first = parse_chat_response(raw)
    second = parse_chat_response(raw)
    ids = [call.id for call in first.tool_calls] + [call.id for call in second.tool_calls]
    assert all(call_id.startswith("ollama-") for call_id in ids)
    assert len(set(ids)) == 4
    persisted = ToolCall.model_validate(first.tool_calls[0].model_dump())
    assert persisted.id == first.tool_calls[0].id


# T07 普通最终答案
def test_plain_final_answer_maps_to_stop():
    response = parse_chat_response(chat_response(content="这是最终回答"))
    assert response.finish_reason == "stop" and response.content == "这是最终回答"
    assert response.tool_calls == [] and response.provider_state is None


# T08 空响应/错误状态
def test_empty_answer_is_not_reported_as_success():
    assert parse_chat_response(chat_response(content="   ")).finish_reason == "error"
    with model_with(lambda request: httpx.Response(200, json=chat_response(content="")) ) as model:
        result = Runtime(model).run("测试空响应")
    assert result.status == "failed" and result.error_code == "model_error"


@pytest.mark.parametrize("payload", [
    b"not json",
    b'{"message": "x", "done": true}',
    b'{"message": {"content": "x"}, "done": false, "done_reason": "stop"}',
    b'{"message": {"content": "x", "tool_calls": "x"}, "done": true}',
    b'{"error": "private-detail"}',
])
def test_malformed_or_error_payloads_never_become_success(payload):
    with model_with(lambda request: httpx.Response(200, content=payload)) as model:
        with pytest.raises(LLMError) as error:
            model.generate([Message(role="user", content="测试")])
    assert error.value.code in {"invalid_response", "invalid_request"}
    assert "private-detail" not in str(error.value)


def test_unknown_done_reason_maps_to_error_instead_of_answer():
    response = parse_chat_response({"message": {"content": "被过滤的文字"}, "done": True,
                                    "done_reason": "content_filter", "prompt_eval_count": 100})
    assert response.finish_reason == "error"
    assert response.tool_calls == []


def test_length_truncation_drops_untrusted_tool_calls():
    raw = chat_response(calls=[function_call("call_cut", arguments={"expression": "1+"})], reason="length", content="半句")
    response = parse_chat_response(raw)
    assert response.finish_reason == "length" and response.tool_calls == []
    with model_with(lambda request: httpx.Response(200, json=raw)) as model:
        executed = []
        result = Runtime(model, tools=calculator_registry(executed)).run("截断的调用")
    assert result.status == "partial" and result.error_code == "output_limit"
    assert executed == []


# T09 超时与连接失败
def test_timeout_is_explicit_and_never_falls_back():
    requests = []

    def handle(request):
        requests.append(request)
        raise httpx.ReadTimeout("private-body", request=request)

    with model_with(handle) as model:
        runtime = Runtime(model)
        result = runtime.run("测试超时")
    assert result.status == "failed" and result.error_code == "model_call_failed"
    assert model.request_count == 1 and len(requests) == 1
    assert "private-body" not in runtime.trace.to_json()


@pytest.mark.parametrize("status", [302, 404, 500])
def test_http_errors_are_not_retried_or_leaked(status):
    def handle(request):
        return httpx.Response(status, headers={"Location": "https://unexpected.invalid/"}, text="private-body")

    with model_with(handle, max_requests=1) as model:
        with pytest.raises(LLMError) as error:
            model.generate([Message(role="user", content="测试")])
        assert error.value.code == f"http_{status}"
        assert "private-body" not in str(error.value)
        with pytest.raises(LLMError, match="request_limit"):
            model.generate([Message(role="user", content="不能自动重试")])
    assert model.request_count == 1


# T10 非法JSON/参数
@pytest.mark.parametrize("arguments", [[], "not-json", '{"expression": "1", "expression": "2"}', 7])
def test_invalid_tool_arguments_never_become_executable_calls(arguments):
    raw = chat_response(calls=[function_call("call_bad", arguments=arguments)])
    with model_with(lambda request: httpx.Response(200, json=raw)) as model:
        with pytest.raises(LLMError, match="invalid_response"):
            model.generate([Message(role="user", content="测试")])


def test_schema_invalid_arguments_are_reported_by_tool_registry_not_executed():
    executed = []
    bodies = []

    def handle(request):
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            return httpx.Response(200, json=chat_response(calls=[function_call("call_bad", arguments={"expression": 3})]))
        if len(bodies) == 2:
            return httpx.Response(200, json=chat_response(calls=[function_call("call_fixed", arguments={"expression": "37*19"})]))
        return httpx.Response(200, json=chat_response(content="结果是703"))

    with model_with(handle) as model:
        result = Runtime(model, tools=calculator_registry(executed)).run("错误参数")
    assert result.status == "completed" and executed == ["37*19"]
    feedback = json.loads(bodies[1]["messages"][-1]["content"])
    assert feedback["ok"] is False and feedback["error"]["code"] == "invalid_arguments"
    assert feedback["error"]["execution_state"] == "not_started"


# T11 上下文过大
def test_context_window_uses_conservative_byte_bound():
    with model_with(lambda request: None, num_ctx=8192, max_output_tokens=1024) as model:
        limits = model.context_limits()
    assert limits.context_window == 8192 * 3
    assert limits.max_output_tokens == 1024
    assert limits.model == "qwen3.5:4b"


def test_truncated_prompt_fails_explicitly_and_keeps_full_system_message():
    long_system = "系统指令" * 1000
    def handle(request):
        return httpx.Response(200, json=chat_response(content="看似完整的回答", prompt=8185))

    with model_with(handle, num_ctx=8192) as model:
        runtime = Runtime(model)
        result = runtime.run("会被截断的输入")
    assert result.status == "partial" and result.error_code == "context_limit"
    assert result.response is None
    assert build_messages([Message(role="system", content=long_system)])[0]["content"] == long_system


def test_long_request_is_over_derived_window_before_any_model_call():
    from min_agent.context import request_bound

    with model_with(lambda request: None, num_ctx=8192) as model:
        limits = model.context_limits()
        bound = request_bound([Message(role="user", content="长" * 8000)], None, model=model)
    assert bound + 1024 + 1024 > limits.context_window


def test_context_window_is_configurable_up_to_model_limit_but_default_stays_safe():
    # 上限对齐qwen3.5:4b声明的262144；默认32768来自32K长上下文多轮验收。
    assert OllamaSettings().num_ctx == 32768
    with model_with(lambda request: None, num_ctx=262144) as model:
        limits = model.context_limits()
    assert limits.context_window == 262144 * 3 == 786432
    assert limits.max_output_tokens == 1024
    for value in (2047, 262145):
        with pytest.raises(ValueError):
            OllamaSettings(num_ctx=value)


def test_truncation_guard_follows_configured_window_at_max_context():
    from min_agent.context import ContextLimit

    truncated = chat_response(content="被截断前的半句", prompt=262130)  # > 262144-16
    complete = chat_response(content="完整回答", prompt=262100)
    with model_with(lambda request: httpx.Response(200, json=truncated), num_ctx=262144) as model:
        with pytest.raises(ContextLimit):
            model.generate([Message(role="user", content="测试")])
    with model_with(lambda request: httpx.Response(200, json=complete), num_ctx=262144) as model:
        assert model.generate([Message(role="user", content="测试")]).content == "完整回答"


def test_byte_budget_and_output_reserve_align_at_max_window():
    from min_agent.context import request_bound

    with model_with(lambda request: None, num_ctx=262144) as model:
        limits = model.context_limits()
        fits = request_bound([Message(role="user", content="长" * 200_000)], None, model=model)
        over = request_bound([Message(role="user", content="长" * 300_000)], None, model=model)
    assert fits + 1024 + 1024 <= limits.context_window
    assert over + 1024 + 1024 > limits.context_window


def test_provider_refuses_requests_over_conservative_byte_window():
    from min_agent.context import ContextLimit

    called = []

    def handle(request):
        called.append(request)
        return httpx.Response(200, json=chat_response(content="不应发送"))

    with model_with(handle, num_ctx=2048) as model:
        with pytest.raises(ContextLimit):
            model.generate([Message(role="user", content="长" * 3000)])  # 9000字节 > 2048*3
    assert called == []


# T12 Provider切换与配置边界
def test_settings_only_allow_loopback_without_paths_or_credentials():
    assert normalize_base_url("http://127.0.0.1:11434/") == "http://127.0.0.1:11434"
    assert normalize_base_url("http://localhost") == "http://localhost:11434"
    assert normalize_base_url("http://[::1]:11434") == "http://[::1]:11434"
    for value in ["http://192.168.1.10:11434", "https://api.openai.com", "http://user:pass@127.0.0.1:11434",
                  "http://127.0.0.1:11434/api/chat", "http://127.0.0.1:11434?x=1", "ftp://127.0.0.1"]:
        with pytest.raises(ValueError):
            OllamaSettings(base_url=value)


def test_output_limit_cannot_exceed_window():
    with pytest.raises(ValueError):
        OllamaSettings(num_ctx=2048, max_output_tokens=4096)


def test_missing_model_is_reported_without_downloading():
    tags = {"models": [{"name": "other:1b"}]}
    settings = OllamaSettings()
    with pytest.raises(ValueError, match="不会自动下载"):
        require_model(settings, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=tags)))
    ok = {"models": [{"name": "qwen3.5:4b"}]}
    require_model(settings, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=ok)))
    assert available_models(settings, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=ok))) == ["qwen3.5:4b"]


def test_unreachable_service_is_an_explicit_error():
    def handle(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ValueError, match="无法连接本机Ollama"):
        require_model(OllamaSettings(), transport=httpx.MockTransport(handle))
    with pytest.raises(LLMError, match="network_error"):
        available_models(OllamaSettings(), transport=httpx.MockTransport(handle))


def test_closed_client_and_large_response_are_rejected():
    with model_with(lambda request: httpx.Response(200, content=b"x" * (4 * 1024 * 1024 + 1))) as model:
        with pytest.raises(LLMError, match="response_too_large"):
            model.generate([Message(role="user", content="大响应")])
    with pytest.raises(LLMError, match="client_closed"):
        model.generate([Message(role="user", content="已关闭")])
