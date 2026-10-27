import json

import httpx
import pytest

from min_agent.llm.config import OpenAISettings
from min_agent.llm.deepseek import DeepSeekSettings
from min_agent.llm.smoke import run_live_smoke
from tests.unit.test_deepseek_adapter import ds_response, ds_text


def test_deepseek_smoke_tracks_real_calculator_with_three_mock_requests(tmp_path):
    path = tmp_path / "fake-keys.json"
    path.write_text(json.dumps({"deepseek_api_key": "unit-deepseek-token", "openai_api_key": "other-provider-token"}), encoding="utf-8")
    requests = []

    def handle(request):
        requests.append(request)
        if len(requests) == 2:
            output = {"type": "function_call", "call_id": "math", "name": "calculator", "arguments": '{"expression":"0.1+0.2"}', "status": "completed"}
        else:
            output = ds_text("连接成功" if len(requests) == 1 else "0.3")
        return httpx.Response(200, json=ds_response(output))

    result = run_live_smoke(path, allow_live=True, provider="deepseek", transport=httpx.MockTransport(handle))
    assert result["plain_ok"] and result["calculator_ok"] and result["requests"] == 3
    assert result["provider"] == "deepseek" and result["model"] == "deepseek-flash"
    assert all(str(request.url) == "https://api.deepseek.com/responses" for request in requests)
    assert all(request.headers["Authorization"] == "Bearer unit-deepseek-token" for request in requests)
    assert "unit-deepseek-token" not in json.dumps(result)


@pytest.mark.parametrize("allow_live", [False, "yes"])
def test_deepseek_smoke_gate_precedes_credentials(monkeypatch, allow_live):
    monkeypatch.setattr(DeepSeekSettings, "from_credentials_file", lambda *args, **kwargs: pytest.fail("不能读凭据"))
    with pytest.raises(PermissionError):
        run_live_smoke("not-read.json", provider="deepseek", allow_live=allow_live)


def test_unknown_provider_does_not_fall_back_or_read_credentials(monkeypatch):
    for settings in (OpenAISettings, DeepSeekSettings):
        monkeypatch.setattr(settings, "from_credentials_file", lambda *args, **kwargs: pytest.fail("不能读凭据"))
    with pytest.raises(ValueError):
        run_live_smoke("not-read.json", provider="unknown", allow_live=True)


def test_deepseek_cli_selects_provider_explicitly(monkeypatch, capsys):
    from min_agent.llm import smoke
    captured = []

    def run(path, **kwargs):
        captured.append(kwargs)
        return {"plain_ok": True, "calculator_ok": True, "requests": 3}

    monkeypatch.setattr(smoke, "run_live_smoke", run)
    monkeypatch.setattr("sys.argv", ["smoke", "--credentials-file", "not-read.json", "--allow-live", "--provider", "deepseek"])
    assert smoke.main() == 0
    assert captured == [{"allow_live": True, "proxy": None, "provider": "deepseek"}]
    assert json.loads(capsys.readouterr().out)["requests"] == 3
