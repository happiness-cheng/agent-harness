"""候选选择器的评测设施，不等于已接入生产加载器。"""
import json
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def api():
    return runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/eval_skill_retention.py"))


@pytest.mark.parametrize("selection", [
    {"keep": ["unknown"], "release": [], "uncertain": []},
    {"keep": ["a", "a"], "release": ["b"], "uncertain": []},
    {"keep": ["a"], "release": [], "uncertain": []},
    {"keep": "a", "release": ["b"], "uncertain": []},
    None,
])
def test_bad_selection_is_rejected_with_conservative_fallback(api, selection):
    result = api["validate_selection"](selection, ["a", "b"])
    assert result["valid"] is False
    assert result["retained"] == ["a", "b"]


def test_uncertain_is_retained(api):
    result = api["validate_selection"]({"keep": ["a"], "release": [], "uncertain": ["b"]}, ["a", "b"])
    assert result["valid"] is True and result["retained"] == ["a", "b"]


def test_expected_labels_are_not_sent_to_model(api):
    suite = {"skills": [{"id": "a", "body": "原规程"}], "selector_prompt": "选择整份Skill"}
    case = {"candidate_ids": ["a"], "current_request": "当前任务", "task_context": "上下文",
            "expected": {"must_retain": ["a"], "secret_gold": "GOLD_DO_NOT_SEND"}}
    messages = api["selector_messages"](suite, case)
    payload = json.dumps([m.model_dump() for m in messages], ensure_ascii=False)
    assert "原规程" in payload and "当前任务" in payload
    assert "GOLD_DO_NOT_SEND" not in payload and "must_retain" not in payload


def test_savings_do_not_cancel_missing_required_skill(api):
    suite = {"skills": [{"id": "a", "name": "needed", "body": "必要规则"},
                         {"id": "b", "name": "unrelated", "body": "无关规则"}]}
    case = {"candidate_ids": ["a", "b"], "expected": {"must_retain": ["a"], "must_release": ["b"], "prefer_uncertain": []}}
    result = api["score"](suite, case, {"keep": [], "release": ["a", "b"], "uncertain": []})
    assert result["retained_utf8_bytes"] == 0
    assert result["missing_required"] == ["a"] and result["safety_pass"] is False


@pytest.fixture
def command(api, tmp_path):
    root = Path(__file__).resolve().parents[2]
    report = tmp_path / "report.json"
    args = ["eval", "--suite", str(root / "docs/acceptance/s05-skill-retention-suite-v1.json"),
            "--report", str(report), "--credentials-file", "OFFLINE_ONLY", "--proxy", "http://127.0.0.1:7890"]
    return api["main"], args, report


def forbidden(*args, **kwargs): raise AssertionError("不得读凭据或调用模型")


def test_baseline_does_not_read_credentials(command, monkeypatch):
    main, args, report = command
    monkeypatch.setitem(main.__globals__, "DeepSeekSettings", SimpleNamespace(from_credentials_file=forbidden))
    monkeypatch.setattr(sys, "argv", args)
    assert main() == 0
    assert json.loads(report.read_text(encoding="utf-8"))["requests"] == 0


def test_existing_report_is_preserved_before_credentials(command, monkeypatch):
    main, args, report = command
    report.write_text("keep", encoding="utf-8")
    monkeypatch.setitem(main.__globals__, "DeepSeekSettings", SimpleNamespace(from_credentials_file=forbidden))
    monkeypatch.setattr(sys, "argv", [*args, "--allow-live"])
    with pytest.raises(FileExistsError): main()
    assert report.read_text() == "keep"


@pytest.mark.parametrize("fail", [False, True])
def test_live_limit_no_tools_and_no_retry(command, monkeypatch, fail):
    from min_agent.llm.client import LLMError
    from min_agent.models import ModelResponse
    main, args, report = command
    def settings(path, **options):
        assert path == "OFFLINE_ONLY" and options["max_requests"] == 12
        return SimpleNamespace(**options)
    class Model:
        def __init__(self, *args, **kwargs): self.request_count, self.usage = 0, []
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def generate(self, messages, *, tools):
            assert tools is None and self.request_count < 12
            self.request_count += 1
            if fail: raise LLMError("network_error")
            candidates = json.loads(messages[1].content)["loaded_candidates"]
            return ModelResponse(finish_reason="stop", content=json.dumps({"keep": [s["id"] for s in candidates], "release": [], "uncertain": []}))
    monkeypatch.setitem(main.__globals__, "DeepSeekSettings", SimpleNamespace(from_credentials_file=settings))
    monkeypatch.setitem(main.__globals__, "DeepSeekResponsesModel", Model)
    monkeypatch.setattr(sys, "argv", [*args, "--allow-live"])
    assert main() == (1 if fail else 0)
    saved = json.loads(report.read_text(encoding="utf-8"))
    assert saved["requests"] == (1 if fail else 12)
    assert saved["run_state"] == ("failed" if fail else "finished")
