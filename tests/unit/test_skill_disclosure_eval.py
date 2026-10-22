"""Skill真实实验的离线调用门与执行白名单，不验证模型语义。"""

import json
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace

import pytest

from min_agent.llm.client import LLMError
from min_agent.models import ModelResponse, ToolCall


@pytest.fixture
def experiment(tmp_path):
    root = Path(__file__).resolve().parents[2]
    main = runpy.run_path(str(root / "scripts/eval_skill_disclosure.py"))["main"]
    report = tmp_path / "result.json"
    args = ["eval", "--audit-report", str(root / "docs/acceptance/s01-skill-disclosure-20261022.json"),
            "--prompt", str(root / "src/min_agent/prompts/system.md"), "--report", str(report),
            "--credentials-file", "OFFLINE_ONLY", "--proxy", "http://127.0.0.1:7890"]
    return main, args, report


def install(main, monkeypatch, mode="read"):
    sent = []

    def settings(path, **options):
        assert path == "OFFLINE_ONLY" and options["max_requests"] == 8
        return SimpleNamespace(**options)

    class Model:
        def __init__(self, *args, **kwargs):
            self.request_count = 0
            self.usage = []

        def __enter__(self): return self
        def __exit__(self, *args): pass

        def generate(self, messages, *, tools=None):
            self.request_count += 1
            assert self.request_count <= 8
            sent.append(([m.model_copy(deep=True) for m in messages], tools))
            if mode == "error": raise LLMError("network_error")
            if mode == "blocked":
                return ModelResponse(finish_reason="tool_calls", tool_calls=[
                    ToolCall(id="blocked", name="todo_add", arguments={"title": "不得执行"})])
            return ModelResponse(finish_reason="tool_calls", tool_calls=[
                ToolCall(id=f"read-{self.request_count}", name="read_skill", arguments={"name": "writing-polish"})])

    monkeypatch.setitem(main.__globals__, "DeepSeekSettings", SimpleNamespace(from_credentials_file=settings))
    monkeypatch.setitem(main.__globals__, "DeepSeekResponsesModel", Model)
    return sent


def test_consent_before_credentials(experiment, monkeypatch):
    main, args, report = experiment
    install(main, monkeypatch)
    def forbidden(*args, **kwargs): raise AssertionError("未同意不得读凭据")
    monkeypatch.setitem(main.__globals__, "DeepSeekSettings", SimpleNamespace(from_credentials_file=forbidden))
    monkeypatch.setattr(sys, "argv", args)
    with pytest.raises(SystemExit): main()
    assert not report.exists()


def test_report_not_overwritten(experiment, monkeypatch):
    main, args, report = experiment
    install(main, monkeypatch)
    def forbidden(*args, **kwargs): raise AssertionError("已有报告时不得读凭据")
    monkeypatch.setitem(main.__globals__, "DeepSeekSettings", SimpleNamespace(from_credentials_file=forbidden))
    report.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [*args, "--allow-live"])
    with pytest.raises(FileExistsError): main()
    assert report.read_text() == "keep"


def test_eight_call_limit_and_progressive_body(experiment, monkeypatch):
    main, args, report = experiment
    sent = install(main, monkeypatch)
    monkeypatch.setattr(sys, "argv", [*args, "--allow-live"])
    assert main() == 0
    saved = json.loads(report.read_text(encoding="utf-8"))
    assert saved["requests"] == len(sent) == 8
    assert all(case["status"] == "step_limit" for case in saved["cases"])
    for index, (messages, tools) in enumerate(sent):
        assert [tool["name"] for tool in tools] == ["read_skill"]
        text = json.dumps([m.model_dump() for m in messages], ensure_ascii=False)
        assert ("S01_POLISH_BODY" in text) == bool(index % 2)
    assert all(len(case["executions"]) == 1 for case in saved["cases"])


def test_non_skill_tools_never_executed(experiment, monkeypatch):
    main, args, report = experiment
    sent = install(main, monkeypatch, "blocked")
    monkeypatch.setattr(sys, "argv", [*args, "--allow-live"])
    assert main() == 0
    saved = json.loads(report.read_text(encoding="utf-8"))
    assert saved["requests"] == len(sent) == 4
    assert all(case["status"] == "blocked_tool" and not case["executions"] for case in saved["cases"])


def test_transport_failure_stops_without_retry(experiment, monkeypatch):
    main, args, report = experiment
    sent = install(main, monkeypatch, "error")
    monkeypatch.setattr(sys, "argv", [*args, "--allow-live"])
    assert main() == 1
    saved = json.loads(report.read_text(encoding="utf-8"))
    assert saved["requests"] == len(sent) == 1
    assert saved["run_state"] == "failed"
