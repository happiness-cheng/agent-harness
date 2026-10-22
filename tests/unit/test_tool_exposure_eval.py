"""离线检查消融实验的请求边界；不验证模型自然语言表现。"""

import json
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace

import pytest

from min_agent.llm.client import LLMError
from min_agent.models import ModelResponse


@pytest.fixture
def experiment(monkeypatch, tmp_path):
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    namespace = runpy.run_path(str(scripts / "eval_tool_exposure.py"))
    prompt = tmp_path / "prompt.md"
    prompt.write_text("只依据真实上下文帮助用户。", encoding="utf-8")
    report = tmp_path / "report.json"
    args = ["eval", "--prompt", str(prompt), "--report", str(report),
            "--credentials-file", "OFFLINE_ONLY", "--proxy", "http://127.0.0.1:7890"]
    return namespace["main"], args, report


def forbidden(*args, **kwargs):
    raise AssertionError("不应读取凭据或联网")


def test_no_consent_stops_before_credentials(experiment, monkeypatch):
    main, args, report = experiment
    monkeypatch.setitem(main.__globals__, "DeepSeekSettings", SimpleNamespace(from_credentials_file=forbidden))
    monkeypatch.setattr(sys, "argv", args)
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert not report.exists()


def test_existing_report_is_preserved_before_credentials(experiment, monkeypatch):
    main, args, report = experiment
    report.write_text("保留已有结果", encoding="utf-8")
    monkeypatch.setitem(main.__globals__, "DeepSeekSettings", SimpleNamespace(from_credentials_file=forbidden))
    monkeypatch.setattr(sys, "argv", [*args, "--allow-live"])
    with pytest.raises(FileExistsError):
        main()
    assert report.read_text(encoding="utf-8") == "保留已有结果"


def install_model(main, monkeypatch, *, fail=False):
    sent = []

    def settings(path, **options):
        assert path == "OFFLINE_ONLY"
        assert options["max_requests"] == 8
        return SimpleNamespace(**options)

    class Model:
        def __init__(self, settings, **kwargs):
            self.limit = settings.max_requests
            self.request_count = 0
            self.usage = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def generate(self, messages, *, tools=None):
            assert self.request_count < self.limit
            self.request_count += 1
            sent.append(([message.model_dump() for message in messages], tools))
            if fail:
                raise LLMError("network_error")
            return ModelResponse(finish_reason="stop", content="离线合成回答")

    monkeypatch.setitem(main.__globals__, "DeepSeekSettings", SimpleNamespace(from_credentials_file=settings))
    monkeypatch.setitem(main.__globals__, "DeepSeekResponsesModel", Model)
    return sent


def test_eight_requests_keep_context_fixed_and_vary_only_tools(experiment, monkeypatch):
    main, args, report = experiment
    sent = install_model(main, monkeypatch)
    monkeypatch.setattr(sys, "argv", [*args, "--allow-live"])
    assert main() == 0
    saved = json.loads(report.read_text(encoding="utf-8"))
    assert saved["requests"] == saved["request_limit"] == len(saved["records"]) == len(sent) == 8
    groups = {}
    for messages, tools in sent:
        assert [message["role"] for message in messages] == ["system", "user", "user"]
        groups.setdefault(messages[-1]["content"], []).append((messages, tools))
    assert len(groups) == 2
    for group in groups.values():
        assert len(group) == 4
        assert all(messages == group[0][0] for messages, _ in group)
        assert sorted(len(tools or []) for _, tools in group) == [0, 0, 5, 5]


def test_first_failure_is_saved_without_retry(experiment, monkeypatch):
    main, args, report = experiment
    sent = install_model(main, monkeypatch, fail=True)
    monkeypatch.setattr(sys, "argv", [*args, "--allow-live"])
    assert main() == 1
    saved = json.loads(report.read_text(encoding="utf-8"))
    assert saved["requests"] == len(saved["records"]) == len(sent) == 1
    assert saved["records"][0]["error"] == "network_error"
