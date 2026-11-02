"""B2 研究观察：真实应用链路、部分事实、许可重查和只读查询。"""
import json
from pathlib import Path
from uuid import uuid4

import pytest

from min_agent.application import HarnessApplication
from min_agent.artifacts import ArtifactStore
from min_agent.models import ModelResponse, ToolCall
from min_agent.research_observation import ResearchObservation

import min_agent.application as application


CONTENT = "Synthetic source sentence."
LENGTH = len(CONTENT)


class _Base:
    read_calls = 0
    revoke = None


class FakeSearch(_Base):
    calls = 0

    def __init__(self, **kwargs):
        pass

    def search(self, query):
        type(self).calls += 1
        if type(self).revoke is not None:
            app, session_id = type(self).revoke
            app.allow_web(session_id, False)
        return {"status": "ok", "results": [{
            "title": "Synthetic", "url": "https://example.com/fact",
            "summary": "summary only"}]}


class FakeReader(_Base):
    calls = 0
    save_text = CONTENT
    range_start = 0
    range_end = LENGTH
    fail = False

    def __init__(self, fetcher, artifacts):
        self.artifacts = artifacts

    def read(self, url, **kwargs):
        type(self).calls += 1
        if type(self).fail:
            raise OSError("synthetic read failure")
        identity = self.artifacts.save(type(self).save_text, source=url)
        return {"artifact_id": identity, "source": url,
                "start": type(self).range_start, "end": type(self).range_end,
                "text": type(self).save_text[type(self).range_start:type(self).range_end],
                "sha256": self.artifacts.get(identity)["sha256"]}


def tool(call_id, name, arguments):
    return ModelResponse(finish_reason="tool_calls",
                         tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)])


def evidence(content=CONTENT, start=0, end=LENGTH, artifact=None, source="https://example.com/fact"):
    return ModelResponse(finish_reason="stop", content=json.dumps({
        "conclusion": "synthetic",
        "citations": [{"url": source, "artifact_id": artifact, "start": start,
                       "end": end, "quote": content}],
        "limitations": [], "conflicts": [], "uncertainties": [], "next_queries": []}))


class Model:
    def __init__(self, responses):
        self.responses = list(responses)
        self.count = 0

    def generate(self, messages, *, tools=None):
        self.count += 1
        return self.responses.pop(0)


@pytest.fixture(autouse=True)
def reset_fakes():
    FakeSearch.calls = 0
    FakeSearch.revoke = None
    FakeReader.calls = 0
    FakeReader.save_text = CONTENT
    FakeReader.range_start = 0
    FakeReader.range_end = LENGTH
    FakeReader.fail = False


def build(tmp_path, monkeypatch, responses, *, enabled=True):
    monkeypatch.setattr(application, "SearchService", FakeSearch)
    monkeypatch.setattr(application, "WebReader", FakeReader)
    app = HarnessApplication(tmp_path / "case.sqlite3", model=Model(responses),
                             research_observation_enabled=enabled)
    sid = app.create_session()["session_id"]
    app.allow_web(sid, True)
    return app, sid


def run_research(app, sid):
    app.submit(sid, "synthetic public question")
    app.wait(sid)


def test_host_observation_matches_sqlite_artifact_and_range(tmp_path, monkeypatch):
    # 父级启动研究，子循环搜索和读取，子循环给出结论后父级结束。
    app, sid = build(tmp_path, monkeypatch, [
        tool("p", "research", {"question": "synthetic public question"}),
        tool("r", "read_webpage", {"url": "https://example.com/fact"}),
        ModelResponse(finish_reason="stop", content="{}"),  # replaced below
        ModelResponse(finish_reason="stop", content="done"),
    ])
    # 子循环最终引用需要读取工具返回的artifact_id，因此用小型模型包装器。
    class Dynamic(Model):
        def generate(self, messages, *, tools=None):
            self.count += 1
            if self.count == 1:
                return tool("p", "research", {"question": "synthetic public question"})
            if self.count == 2:
                return tool("r", "read_webpage", {"url": "https://example.com/fact"})
            if self.count == 3:
                value = json.loads(json.loads(next(m.content for m in reversed(messages)
                                                   if m.role == "tool"))["result"])
                return evidence(artifact=value["artifact_id"])
            return ModelResponse(finish_reason="stop", content="done")

    app.model = Dynamic([])
    try:
        run_research(app, sid)
        observation = app.get_research_observation(sid)
        assert observation.status == "VERIFIED"
        assert observation.verified_citations == 1
        assert observation.execution_authorization == "NOT_GRANTED"
        assert len(observation.documents) == 1
        document = observation.documents[0]
        assert document.start == 0 and document.end == LENGTH
        assert document.ranges[0].start == 0 and document.ranges[0].end == LENGTH
        assert document.sha256 == ArtifactStore(app.sessions, app.context(sid)).get(
            document.artifact_id)["sha256"]
        assert CONTENT not in observation.model_dump_json()
        assert app.get_research_observation(sid, observation.run_ref) == observation
    finally:
        app.close()


def test_invalid_final_keeps_partial_read_facts(tmp_path, monkeypatch):
    app, sid = build(tmp_path, monkeypatch, [])
    class Dynamic(Model):
        def generate(self, messages, *, tools=None):
            self.count += 1
            if self.count == 1:
                return tool("p", "research", {"question": "synthetic public question"})
            if self.count == 2:
                return tool("r", "read_webpage", {"url": "https://example.com/fact"})
            if self.count == 3:
                return ModelResponse(finish_reason="stop", content="{not json")
            return ModelResponse(finish_reason="stop", content="done")
    app.model = Dynamic([])
    try:
        run_research(app, sid)
        observation = app.get_research_observation(sid)
        assert observation.status == "PARTIAL"
        assert len(observation.documents) == 1
        assert observation.verified_citations == 0
    finally:
        app.close()


def test_search_only_is_none_not_verified(tmp_path, monkeypatch):
    app, sid = build(tmp_path, monkeypatch, [])
    class Dynamic(Model):
        def generate(self, messages, *, tools=None):
            self.count += 1
            if self.count == 1:
                return tool("p", "research", {"question": "synthetic public question"})
            if self.count == 2:
                return tool("s", "search", {"query": "public"})
            if self.count == 3:
                return ModelResponse(finish_reason="stop", content=json.dumps({
                    "conclusion": "not enough", "citations": [], "limitations": ["no read"],
                    "conflicts": [], "uncertainties": [], "next_queries": []}))
            return ModelResponse(finish_reason="stop", content="done")
    app.model = Dynamic([])
    try:
        run_research(app, sid)
        observation = app.get_research_observation(sid)
        assert observation.status == "NONE"
        assert observation.documents == ()
        assert observation.verified_citations == 0
    finally:
        app.close()


@pytest.mark.parametrize("enabled", [False, True])
def test_revocation_before_next_read_makes_zero_backend_calls(tmp_path, monkeypatch, enabled):
    app, sid = build(tmp_path, monkeypatch, [], enabled=enabled)
    FakeSearch.revoke = (app, sid)
    class Dynamic(Model):
        def generate(self, messages, *, tools=None):
            self.count += 1
            if self.count == 1:
                return tool("p", "research", {"question": "synthetic public question"})
            if self.count == 2:
                return tool("s", "search", {"query": "public"})
            if self.count == 3:
                return tool("r", "read_webpage", {"url": "https://example.com/fact"})
            return ModelResponse(finish_reason="stop", content="done")
    app.model = Dynamic([])
    try:
        run_research(app, sid)
        assert FakeSearch.calls == 1
        assert FakeReader.calls == 0
        if enabled:
            observation = app.get_research_observation(sid)
            assert observation.status in {"UNKNOWN", "NONE", "WITHHELD"}
            assert observation.documents == ()
    finally:
        app.close()


def test_observation_off_has_no_host_projection(tmp_path, monkeypatch):
    app, sid = build(tmp_path, monkeypatch, [], enabled=False)
    try:
        with pytest.raises(PermissionError, match="disabled"):
            app.get_research_observation(sid)
    finally:
        app.close()


def test_unknown_run_ref_and_wrong_owner_are_withheld(tmp_path, monkeypatch):
    app, sid = build(tmp_path, monkeypatch, [])
    try:
        observation = app.get_research_observation(sid, str(uuid4()))
        assert observation.status == "WITHHELD"
        assert observation.documents == ()
        other = HarnessApplication(tmp_path / "other.sqlite3", owner_id=uuid4(),
                                   research_observation_enabled=True)
        try:
            assert other.get_research_observation(sid).status == "WITHHELD"
        finally:
            other.close()
    finally:
        app.close()


def test_citation_outside_returned_read_range_is_partial(tmp_path, monkeypatch):
    FakeReader.save_text = "Header\nExact quote."
    FakeReader.range_start = 0
    FakeReader.range_end = 6
    app, sid = build(tmp_path, monkeypatch, [])
    class Dynamic(Model):
        def generate(self, messages, *, tools=None):
            self.count += 1
            if self.count == 1:
                return tool("p", "research", {"question": "synthetic public question"})
            if self.count == 2:
                return tool("r", "read_webpage", {"url": "https://example.com/fact"})
            if self.count == 3:
                value = json.loads(json.loads(next(m.content for m in reversed(messages)
                                                   if m.role == "tool"))["result"])
                return evidence(content="Exact quote.", start=7, end=19,
                                artifact=value["artifact_id"])
            return ModelResponse(finish_reason="stop", content="done")
    app.model = Dynamic([])
    try:
        run_research(app, sid)
        observation = app.get_research_observation(sid)
        assert observation.status == "PARTIAL"
        assert observation.verified_citations == 0
    finally:
        app.close()


def test_read_failure_is_withheld(tmp_path, monkeypatch):
    FakeReader.fail = True
    app, sid = build(tmp_path, monkeypatch, [])
    class Dynamic(Model):
        def generate(self, messages, *, tools=None):
            self.count += 1
            if self.count == 1:
                return tool("p", "research", {"question": "synthetic public question"})
            if self.count == 2:
                return tool("r", "read_webpage", {"url": "https://example.com/fact"})
            return ModelResponse(finish_reason="stop", content="done")
    app.model = Dynamic([])
    try:
        run_research(app, sid)
        observation = app.get_research_observation(sid)
        assert observation.status == "WITHHELD"
        assert observation.documents == ()
    finally:
        app.close()


def test_corrupt_or_removed_artifact_is_withheld(tmp_path, monkeypatch):
    app, sid = build(tmp_path, monkeypatch, [])
    class Dynamic(Model):
        def generate(self, messages, *, tools=None):
            self.count += 1
            if self.count == 1:
                return tool("p", "research", {"question": "synthetic public question"})
            if self.count == 2:
                return tool("r", "read_webpage", {"url": "https://example.com/fact"})
            if self.count == 3:
                value = json.loads(json.loads(next(m.content for m in reversed(messages)
                                                   if m.role == "tool"))["result"])
                return evidence(artifact=value["artifact_id"])
            return ModelResponse(finish_reason="stop", content="done")
    app.model = Dynamic([])
    try:
        run_research(app, sid)
        observation = app.get_research_observation(sid)
        assert observation.status == "VERIFIED"
        context = app.context(sid)
        artifacts = app.sessions.extension(context, "artifacts", {})
        identity = observation.documents[0].artifact_id
        artifacts[identity] = {"content": None, "sha256": "0" * 64}
        app.sessions.set_extension(context, "artifacts", artifacts)
        assert app.get_research_observation(sid, observation.run_ref).status == "WITHHELD"
        app.sessions.set_extension(context, "artifacts", {})
        assert app.get_research_observation(sid, observation.run_ref).status == "WITHHELD"
    finally:
        app.close()


def test_restart_reloads_verified_observation(tmp_path, monkeypatch):
    app, sid, owner = None, None, None
    app, sid = build(tmp_path, monkeypatch, [])
    class Dynamic(Model):
        def generate(self, messages, *, tools=None):
            self.count += 1
            if self.count == 1:
                return tool("p", "research", {"question": "synthetic public question"})
            if self.count == 2:
                return tool("r", "read_webpage", {"url": "https://example.com/fact"})
            if self.count == 3:
                value = json.loads(json.loads(next(m.content for m in reversed(messages)
                                                   if m.role == "tool"))["result"])
                return evidence(artifact=value["artifact_id"])
            return ModelResponse(finish_reason="stop", content="done")
    app.model = Dynamic([])
    try:
        run_research(app, sid)
        observation = app.get_research_observation(sid)
        assert observation.status == "VERIFIED"
        owner = app.owner_id
        app.close()
        reopened = HarnessApplication(tmp_path / "case.sqlite3", owner_id=owner,
                                       research_observation_enabled=True)
        try:
            assert reopened.get_research_observation(sid, observation.run_ref).status == "VERIFIED"
        finally:
            reopened.close()
    finally:
        try:
            app.close()
        except Exception:
            pass


def test_new_parent_task_cannot_reuse_old_run_as_current(tmp_path, monkeypatch):
    app, sid = build(tmp_path, monkeypatch, [])
    class Dynamic(Model):
        def generate(self, messages, *, tools=None):
            self.count += 1
            if self.count == 1:
                return tool("p", "research", {"question": "synthetic public question"})
            if self.count == 2:
                return tool("r", "read_webpage", {"url": "https://example.com/fact"})
            if self.count == 3:
                value = json.loads(json.loads(next(m.content for m in reversed(messages)
                                                   if m.role == "tool"))["result"])
                return evidence(artifact=value["artifact_id"])
            return ModelResponse(finish_reason="stop", content="done")
    app.model = Dynamic([])
    try:
        run_research(app, sid)
        observation = app.get_research_observation(sid)
        assert observation.status == "VERIFIED"
        app.model = Model([ModelResponse(finish_reason="stop", content="second")])
        app.submit(sid, "second task")
        app.wait(sid)
        assert app.get_research_observation(sid, observation.run_ref).status == "WITHHELD"
        assert app.get_research_observation(sid).status == "WITHHELD"
    finally:
        app.close()
