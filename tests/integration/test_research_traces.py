import json
from hashlib import sha256
from uuid import uuid4

from fastapi.testclient import TestClient

from min_agent.application import HarnessApplication
from min_agent.models import ModelResponse, ToolCall
from min_agent.web.app import create_app


def test_child_search_read_trace_is_linked_persistent_and_not_overwritten(tmp_path,monkeypatch):
    import min_agent.application as application
    class Search:
        def __init__(self,**kwargs):pass
        def search(self,query):
            return {"status":"ok","results":[{"title":"Source","url":"https://example.com/rule","summary":"需要正文"}]}
    class Reader:
        def __init__(self,fetcher,artifacts):self.artifacts=artifacts
        def read(self,url,**kwargs):
            identity=self.artifacts.save("A verified rule.",source=url)
            return {"artifact_id":identity,"source":url,"start":0,"end":16,"text":"A verified rule."}
    monkeypatch.setattr(application,"SearchService",Search)
    monkeypatch.setattr(application,"WebReader",Reader)
    class Model:
        count=0
        def generate(self,messages,**kwargs):
            self.count+=1
            if self.count<=3:
                name,args=[("research",{"question":"合成问题"}),
                    ("search",{"query":"公开合成查询"}),
                    ("read_webpage",{"url":"https://example.com/rule"})][self.count-1]
                return ModelResponse(finish_reason="tool_calls",tool_calls=[ToolCall(id=str(self.count),name=name,arguments=args)])
            if self.count==4:
                value=json.loads(json.loads(next(m.content for m in reversed(messages) if m.role=="tool"))["result"])
                return ModelResponse(finish_reason="stop",content=json.dumps({"conclusion":"有原文","citations":[{
                    "url":value["source"],"artifact_id":value["artifact_id"],"start":0,"end":16,"quote":"A verified rule."
                }],"limitations":[],"conflicts":[],"uncertainties":[],"next_queries":[]}))
            return ModelResponse(finish_reason="stop",content="已按实际工具结果回复")
    path=tmp_path/"traces.sqlite3";harness=HarnessApplication(path,model=Model())
    identity=harness.create_session()["session_id"];harness.allow_web(identity,True)
    harness.submit(identity,"调查合成公开问题");harness.wait(identity)
    first=harness.snapshot(identity)
    assert first["task"]["status"]=="completed"
    events=first["trace"]
    research=next(event for event in events if event["tool_ref"]==sha256(b"research").hexdigest())
    child=next(event for event in events if event["operation"]=="run" and event["run_id"]!=first["task"]["run_id"])
    assert child["parent_call_id"]==research["call_id"]
    assert {sha256(b"search").hexdigest(),sha256(b"read_webpage").hexdigest()} <= {
        event["tool_ref"] for event in events if event["run_id"]==child["run_id"]}
    assert "https://example.com" not in json.dumps(events)
    first_run=first["task"]["run_id"]
    harness.submit(identity,"后续普通问题");harness.wait(identity);harness.close()
    harness=HarnessApplication(path)
    try:
        client=TestClient(create_app(harness),base_url="http://127.0.0.1")
        client.get("/api/bootstrap")
        restored=client.get(f"/api/sessions/{identity}").json()
        assert len(restored["trace_runs"])==2
        response=client.get(f"/api/sessions/{identity}/traces/{first_run}")
        assert response.status_code==200 and response.json()["events"]==events
        other=harness.create_session()["session_id"]
        assert client.get(f"/api/sessions/{other}/traces/{first_run}").status_code==404
    finally:harness.close()


def test_trace_run_history_has_an_explicit_retention_bound(tmp_path):
    from min_agent.trace import TraceRecorder,TraceContext,record_run_trace
    harness=HarnessApplication(tmp_path/"retention.sqlite3")
    try:
        identity=harness.create_session()["session_id"];context=harness.context(identity)
        runs=[uuid4() for _ in range(3)]
        for run in runs:
            trace=TraceRecorder()
            with trace.span("run",run_id=run,context=TraceContext() ) as span:span.finish("completed")
            record_run_trace(harness.sessions,context,trace,run_id=run,budget_id=None,status="completed",limit=2)
        saved=harness.sessions.extension(context,"trace_runs",[])
        assert [entry["run_id"] for entry in saved]==[str(run) for run in runs[-2:]]
    finally:harness.close()


def test_bound_child_research_rejects_other_session_before_model_or_budget_use(tmp_path):
    import pytest
    from min_agent.artifacts import ArtifactStore
    from min_agent.budget import BudgetLimits
    from min_agent.budget_persistence import SessionBudgets
    from min_agent.research import ResearchService
    from tests.fakes.model import ScriptedModel
    harness=HarnessApplication(tmp_path/"ownership.sqlite3")
    try:
        context=harness.context(harness.create_session()["session_id"])
        other=harness.context(harness.create_session()["session_id"])
        budgets=SessionBudgets(harness.sessions)
        budget_id=budgets.create(context,BudgetLimits(model_calls=3,tool_calls=3))
        ledger=budgets.open(context,budget_id)
        segment=ledger.start_segment(BudgetLimits(model_calls=3,tool_calls=3))
        model=ScriptedModel([])
        before=ledger.snapshot()
        with pytest.raises(PermissionError):
            ResearchService(model,object(),object(),ArtifactStore(harness.sessions,other)).run("公开问题",segment)
        assert ledger.snapshot()==before and model.calls==[]
    finally:harness.close()


def test_bound_isolated_runtime_requires_an_active_matching_parent(tmp_path):
    import pytest
    from min_agent.budget import BudgetLimits
    from min_agent.budget_persistence import SessionBudgets
    from min_agent.runtime import Runtime
    from min_agent.trace import TraceContext
    from tests.fakes.model import ScriptedModel
    harness=HarnessApplication(tmp_path/"parent.sqlite3")
    try:
        context=harness.context(harness.create_session()["session_id"])
        budgets=SessionBudgets(harness.sessions)
        budget_id=budgets.create(context,BudgetLimits(model_calls=3,tool_calls=3))
        segment=budgets.open(context,budget_id).start_segment(BudgetLimits(model_calls=3,tool_calls=3))
        model=ScriptedModel([])
        runtime=Runtime(model,budget=segment)
        with pytest.raises(RuntimeError):
            runtime.run("子任务",store=harness.sessions,execution_context=context)
        harness.sessions.begin(context)
        with pytest.raises(PermissionError):
            runtime.run("子任务",store=harness.sessions,execution_context=context,
                trace_context=TraceContext(session_id=context.session_id,task_id=uuid4()))
        assert model.calls==[] and segment.usage.model_calls==0
    finally:harness.close()
