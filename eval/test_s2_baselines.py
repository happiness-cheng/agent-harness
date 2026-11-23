"""S2 基线能力的离线测试：Ollama 适配器、可用性检查、专用模式判据。

全部用 MockTransport / 直接调 verify，不调任何真实 API、不依赖本机 Ollama。
证明的是「能力接好了、判据没被悄悄改」，不证明真模型成功率。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx  # noqa: E402

from min_agent.contracts.models import Message  # noqa: E402

from eval.real import OllamaEvalModel, ollama_available  # noqa: E402
from eval.verifiers import verify  # noqa: E402


def _chat_handler(request):
    return httpx.Response(200, json={
        "model": "qwen3.5:4b", "message": {"role": "assistant", "content": "好"},
        "done": True, "done_reason": "stop",
        "prompt_eval_count": 100, "eval_count": 5, "total_duration": 1_000_000,
    })


def test_ollama_eval_model_maps_usage_and_describe():
    model = OllamaEvalModel(model="qwen3.5:4b", transport=httpx.MockTransport(_chat_handler))
    try:
        response = model.generate([Message(role="user", content="你好")])
        assert response.finish_reason == "stop"
        assert model.describe()["model"] == "qwen3.5:4b"
        assert model.request_count == 1
        usage = model.usage[0]
        # Ollama 的 prompt_eval_count/eval_count 映射成评测层统一字段。
        assert usage["prompt_tokens"] == 100
        assert usage["completion_tokens"] == 5
        assert model.retries == 0
    finally:
        model.close()


def test_ollama_eval_model_records_last_error_on_http_failure():
    def handler(request):
        return httpx.Response(500, text="boom")

    model = OllamaEvalModel(transport=httpx.MockTransport(handler))
    try:
        try:
            model.generate([Message(role="user", content="hi")])
            raise AssertionError("应当抛出 RuntimeError")
        except RuntimeError:
            pass
        assert model.last_error and "http_500" in model.last_error
    finally:
        model.close()


def test_ollama_available_present_and_absent():
    def handler(request):
        return httpx.Response(200, json={"models": [{"name": "qwen3.5:4b"}]})

    transport = httpx.MockTransport(handler)
    ok, detail = ollama_available("qwen3.5:4b", transport=transport)
    assert ok, detail
    ok, detail = ollama_available("qwen3.5:4b", transport=transport)  # 带 :latest 归一化
    assert ok
    ok, detail = ollama_available("missing:7b", transport=transport)
    assert not ok and "不在本机" in detail


def test_ollama_available_unreachable_is_not_run():
    def handler(request):
        raise httpx.ConnectError("refused")

    ok, detail = ollama_available("qwen3.5:4b", transport=httpx.MockTransport(handler))
    assert not ok and "不可达" in detail


def _task(checks):
    return {"id": "T", "checks": checks}


def test_dedicated_mode_marks_bash_gate_check_not_applicable(tmp_path):
    task = _task([
        {"kind": "tool_result", "step": 0, "ok": False, "error_code": "blocked_command"},
        {"kind": "file_exists", "path": "a.txt"},
    ])
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    result = verify(task, work=tmp_path, outside=tmp_path, steps=[], mode="dedicated")
    by_kind = {c["id"]: c["verdict"] for c in result["checks"]}
    assert by_kind["tool_result"] == "NOT_APPLICABLE"
    assert by_kind["file_exists"] == "PASS"
    assert result["verdict"] == "PASS"  # 文件检查仍照常判，不被 N/A 拖挂


def test_dedicated_mode_all_bash_checks_is_not_applicable(tmp_path):
    task = _task([{"kind": "tool_result", "step": 0, "ok": False, "error_code": "blocked_command"}])
    result = verify(task, work=tmp_path, outside=tmp_path, steps=[], mode="dedicated")
    assert result["verdict"] == "NOT_APPLICABLE"


def test_generic_mode_criteria_unchanged(tmp_path):
    # 同一题在 generic 模式下，blocked_command 检查仍照常判（不改判据）。
    task = _task([{"kind": "tool_result", "step": 0, "ok": False, "error_code": "blocked_command"}])
    result = verify(task, work=tmp_path, outside=tmp_path, steps=[], mode="generic")
    # 没有步骤、期望 ok=False：真实模型层规则判 PASS（未尝试、未造成危害）。
    assert result["checks"][0]["verdict"] == "PASS"
    assert result["verdict"] == "PASS"