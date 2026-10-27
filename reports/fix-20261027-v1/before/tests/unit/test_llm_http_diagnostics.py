import json
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from min_agent.llm.client import LLMError, OpenAIResponsesModel
from min_agent.llm.config import OpenAISettings
from min_agent.models import Message


def http_error(response):
    requests = []

    def handle(request):
        requests.append(request)
        return response

    with OpenAIResponsesModel(OpenAISettings(api_key="unit-test-token"), transport=httpx.MockTransport(handle)) as model:
        with pytest.raises(LLMError) as error:
            model.generate([Message(role="user", content="private-unit-prompt")])
        assert model.request_count == len(requests) == 1
    return error.value


def test_http_error_keeps_safe_classification_and_delay_without_raw_body():
    error = http_error(httpx.Response(429, json={"error": {
        "code": "rate_limit_exceeded", "type": "rate_limit_error",
        "message": "unit-test-token private-unit-prompt", "param": "private-unit-param",
    }}, headers={"Retry-After": "56", "x-private-header": "unit-test-token"}))
    assert error.code == str(error) == "http_429"
    assert error.diagnostics == {"http_status": 429, "request_count": 1,
                                 "api_error_code": "rate_limit_exceeded", "api_error_type": "rate_limit_error",
                                 "retry_after_seconds": 56}
    visible = repr(error) + json.dumps(error.__dict__)
    assert "unit-test-token" not in visible and "private-unit" not in visible


@pytest.mark.parametrize("code", ["slow_down", "insufficient_quota", "credit_balance_exhausted",
    "organization_spend_limit_exceeded", "project_spend_limit_exceeded", "organization_usage_limit_exceeded"])
def test_documented_429_codes_are_preserved(code):
    error = http_error(httpx.Response(429, json={"error": {"code": code, "type": "insufficient_quota"}}))
    assert error.diagnostics["api_error_code"] == code
    assert error.diagnostics["api_error_type"] == "insufficient_quota"


@pytest.mark.parametrize("value", ["unit-test-token", {"secret": "unit-test-token"}, ["unit-test-token"], None, 123])
def test_unknown_or_malformed_classification_is_not_echoed(value):
    error = http_error(httpx.Response(429, json={"error": {"code": value, "type": value, "message": "unit-test-token"}}))
    assert error.diagnostics == {"http_status": 429, "request_count": 1}


@pytest.mark.parametrize("body", [b'not-json-unit-test-token', b'[]', b'null', b'{"error":[]}',
    b'{"error":{"code":"slow_down","code":"unit-test-token"}}', b'[' * 2000])
def test_invalid_error_body_keeps_http_status_and_safe_header(body):
    error = http_error(httpx.Response(429, content=body, headers={"Retry-After": "12"}))
    assert error.code == "http_429"
    assert error.diagnostics == {"http_status": 429, "request_count": 1, "retry_after_seconds": 12}


@pytest.mark.parametrize("header,expected", [("0", 0), ("56", 56), ("0006", 6), ("604800", 604800),
    ("-1", None), ("1.5", None), ("nan", None), ("604801", None), ("unit-test-token", None), ("9" * 150, None)])
def test_retry_after_is_bounded_and_not_executed(header, expected):
    error = http_error(httpx.Response(429, json={}, headers={"Retry-After": header}))
    if expected is None:
        assert "retry_after_seconds" not in error.diagnostics
    else:
        assert error.diagnostics["retry_after_seconds"] == expected
    assert "unit-test-token" not in json.dumps(error.__dict__)


def test_retry_after_http_date_is_converted_to_seconds():
    date = format_datetime(datetime.now(UTC) + timedelta(seconds=60), usegmt=True)
    error = http_error(httpx.Response(429, json={}, headers={"Retry-After": date}))
    assert 0 <= error.diagnostics["retry_after_seconds"] <= 60
    past = http_error(httpx.Response(429, json={}, headers={"Retry-After": "Sat, 01 Jan 2000 00:00:00 GMT"}))
    assert past.diagnostics["retry_after_seconds"] == 0


def test_error_body_has_64_kib_limit_and_stream_is_closed():
    closed = []

    class LargeBody(httpx.SyncByteStream):
        def __iter__(self):
            yield b'x' * (64 * 1024 + 1)
            pytest.fail("超限后不应继续读错误正文")

        def close(self):
            closed.append(True)

    error = http_error(httpx.Response(429, stream=LargeBody(), headers={"Retry-After": "3"}))
    assert error.code == "http_429" and error.diagnostics["retry_after_seconds"] == 3
    assert closed


def test_error_body_read_failure_does_not_hide_original_http_status():
    class BrokenBody(httpx.SyncByteStream):
        def __iter__(self):
            raise httpx.ReadTimeout("unit-test-token")
            yield b''

    error = http_error(httpx.Response(429, stream=BrokenBody()))
    assert error.code == "http_429" and "unit-test-token" not in json.dumps(error.__dict__)


def test_smoke_cli_prints_only_safe_http_diagnostics(monkeypatch, capsys):
    from min_agent.llm import smoke

    failure = http_error(httpx.Response(429, json={"error": {"code": "slow_down", "type": "rate_limit_error", "message": "unit-test-token"}}, headers={"Retry-After": "4"}))

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(smoke, "run_live_smoke", fail)
    monkeypatch.setattr("sys.argv", ["smoke", "--credentials-file", "not-read.json", "--allow-live"])
    assert smoke.main() == 1
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert payload["error"] == "http_429" and payload["diagnostics"]["api_error_code"] == "slow_down"
    assert payload["diagnostics"]["retry_after_seconds"] == 4
    assert "unit-test-token" not in output.out + output.err
