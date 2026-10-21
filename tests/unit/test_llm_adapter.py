import json

import httpx
import pytest

from min_agent.llm.client import LLMError, OpenAIResponsesModel
from min_agent.llm.config import OpenAISettings
from min_agent.models import Message
from min_agent.runtime import Runtime
from min_agent.models import ProviderState
from min_agent.tools.calculator import CALCULATOR_TOOL
from min_agent.tools.registry import ToolRegistry


def text_output(text="连接成功"):
    return {"type": "message", "id": "msg_test", "role": "assistant", "status": "completed", "content": [
        {"type": "output_text", "text": text, "annotations": []},
    ]}


def wire_response(*items, status="completed", **extra):
    return {"id": "resp_test", "model": "gpt-6.1-sol", "status": status, "output": list(items), **extra}


def test_adapter_posts_explicit_openai_responses_request_and_maps_text():
    captured = []

    def handle(request):
        captured.append(request)
        return httpx.Response(200, json=wire_response(text_output()))

    settings = OpenAISettings(api_key="unit-test-token")
    with OpenAIResponsesModel(settings, transport=httpx.MockTransport(handle)) as model:
        response = model.generate([Message(role="user", content="只回复连接成功")])

    assert response.finish_reason == "stop" and response.content == "连接成功"
    request = captured[0]
    assert str(request.url) == "https://api.openai.com/v1/responses"
    assert request.method == "POST" and request.headers["Authorization"] == "Bearer unit-test-token"
    body = json.loads(request.content)
    assert body["model"] == "gpt-6.1-sol" and body["reasoning"]["effort"] == "high"
    assert body["input"] == [{"role": "user", "content": "只回复连接成功"}]
    assert body["store"] is False and body["stream"] is False
    assert body["max_output_tokens"] == 4096
    assert "previous_response_id" not in body


def test_native_function_call_and_encrypted_reasoning_return_through_real_runtime():
    captured = []
    reasoning = {"type": "reasoning", "id": "rs_test", "summary": [{"type": "summary_text", "text": "准备计算"}], "encrypted_content": "opaque-test-state"}
    call = {"type": "function_call", "id": "fc_item", "call_id": "call_math", "name": "calculator", "arguments": '{"expression":"0.1+0.2"}', "status": "completed"}

    def handle(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=wire_response(reasoning, call) if len(captured) == 1 else wire_response(text_output("结果0.3")))

    tools = ToolRegistry()
    tools.register(CALCULATOR_TOOL)
    with OpenAIResponsesModel(OpenAISettings(api_key="unit-test-token"), transport=httpx.MockTransport(handle)) as model:
        result = Runtime(model, tools=tools).run("用工具计算0.1+0.2")
    assert result.status == "completed"
    assert result.response.content == "结果0.3"
    assert captured[0]["tools"][0]["type"] == "function"
    assert captured[0]["tools"][0]["strict"] is False
    assert captured[0]["parallel_tool_calls"] is False
    assert captured[1]["input"][1:3] == [reasoning, call]
    output = captured[1]["input"][3]
    assert output["type"] == "function_call_output" and output["call_id"] == "call_math"
    assert json.loads(output["output"])["ok"] is True


@pytest.mark.parametrize("raw,reason,content", [
    (wire_response(text_output("半句"), status="incomplete", incomplete_details={"reason": "max_output_tokens"}), "length", "半句"),
    (wire_response(status="incomplete", incomplete_details={"reason": "max_output_tokens"}), "length", None),
    (wire_response({"type": "message", "id": "msg_refusal", "role": "assistant", "status": "completed", "content": [{"type": "refusal", "refusal": "拒绝执行"}]}), "refusal", "拒绝执行"),
    (wire_response(text_output("过滤前片段"), status="incomplete", incomplete_details={"reason": "content_filter"}), "refusal", "过滤前片段"),
    (wire_response(text_output("非终态"), status="in_progress"), "error", None),
    (wire_response(status="failed", error={"message": "不可暴露的服务器正文"}), "error", None),
])
def test_api_statuses_refusal_and_truncation_never_become_success(raw, reason, content):
    with OpenAIResponsesModel(OpenAISettings(api_key="unit-test-token"), transport=httpx.MockTransport(lambda request: httpx.Response(200, json=raw))) as model:
        response = model.generate([Message(role="user", content="测试")])
    assert response.finish_reason == reason and response.content == content
    assert response.tool_calls == []


def test_credentials_loaded_only_from_explicit_path_without_exposing_other_provider_key(tmp_path):
    path = tmp_path / "keys.json"
    path.write_text(json.dumps({"openai_api_key": "unit-test-token", "deepseek_api_key": "other-unit-token"}), encoding="utf-8-sig")
    settings = OpenAISettings.from_credentials_file(path)
    assert settings.api_key.get_secret_value() == "unit-test-token"
    assert "unit-test-token" not in repr(settings) + str(settings) + settings.model_dump_json()
    assert "other-unit-token" not in repr(settings) + settings.model_dump_json()


def test_bad_credential_file_never_falls_back_to_environment_or_echoes_content(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "environment-must-not-be-used")
    missing = tmp_path / "missing.json"
    with pytest.raises(ValueError) as error:
        OpenAISettings.from_credentials_file(missing)
    assert "environment-must-not-be-used" not in str(error.value)
    path = tmp_path / "bad.json"
    path.write_text('{"openai_api_key": "private-test-value", broken', encoding="utf-8")
    with pytest.raises(ValueError) as error:
        OpenAISettings.from_credentials_file(path)
    assert "private-test-value" not in str(error.value)


@pytest.mark.parametrize("status", [302, 401, 429, 500])
def test_http_errors_and_redirects_are_not_retried_or_leaked(status):
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(status, headers={"Location": "https://unexpected.invalid/"}, text="private-error-body")
    with OpenAIResponsesModel(OpenAISettings(api_key="unit-test-token", max_requests=1), transport=httpx.MockTransport(handle)) as model:
        with pytest.raises(LLMError) as error:
            model.generate([Message(role="user", content="测试")])
        assert error.value.code == f"http_{status}"
        assert "private-error-body" not in str(error.value) and "unit-test-token" not in repr(error.value)
        with pytest.raises(LLMError, match="request_limit"):
            model.generate([Message(role="user", content="不能自动重试")])
        assert model.request_count == 1 and len(requests) == 1


def test_network_exception_and_runtime_trace_do_not_contain_request_or_credentials():
    def handle(request):
        raise httpx.ReadTimeout("private-body unit-test-token", request=request)
    with OpenAIResponsesModel(OpenAISettings(api_key="unit-test-token"), transport=httpx.MockTransport(handle)) as model:
        runtime = Runtime(model)
        result = runtime.run("测试异常")
    assert result.status == "failed" and result.error_code == "model_call_failed"
    assert "unit-test-token" not in runtime.trace.to_json()
    assert "private-body" not in runtime.trace.to_json()


@pytest.mark.parametrize("content", [b'not json', b'{"status":"completed","status":"failed"}', b'NaN', b'[]'])
def test_bad_json_is_a_sanitized_protocol_error(content):
    with OpenAIResponsesModel(OpenAISettings(api_key="unit-test-token"), transport=httpx.MockTransport(lambda request: httpx.Response(200, content=content))) as model:
        with pytest.raises(LLMError, match="invalid_response"):
            model.generate([Message(role="user", content="测试")])


@pytest.mark.parametrize("item", [
    {**text_output(), "status": "incomplete"},
    {"type": "web_search_call", "id": "unsupported"},
    {"type": "function_call", "id": "fc", "call_id": "call", "name": "calculator", "arguments": "[]"},
    {"type": "function_call", "id": "fc", "call_id": "call", "name": "calculator", "arguments": '{"expression":"1","expression":"2"}'},
    {"type": "reasoning", "id": "rs", "summary": [], "encrypted_content": "", "content": []},
])
def test_malformed_or_unsupported_items_cannot_become_success_or_executable_calls(item):
    with OpenAIResponsesModel(OpenAISettings(api_key="unit-test-token"), transport=httpx.MockTransport(lambda request: httpx.Response(200, json=wire_response(item)))) as model:
        with pytest.raises(LLMError, match="invalid_response"):
            model.generate([Message(role="user", content="测试")])


def test_incomplete_function_arguments_are_never_executed():
    item = {"type": "function_call", "id": "fc", "call_id": "call", "name": "calculator", "arguments": '{"expression":', "status": "incomplete"}
    with OpenAIResponsesModel(OpenAISettings(api_key="unit-test-token"), transport=httpx.MockTransport(lambda request: httpx.Response(200, json=wire_response(item, status="incomplete", incomplete_details={"reason": "max_output_tokens"})))) as model:
        tools = ToolRegistry()
        tools.register(CALCULATOR_TOOL)
        runtime = Runtime(model, tools=tools)
        result = runtime.run("截断")
    assert result.status == "partial"
    assert not any(event.operation == "tool" for event in runtime.trace.events)


@pytest.mark.parametrize("change", ["provider", "model", "text"])
def test_foreign_or_inconsistent_native_state_is_rejected_before_request(change):
    state = ProviderState(provider="openai.responses", model="gpt-6.1-sol", response_model="gpt-6.1-sol", output=[text_output("原文")])
    if change == "provider":state.provider = "deepseek.responses"
    if change == "model":state.model = "different-model"
    requests = []
    with OpenAIResponsesModel(OpenAISettings(api_key="unit-test-token"), transport=httpx.MockTransport(lambda request: requests.append(request))) as model:
        with pytest.raises(LLMError, match="invalid_request"):
            model.generate([Message(role="assistant", content="修改" if change == "text" else "原文", provider_state=state)])
        assert model.request_count == 0
    assert requests == []


def test_visible_reasoning_summary_is_distinct_from_opaque_state_and_legacy_history_encodes():
    reasoning = {"type": "reasoning", "id": "rs", "summary": [{"type": "summary_text", "text": "可见摘要"}], "encrypted_content": "opaque-state"}
    captured = []
    def handle(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=wire_response(reasoning, text_output("回答")))
    from min_agent.models import ToolCall
    history = [
        Message(role="assistant", content="", tool_calls=[ToolCall(id="legacy", name="calculator", arguments={"expression": "1+1"})]),
        Message(role="tool", content="2", tool_call_id="legacy"), Message(role="user", content="继续"),
    ]
    with OpenAIResponsesModel(OpenAISettings(api_key="unit-test-token"), transport=httpx.MockTransport(handle)) as model:
        response = model.generate(history)
    assert response.reasoning_summary == "可见摘要"
    assert response.provider_state.output == [reasoning, text_output("回答")]
    assert captured[0]["input"][0]["type"] == "function_call"
    assert captured[0]["input"][1]["type"] == "function_call_output"


def test_response_buffer_limit_and_closed_resource():
    with OpenAIResponsesModel(OpenAISettings(api_key="unit-test-token"), transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * (4 * 1024 * 1024 + 1)))) as model:
        with pytest.raises(LLMError, match="response_too_large"):
            model.generate([Message(role="user", content="大响应")])
    with pytest.raises(LLMError, match="client_closed"):
        model.generate([Message(role="user", content="已关闭")])


@pytest.mark.parametrize("key", ["", "contains space", "invalid\nheader", "invalid\x00header"])
def test_invalid_secret_never_reaches_network(key):
    with pytest.raises(LLMError, match="invalid_api_key"):
        OpenAIResponsesModel(OpenAISettings(api_key=key))


def test_credentials_with_unrecognized_endpoint_configuration_are_not_silently_ignored(tmp_path):
    path = tmp_path / "unexpected-config.json"
    path.write_text(json.dumps({"openai_api_key": "unit-test-token", "base_url": "https://unexpected.invalid"}), encoding="utf-8")
    with pytest.raises(ValueError, match="无法加载指定OpenAI凭据或配置"):
        OpenAISettings.from_credentials_file(path)
