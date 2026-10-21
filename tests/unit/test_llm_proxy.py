import json

import httpx
import pytest

from min_agent.llm.client import LLMError, OpenAIResponsesModel
from min_agent.llm.config import OpenAISettings
from min_agent.models import Message
from tests.unit.test_llm_adapter import text_output, wire_response


def test_explicit_proxy_preserves_official_endpoint_tls_and_request_limits(monkeypatch):
    original_client = httpx.Client
    options = []
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=wire_response(text_output()))

    def client(**kwargs):
        options.append(kwargs)
        # 仅替换测试中的传输，不向本机代理或远端发出网络请求。
        offline = {key: value for key, value in kwargs.items() if key not in {"proxy", "transport"}}
        return original_client(**offline, transport=httpx.MockTransport(handle))

    monkeypatch.setattr(httpx, "Client", client)
    with OpenAIResponsesModel(OpenAISettings(api_key="unit-test-token", max_requests=1), proxy="http://127.0.0.1:7890") as model:
        assert model.generate([Message(role="user", content="连接测试")]).content == "连接成功"
        with pytest.raises(LLMError, match="^request_limit$"):
            model.generate([Message(role="user", content="不应发出的第二次请求")])
        assert model.request_count == 1
    assert options[0]["proxy"] == "http://127.0.0.1:7890"
    assert options[0]["verify"] is True
    assert options[0]["trust_env"] is False and options[0]["follow_redirects"] is False
    assert options[0]["timeout"] == 60.0
    assert len(requests) == 1
    assert str(requests[0].url) == "https://api.openai.com/v1/responses"
    assert requests[0].headers["Authorization"] == "Bearer unit-test-token"
    body = json.loads(requests[0].content)
    assert body["model"] == "gpt-6.1-sol" and body["reasoning"]["effort"] == "high"
    assert body["max_output_tokens"] == 4096 and body["store"] is False


@pytest.mark.parametrize("proxy", ["http://127.0.0.1:7890", "http://localhost:7890/", "http://[::1]:7890"])
def test_loopback_http_proxy_is_accepted_without_connecting(proxy):
    with OpenAIResponsesModel(OpenAISettings(api_key="unit-test-token"), proxy=proxy) as model:
        assert model.request_count == 0


@pytest.mark.parametrize("proxy", [
    "http://remote.example:7890", "https://127.0.0.1:7890", "socks5://127.0.0.1:7890",
    "http://unit-user:unit-password@127.0.0.1:7890", "http://127.0.0.1",
    "http://127.0.0.1:0", "http://127.0.0.1:65536", "http://127.0.0.1:bad",
    "http://127.0.0.1:7890/path", "http://127.0.0.1:7890?token=unit-private",
    "http://127.0.0.1:7890#unit-private", "http://127.0.0.1:7890\n", "", 123,
])
def test_unsupported_proxy_is_rejected_with_fixed_error(proxy):
    with pytest.raises(LLMError, match="^invalid_proxy$") as error:
        OpenAIResponsesModel(OpenAISettings(api_key="unit-test-token"), proxy=proxy)
    assert "unit-password" not in str(error.value) and "unit-private" not in str(error.value)


def test_explicit_proxy_cannot_bypass_custom_test_transport():
    transport = httpx.MockTransport(lambda request: pytest.fail("不应发出请求"))
    with pytest.raises(LLMError, match="^invalid_transport_configuration$"):
        OpenAIResponsesModel(OpenAISettings(api_key="unit-test-token"), proxy="http://127.0.0.1:7890", transport=transport)


def test_environment_proxy_is_not_used_without_explicit_option(monkeypatch):
    original_client = httpx.Client
    options = []

    def client(**kwargs):
        options.append(kwargs)
        return original_client(**kwargs)

    monkeypatch.setenv("HTTPS_PROXY", "http://remote.example:7890")
    monkeypatch.setenv("OPENAI_API_KEY", "environment-must-not-be-used")
    monkeypatch.setattr(httpx, "Client", client)
    with OpenAIResponsesModel(OpenAISettings(api_key="unit-test-token"), transport=httpx.MockTransport(lambda request: httpx.Response(200, json=wire_response(text_output())))) as model:
        assert model.generate([Message(role="user", content="测试")]).content == "连接成功"
    assert options[0].get("proxy") is None and options[0]["trust_env"] is False


def test_smoke_forwards_explicit_proxy_and_keeps_three_call_cases(tmp_path, monkeypatch):
    from min_agent.llm import smoke

    path = tmp_path / "fake-keys.json"
    path.write_text(json.dumps({"openai_api_key": "unit-test-token"}), encoding="utf-8")
    proxies = []
    calls = []

    def handle(request):
        calls.append(request)
        if len(calls) == 2:
            output = {"type": "function_call", "id": "fc", "call_id": "math", "name": "calculator", "arguments": '{"expression":"0.1+0.2"}', "status": "completed"}
        else:
            output = text_output("连接成功" if len(calls) == 1 else "0.3")
        return httpx.Response(200, json=wire_response(output))

    def model(settings, *, proxy=None, transport=None):
        proxies.append(proxy)
        return OpenAIResponsesModel(settings, transport=httpx.MockTransport(handle))

    monkeypatch.setattr(smoke, "OpenAIResponsesModel", model)
    result = smoke.run_live_smoke(path, allow_live=True, proxy="http://127.0.0.1:7890")
    assert proxies == ["http://127.0.0.1:7890"]
    assert result["plain_ok"] and result["calculator_ok"] and result["requests"] == 3


def test_smoke_proxy_option_does_not_skip_live_gate(monkeypatch):
    from min_agent.llm import smoke

    monkeypatch.setattr(OpenAISettings, "from_credentials_file", lambda *args, **kwargs: pytest.fail("不应读取凭据"))
    with pytest.raises(PermissionError):
        smoke.run_live_smoke("not-read.json", proxy="http://127.0.0.1:7890")


def test_smoke_cli_forwards_explicit_proxy(monkeypatch, capsys):
    from min_agent.llm import smoke

    observed = []

    def run(path, **kwargs):
        observed.append((path, kwargs))
        return {"plain_ok": True, "calculator_ok": True, "requests": 3}

    monkeypatch.setattr(smoke, "run_live_smoke", run)
    monkeypatch.setattr("sys.argv", ["smoke", "--credentials-file", "not-read.json", "--allow-live", "--proxy", "http://127.0.0.1:7890"])
    assert smoke.main() == 0
    assert observed == [("not-read.json", {"allow_live": True, "proxy": "http://127.0.0.1:7890"})]
    assert json.loads(capsys.readouterr().out)["requests"] == 3
