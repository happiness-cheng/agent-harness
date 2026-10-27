"""固定判断只验证接线；真实模型语义评测另行授权。"""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from min_agent.models import Message, ModelResponse


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("skill_compaction_probe", ROOT / "scripts/audit_skill_compaction.py")
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)
SUITE = json.loads((ROOT / "docs/acceptance/s07-skill-compaction-suite-v1.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", SUITE["cases"], ids=lambda case: case["id"])
def test_fixed_integration_acceptance(case):
    record = PROBE.run_case(case)
    assert record["passed"], {"expected": record["expected"], "observed": record["observed"], "error": record["error"]}


def test_ordinary_history_compression_receives_records_without_skill_bodies():
    from min_agent.sessions import SessionStore
    from uuid import uuid4

    class Inspect(PROBE.OfflineCompactor):
        def _summarize(self, state, source, phase):
            self.source = source
            return super()._summarize(state, source, phase)

    store = SessionStore()
    context = store.create(uuid4())
    compactor = Inspect(None, store, context, None)
    messages = [Message(role="user", content="问题")] + PROBE.read("p", "polish", PROBE.BODIES[0])
    state = {"version": 1, "projections": {}, "summary": None, "covered": 0, "covered_hashes": []}
    compactor.compact_history(state, messages, [], len(messages))
    assert PROBE.BODIES[0] not in json.dumps(compactor.source, ensure_ascii=False)
    archive = list(store.extension(context, "memory_journal", {}).values())[-1]
    assert PROBE.BODIES[0] in json.dumps(archive["messages"], ensure_ascii=False)


class Budget:
    def __init__(self, count=3):
        self.remaining = SimpleNamespace(model_calls=count)
        self.consumed = []

    def consume(self, resource, *, purpose):
        self.consumed.append((resource, purpose))


class SelectorModel:
    def __init__(self, *, response=None, error=None):
        self.calls = []
        self.response = response or ModelResponse(finish_reason="stop", content='{"keep":["p"],"release":[],"uncertain":[]}')
        self.error = error

    def generate(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if self.error:
            raise self.error
        return self.response


def candidate():
    return [{"name": "polish", "body": PROBE.BODIES[0], "body_sha256": "digest", "source_tool_call_id": "p"}]


def test_selection_is_no_tool_and_uses_shared_budget():
    from min_agent.skill_retention import SkillRetentionSelector

    model, budget = SelectorModel(), Budget()
    selector = SkillRetentionSelector(model, budget)
    assert selector.select(candidate(), {"current_request": "继续润色"})["keep"] == ["p"]
    assert budget.consumed == [("model_calls", "compaction")]
    assert model.calls[0][1].get("tools") is None
    assert len(model.calls) == 1


@pytest.mark.parametrize("kind", ["budget", "window", "error", "invalid_json", "tool_call"])
def test_selection_failure_does_not_retry_or_drop_skills(kind):
    from min_agent.skill_retention import SkillRetentionSelector, normalize_selection

    from min_agent.models import ToolCall
    model, budget, window = SelectorModel(), Budget(), 32768
    if kind == "budget":
        budget = Budget(1)
    elif kind == "window":
        window = 100
    elif kind == "error":
        model.error = RuntimeError("离线失败")
    elif kind == "invalid_json":
        model.response = ModelResponse(finish_reason="stop", content="invalid JSON")
    elif kind == "tool_call":
        model.response = ModelResponse(finish_reason="tool_calls", tool_calls=[ToolCall(id="bad", name="write", arguments={})])
    raw = SkillRetentionSelector(model, budget, input_window=window).select(candidate(), {})
    decisions = normalize_selection(raw, candidate())
    assert decisions["decisions"] == {"p": "uncertain"}
    assert decisions["fallback"] is True
    assert len(model.calls) == (0 if kind in {"budget", "window"} else 1)


def test_stop_prevents_selection_call():
    from min_agent.context import ContextLimit
    from min_agent.skill_retention import SkillRetentionSelector

    control = SimpleNamespace(snapshot=lambda: {"stop": True})
    model, budget = SelectorModel(), Budget()
    with pytest.raises(ContextLimit):
        SkillRetentionSelector(model, budget, control=control).select(candidate(), {})
    assert not model.calls and not budget.consumed


@pytest.mark.parametrize("selection", [
    None, {"keep": ["p"], "release": []},
    {"keep": ["p"], "release": ["p"], "uncertain": []},
    {"keep": [], "release": [], "uncertain": []},
    {"keep": ["unknown"], "release": [], "uncertain": []},
    {"keep": ["p"], "release": [], "uncertain": [], "permissions": ["write"]},
])
def test_invalid_partition_preserves_whole_candidate(selection):
    from min_agent.skill_retention import normalize_selection

    assert normalize_selection(selection, candidate()) == {"decisions": {"p": "uncertain"}, "fallback": True}


def test_application_wires_selection_to_same_model_budget_and_final_input(tmp_path):
    from min_agent.application import HarnessApplication
    from min_agent.skill_retention import PROMPT

    class OfflineApplicationModel:
        def __init__(self):
            self.selection_calls, self.main_calls = [], []

        def generate(self, messages, *, tools=None):
            if messages[0].content == PROMPT:
                self.selection_calls.append(messages)
                assert tools is None
                return ModelResponse(finish_reason="stop", content=json.dumps(
                    {"keep": ["p"], "release": ["e"], "uncertain": ["d"]}))
            if tools is None:
                # 兼容公开分支的JSON摘要及主工作区的来源ID选择，均为离线替身。
                payload = json.loads(messages[-1].content)
                if "units" in payload:
                    return ModelResponse(finish_reason="stop", content='{"selections":[]}')
                return ModelResponse(finish_reason="stop", content=json.dumps({
                    "facts": ["旧交互整理"], "constraints": [], "corrections": [],
                    "open_questions": [], "source_references": []}))
            self.main_calls.append(messages)
            return ModelResponse(finish_reason="stop", content="固定离线答复")

    model = OfflineApplicationModel()
    harness = HarnessApplication(tmp_path / "skill-retention.sqlite3", model=model, window=50000)
    try:
        identity = harness.create_session()["session_id"]
        context = harness.context(identity)
        task = harness.sessions.begin(context)
        old = [Message(role="user", content="旧工作过程。" * 9000)]
        for call_id, name, body in zip(("p", "e", "d"), PROBE.NAMES, PROBE.BODIES):
            old += PROBE.read(call_id, name, body)
        old += [Message(role="assistant", content="已处理"),
                Message(role="user", content="核验已经结束，继续润色和排错"),
                Message(role="assistant", content="继续处理")]
        # 历史压缩请求自身能装下，但加上主提示和完整工具清单的主请求超窗。
        # 公开分支旧压缩器不支持长输入分块，本用例只验证应用接线。
        from min_agent.context import request_bound
        harness.window = request_bound(old) + 8192
        harness.sessions.append(context, task.task_id, old)
        harness.sessions.finish(context, task.task_id, status="completed")
        harness.submit(identity, "按规程收尾")
        harness.wait(identity)
        snapshot = harness.snapshot(identity)
        assert snapshot["task"]["status"] == "completed", {
            "task": snapshot["task"], "selection_calls": len(model.selection_calls),
            "main_calls": len(model.main_calls), "error": snapshot["error"]}
        assert len(model.selection_calls) == len(model.main_calls) == 1
        assert [PROBE.body_count(model.main_calls[0], body) for body in PROBE.BODIES] == [1, 0, 1]
        assert snapshot["budget"]["usage"]["by_purpose"]["compaction"]["model_calls"] >= 1
    finally:
        harness.close()
