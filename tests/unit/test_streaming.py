"""QWEN-STREAM-001：流式显示通道的离线测试（MockTransport，无外部请求）。"""

import json
import time

import httpx
import pytest

from min_agent.llm.client import LLMError
from min_agent.llm.qianwen import QianwenChatModel, QianwenSettings
from min_agent.contracts.models import Message, ModelResponse
from min_agent.runtime.streaming import StreamSink, StreamingModel


def sse(*rows: str) -> bytes:
    return ("".join(row + "\n\n" for row in rows)).encode()


def settings():
    return QianwenSettings(api_key="sk-test", max_output_tokens=64)


def test_sink_returns_new_chunks_then_closes():
    sink = StreamSink()
    sink.push("你")
    sink.push("好")
    chunks, cursor, closed = sink.read(0, timeout=0.1)
    assert chunks == ["你", "好"] and cursor == 2 and closed is False

    chunks, cursor, closed = sink.read(cursor, timeout=0.1)
    assert chunks == [] and cursor == 2 and closed is False

    sink.close()
    chunks, cursor, closed = sink.read(cursor, timeout=0.1)
    assert chunks == [] and closed is True


def test_sink_overflow_stops_display_but_does_not_raise():
    sink = StreamSink(limit=4)
    sink.push("1234")
    sink.push("5678")

    assert sink.overflow is True
    chunks, _, _ = sink.read(0, timeout=0.1)
    assert chunks == ["1234"]


def test_sink_rejects_bad_cursor():
    with pytest.raises(ValueError):
        StreamSink().read(-1)


def test_streaming_model_forwards_deltas_and_returns_final_response():
    class Inner:
        _provider = "fake"

        def generate_stream(self, messages, *, tools=None, on_delta):
            on_delta("你")
            on_delta("好")
            return ModelResponse(finish_reason="stop", content="你好")

        def generate(self, messages, *, tools=None):  # pragma: no cover - 不应被调用
            raise AssertionError("流式包装不应回落到非流式")

    sink = StreamSink()
    model = StreamingModel(Inner(), sink)
    result = model.generate([Message(role="user", content="问候")])

    assert result.content == "你好"
    assert sink.read(0, timeout=0.1)[0] == ["你", "好"]
    assert model._provider == "fake"


def test_qianwen_stream_assembles_text_and_usage():
    def handler(request):
        return httpx.Response(200, content=sse(
            'data: {"choices":[{"delta":{"content":"你"},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{"content":"好"},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
            '"usage":{"prompt_tokens":5,"completion_tokens":2,"total_tokens":7}}',
            'data: [DONE]',
        ), headers={"content-type": "text/event-stream"})

    model = QianwenChatModel(settings(), transport=httpx.MockTransport(handler))
    pieces = []
    result = model.generate_stream([Message(role="user", content="问候")], on_delta=pieces.append)

    assert result.finish_reason == "stop" and result.content == "你好"
    assert "".join(pieces) == "你好"
    assert model.usage[-1]["total_tokens"] == 7


def test_qianwen_stream_assembles_fragmented_tool_arguments():
    def handler(request):
        return httpx.Response(200, content=sse(
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_a",'
            '"function":{"name":"calculator","arguments":"{\\"expression\\": "}}]}}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
            '"function":{"arguments":"\\"1+1\\"}"}}]}}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
            'data: [DONE]',
        ), headers={"content-type": "text/event-stream"})

    model = QianwenChatModel(settings(), transport=httpx.MockTransport(handler))
    result = model.generate_stream([Message(role="user", content="算一下")], on_delta=lambda text: None)

    assert result.finish_reason == "tool_calls"
    assert [(call.id, call.name, call.arguments) for call in result.tool_calls] == [
        ("call_a", "calculator", {"expression": "1+1"})]


def test_qianwen_stream_truncated_tool_call_is_length_not_executable():
    def handler(request):
        return httpx.Response(200, content=sse(
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_a",'
            '"function":{"name":"calculator","arguments":"{\\"expr"}}]}}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"length"}]}',
            'data: [DONE]',
        ), headers={"content-type": "text/event-stream"})

    model = QianwenChatModel(settings(), transport=httpx.MockTransport(handler))
    result = model.generate_stream([Message(role="user", content="算一下")], on_delta=lambda text: None)

    assert result.finish_reason == "length"
    assert result.tool_calls == []


def test_qianwen_stream_maps_http_402_without_body_leak():
    secret = "仅测试的余额提示"

    def handler(request):
        return httpx.Response(402, json={"error": {"message": secret}})

    model = QianwenChatModel(settings(), transport=httpx.MockTransport(handler))

    with pytest.raises(LLMError) as caught:
        model.generate_stream([Message(role="user", content="问")], on_delta=lambda text: None)

    assert caught.value.code == "http_402"
    assert secret not in str(caught.value)


def test_qianwen_stream_rejects_invalid_json_chunk():
    def handler(request):
        return httpx.Response(200, content=sse("data: not-json"), headers={"content-type": "text/event-stream"})

    model = QianwenChatModel(settings(), transport=httpx.MockTransport(handler))

    with pytest.raises(LLMError) as caught:
        model.generate_stream([Message(role="user", content="问")], on_delta=lambda text: None)

    assert caught.value.code == "invalid_response"


class _FakeModel:
    def generate(self, messages, *, tools=None):
        return ModelResponse(finish_reason="stop", content="好")


def test_stream_endpoint_is_409_without_active_stream(tmp_path):
    from fastapi.testclient import TestClient
    from min_agent.application import HarnessApplication
    from min_agent.web.app import create_app

    harness = HarnessApplication(tmp_path / "endpoint.sqlite3", model=_FakeModel())
    with TestClient(create_app(harness), base_url="http://127.0.0.1") as client:
        boot = client.get("/api/bootstrap").json()
        session = client.post("/api/sessions", json={"label": "流"},
                              headers={"x-harness-csrf": boot["csrf"]}).json()

        response = client.get(f"/api/sessions/{session['session_id']}/stream")

        assert response.status_code == 409
        assert response.json()["error"] == "not_streaming"


def test_stream_endpoint_pushes_deltas_and_done(tmp_path):
    from fastapi.testclient import TestClient
    from min_agent.application import HarnessApplication
    from min_agent.web.app import create_app

    harness = HarnessApplication(tmp_path / "push.sqlite3", model=_FakeModel())
    with TestClient(create_app(harness), base_url="http://127.0.0.1") as client:
        boot = client.get("/api/bootstrap").json()
        session = client.post("/api/sessions", json={"label": "流"},
                              headers={"x-harness-csrf": boot["csrf"]}).json()
        sink = harness._open_stream(session["session_id"])
        sink.push("你")
        sink.push("好")
        sink.close()

        response = client.get(f"/api/sessions/{session['session_id']}/stream")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = response.text
        assert '"text": "你"' in body and '"text": "好"' in body
        assert "event: done" in body
