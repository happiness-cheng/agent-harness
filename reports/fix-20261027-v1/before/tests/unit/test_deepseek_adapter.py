import json

import httpx
import pytest

from min_agent.llm.client import LLMError, OpenAIResponsesModel
from min_agent.llm.config import OpenAISettings
from min_agent.llm.deepseek import DeepSeekResponsesModel, DeepSeekSettings
from min_agent.models import Message
from min_agent.runtime import Runtime
from min_agent.tools.calculator import CALCULATOR_TOOL
from min_agent.tools.registry import ToolRegistry


def ds_text(text="连接成功"):
    return {"type": "message", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": text}]}


def ds_response(*items, status="completed", **extra):
    return {"status": status, "model": "deepseek-flash", "output": list(items), **extra}


def test_provider_usage_is_separate_from_context_estimate_and_sanitized():
    raw = ds_response(ds_text(), usage={"input_tokens": 10, "output_tokens": 4, "total_tokens": 14,
                                       "private": "do-not-copy", "cached_tokens": True})
    with DeepSeekResponsesModel(DeepSeekSettings(api_key="unit-token"),
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=raw))) as model:
        model.generate([Message(role="user", content="公开用例")])
        assert model.usage == [{"request": 1, "input_tokens": 10, "output_tokens": 4, "total_tokens": 14}]
        assert "do-not-copy" not in str(model.usage)


def test_deepseek_uses_only_its_explicit_credential_field_and_official_endpoint(tmp_path):
    path = tmp_path / "fake-keys.json"
    path.write_text(json.dumps({"openai_api_key": "other-provider-token", "deepseek_api_key": "unit-deepseek-token"}), encoding="utf-8-sig")
    settings = DeepSeekSettings.from_credentials_file(path)
    assert settings.model == "deepseek-flash" and settings.reasoning_effort == "high"
    assert "unit-deepseek-token" not in repr(settings) + settings.model_dump_json()
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=ds_response(ds_text()))

    with DeepSeekResponsesModel(settings, transport=httpx.MockTransport(handle)) as model:
        response = model.generate([Message(role="user", content="连接测试")])
    assert response.content == "连接成功" and response.finish_reason == "stop"
    assert response.provider_state.provider == "deepseek.responses"
    assert str(requests[0].url) == "https://api.deepseek.com/responses"
    assert requests[0].headers["Authorization"] == "Bearer unit-deepseek-token"
    body = json.loads(requests[0].content)
    assert body["model"] == "deepseek-flash" and body["reasoning"] == {"effort": "high"}
    assert body["max_output_tokens"] == 4096 and body["stream"] is False
    assert not {"include", "store", "parallel_tool_calls", "previous_response_id"} & body.keys()
    assert "other-provider-token" not in requests[0].headers["Authorization"] + json.dumps(body)


def test_deepseek_tool_roundtrip_preserves_native_state_without_displaying_it():
    reasoning = {"type": "reasoning", "content": [{"type": "reasoning_text", "text": "synthetic-protocol-state"}]}
    call = {"type": "function_call", "call_id": "math", "name": "calculator", "arguments": '{"expression":"0.1+0.2"}', "status": "completed"}
    bodies = []

    def handle(request):
        bodies.append(json.loads(request.content))
        raw = ds_response(reasoning, call) if len(bodies) == 1 else ds_response(ds_text("0.3"))
        return httpx.Response(200, json=raw)

    tools = ToolRegistry()
    tools.register(CALCULATOR_TOOL)
    with DeepSeekResponsesModel(DeepSeekSettings(api_key="unit-deepseek-token"), transport=httpx.MockTransport(handle)) as model:
        result = Runtime(model, tools=tools).run("调用工具计算0.1+0.2")
    assert result.status == "completed" and result.response.content == "0.3"
    assert result.response.reasoning_summary is None
    assert bodies[1]["input"][1:3] == [reasoning, call]
    assert bodies[1]["input"][3]["call_id"] == "math"
    assert json.loads(bodies[1]["input"][3]["output"])["result"] == "0.3"


@pytest.mark.parametrize("code,raw,reason", [
    (200, ds_response(ds_text("半句"), status="incomplete", incomplete_details={"reason": "max_output_tokens"}), "length"),
    (200, ds_response(status="failed"), "error"),
    (429, {"error": {"code": "rate_limit_exceeded", "type": "rate_limit_error"}}, "http_429"),
])
def test_deepseek_failures_are_not_success_or_automatically_retried(code, raw, reason):
    requests = []
    with DeepSeekResponsesModel(DeepSeekSettings(api_key="unit-deepseek-token"), transport=httpx.MockTransport(lambda request: requests.append(request) or httpx.Response(code, json=raw))) as model:
        if code == 200:
            response = model.generate([Message(role="user", content="测试")])
            assert response.finish_reason == reason and response.provider_state is None
        else:
            with pytest.raises(LLMError, match=reason):
                model.generate([Message(role="user", content="测试")])
        assert model.request_count == len(requests) == 1


@pytest.mark.parametrize("item", [
    {"type": "reasoning", "encrypted_content": "wrong-provider-state"},
    {"type": "reasoning", "summary": [{"type": "summary_text", "text": "not-a-deepseek-summary"}]},
    {"type": "reasoning", "content": [{"type": "unexpected", "text": "synthetic-state"}]},
    {"type": "web_search_call"},
    {"type": "message", "status": "in_progress", "role": "assistant", "content": []},
])
def test_unsupported_native_items_are_rejected(item):
    with DeepSeekResponsesModel(DeepSeekSettings(api_key="unit-deepseek-token"), transport=httpx.MockTransport(lambda request: httpx.Response(200, json=ds_response(item, ds_text())))) as model:
        with pytest.raises(LLMError, match="^invalid_response$"):
            model.generate([Message(role="user", content="测试")])


def test_native_state_cannot_cross_provider_or_disagree_with_visible_history():
    with DeepSeekResponsesModel(DeepSeekSettings(api_key="unit-deepseek-token"), transport=httpx.MockTransport(lambda request: httpx.Response(200, json=ds_response(ds_text())))) as model:
        response = model.generate([Message(role="user", content="测试")])
    history = [Message(role="assistant", content="连接成功", provider_state=response.provider_state)]
    forbidden = httpx.MockTransport(lambda request: pytest.fail("不应请求网络"))
    with OpenAIResponsesModel(OpenAISettings(api_key="unit-test-token"), transport=forbidden) as other:
        with pytest.raises(LLMError, match="^invalid_request$"):
            other.generate(history)
        assert other.request_count == 0
    history[0].content = "tampered"
    with DeepSeekResponsesModel(DeepSeekSettings(api_key="unit-deepseek-token"), transport=forbidden) as model:
        with pytest.raises(LLMError, match="^invalid_request$"):
            model.generate(history)
        assert model.request_count == 0


@pytest.mark.parametrize("model_type,settings", [
    (OpenAIResponsesModel, DeepSeekSettings(api_key="unit-deepseek-token")),
    (DeepSeekResponsesModel, OpenAISettings(api_key="unit-test-token")),
])
def test_settings_cannot_select_the_wrong_provider_endpoint(model_type, settings):
    with pytest.raises(LLMError, match="^model_provider_mismatch$"):
        model_type(settings)


def test_missing_deepseek_key_never_uses_openai_or_environment(tmp_path, monkeypatch):
    path = tmp_path / "fake-keys.json"
    path.write_text(json.dumps({"openai_api_key": "other-provider-token"}), encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "environment-must-not-be-used")
    with pytest.raises(ValueError) as error:
        DeepSeekSettings.from_credentials_file(path)
    assert "other-provider-token" not in str(error.value)


def test_deepseek_parser_failure_keeps_fixed_category_without_remote_text():
    item = {"type": "reasoning", "summary": [{"type": "summary_text", "text": "unit-deepseek-token"}]}
    with DeepSeekResponsesModel(DeepSeekSettings(api_key="unit-deepseek-token"), transport=httpx.MockTransport(lambda request: httpx.Response(200, json=ds_response(item, ds_text())))) as model:
        with pytest.raises(LLMError) as error:
            model.generate([Message(role="user", content="测试")])
    assert error.value.code == "invalid_response"
    assert error.value.diagnostics == {"protocol_issue": "unsupported_deepseek_state"}
    assert "unit-deepseek-token" not in json.dumps(error.value.__dict__)


@pytest.mark.parametrize("extra", [
    {"summary": [{"type": "summary_text", "text": "synthetic-extra-metadata"}]},
    {"encrypted_content": "synthetic-extra-metadata"},
    {"summary": [{"type": "summary_text", "text": "synthetic-extra-metadata"}], "encrypted_content": "synthetic-extra-metadata"},
])
def test_deepseek_tool_roundtrip_ignores_unsupported_reasoning_metadata(extra):
    reasoning = {"type": "reasoning", "content": [{"type": "reasoning_text", "text": "synthetic-protocol-state"}], **extra}
    call = {"type": "function_call", "call_id": "math", "name": "calculator", "arguments": '{"expression":"0.1+0.2"}'}
    bodies = []

    def handle(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=ds_response(reasoning, call) if len(bodies) == 1 else ds_response(ds_text("0.3")))

    tools = ToolRegistry()
    tools.register(CALCULATOR_TOOL)
    with DeepSeekResponsesModel(DeepSeekSettings(api_key="unit-deepseek-token"), transport=httpx.MockTransport(handle)) as model:
        result = Runtime(model, tools=tools).run("调用工具计算0.1+0.2")
    assert result.status == "completed" and result.response.content == "0.3"
    assert result.response.reasoning_summary is None
    assert bodies[1]["input"][1] == {"type": "reasoning", "content": reasoning["content"]}
    assert "synthetic-extra-metadata" not in json.dumps(bodies[1])
    assert json.loads(bodies[1]["input"][3]["output"])["result"] == "0.3"
    assert all(reasoning[key] == value for key, value in extra.items())
