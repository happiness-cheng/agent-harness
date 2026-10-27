import json

import httpx
import pytest

from min_agent.llm.config import OpenAISettings
from min_agent.llm.smoke import run_live_smoke
from tests.unit.test_llm_adapter import text_output, wire_response


@pytest.mark.parametrize("allow_live", [False, "yes"])
def test_live_gate_refuses_before_reading_any_credentials(monkeypatch, allow_live):
    def forbidden(*args, **kwargs):
        raise AssertionError("不应读取凭据")
    monkeypatch.setattr(OpenAISettings, "from_credentials_file", forbidden)
    with pytest.raises(PermissionError):
        run_live_smoke("not-read.json", allow_live=allow_live)


def test_live_smoke_uses_fixed_public_cases_and_maximum_three_mock_requests(tmp_path):
    path = tmp_path / "fake-keys.json"
    path.write_text(json.dumps({"openai_api_key": "unit-test-token"}), encoding="utf-8")
    captured = []
    def handle(request):
        captured.append(json.loads(request.content))
        if len(captured) == 1:
            raw = wire_response(text_output("连接成功"))
        elif len(captured) == 2:
            raw = wire_response({"type": "function_call", "id": "fc", "call_id": "math", "name": "calculator", "arguments": '{"expression":"0.1+0.2"}', "status": "completed"})
        else:
            raw = wire_response(text_output("0.3"))
        return httpx.Response(200, json=raw)
    result = run_live_smoke(path, allow_live=True, transport=httpx.MockTransport(handle))
    assert result["plain_ok"] is True and result["calculator_ok"] is True
    assert result["requests"] == 3
    assert all(body["max_output_tokens"] == 4096 and body["store"] is False for body in captured)
    assert "unit-test-token" not in json.dumps(captured) + json.dumps(result)


@pytest.mark.parametrize("code", ["http_401", "network_error", "invalid_response"])
def test_smoke_cli_keeps_sanitized_provider_error_code(monkeypatch, capsys, code):
    from min_agent.llm import smoke
    from min_agent.llm.client import LLMError

    def fail(*args, **kwargs):
        raise LLMError(code)

    monkeypatch.setattr(smoke, "run_live_smoke", fail)
    monkeypatch.setattr("sys.argv", ["smoke", "--credentials-file", "not-read.json", "--allow-live"])
    assert smoke.main() == 1
    output = capsys.readouterr()
    assert json.loads(output.out) == {"ok": False, "error": code}
    assert output.err == ""


def test_smoke_cli_does_not_print_raw_configuration_error(monkeypatch, capsys):
    from min_agent.llm import smoke

    def fail(*args, **kwargs):
        raise ValueError("private-unit-test-value")

    monkeypatch.setattr(smoke, "run_live_smoke", fail)
    monkeypatch.setattr("sys.argv", ["smoke", "--credentials-file", "not-read.json"])
    assert smoke.main() == 1
    output = capsys.readouterr()
    assert json.loads(output.out) == {"ok": False, "error": "live_smoke_failed_or_not_authorized"}
    assert "private-unit-test-value" not in output.out + output.err
