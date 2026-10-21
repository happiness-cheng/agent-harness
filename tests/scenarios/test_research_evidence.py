import json
from uuid import uuid4

from min_agent.artifacts import ArtifactStore
from min_agent.budget import BudgetLedger, BudgetLimits
from min_agent.models import ModelResponse, ToolCall
from min_agent.research import ResearchService
from min_agent.sessions import SessionStore
from tests.fakes.model import ScriptedModel


class Search:
    def search(self, query, limit=5):
        return {"status": "ok", "results": [{"title": "Source", "url": "https://example.com/fact", "summary": "needs reading"}]}


def test_research_autonomously_searches_reads_and_rejects_fake_citations():
    sessions = SessionStore(); context = sessions.create(uuid4()); artifacts = ArtifactStore(sessions, context)
    class Reader:
        def read(self, url, **kwargs):
            identity = artifacts.save("The rule is not universal.", source=url)
            return {"artifact_id": identity, "source": url, "start": 0, "end": 26, "text": "The rule is not universal."}
    class Model:
        def __init__(self): self.count = 0
        def generate(self, messages, **kwargs):
            self.count += 1
            if self.count == 1:
                assert 'private-main-message' not in str(messages)
                return ModelResponse(finish_reason="tool_calls", tool_calls=[ToolCall(id="s", name="search", arguments={"query": "public rule"})])
            if self.count == 2:
                assert "https://example.com/fact" in messages[-1].content
                return ModelResponse(finish_reason="tool_calls", tool_calls=[ToolCall(id="r", name="read_webpage", arguments={"url": "https://example.com/fact", "question": "exceptions"})])
            record = json.loads(messages[-1].content)["result"]
            record = json.loads(record)
            return ModelResponse(finish_reason="stop", content=json.dumps({"conclusion": "存在例外", "citations": [{
                "url": record["source"], "artifact_id": record["artifact_id"], "start": 0, "end": 26,
                "quote": "The rule is not universal.",
            }], "limitations": ["仅一份来源"], "conflicts": [], "uncertainties": [], "next_queries": []}))
    ledger = BudgetLedger(BudgetLimits(model_calls=5, tool_calls=5))
    result = ResearchService(Model(), Search(), Reader(), artifacts).run("核对规则的例外", ledger.start_segment(BudgetLimits(model_calls=5, tool_calls=5)))
    assert result["status"] == "completed" and result["evidence"]["limitations"]
    assert ledger.usage.by_purpose["research"].model_calls == 3
    fake = ScriptedModel([ModelResponse(finish_reason="stop", content=json.dumps({"conclusion": "假证据", "citations": [{
        "url": "https://evil.example", "artifact_id": str(uuid4()), "start": 0, "end": 4, "quote": "fake",
    }], "limitations": [], "conflicts": [], "uncertainties": [], "next_queries": []}))])
    result = ResearchService(fake, Search(), Reader(), artifacts).run("核验", ledger.start_segment(BudgetLimits(model_calls=2, tool_calls=2)))
    assert result["status"] == "partial" and result["error"] == "invalid_evidence"


def test_unique_verbatim_quote_can_normalize_model_offset_without_inventing_text():
    sessions=SessionStore();context=sessions.create(uuid4());artifacts=ArtifactStore(sessions,context)
    class Reader:
        def read(self,url,**kwargs):
            identity=artifacts.save("Header\nExact quote.",source=url)
            return {"artifact_id":identity,"source":url,"start":0,"end":19,"text":"Header\nExact quote."}
    class Model:
        count=0
        def generate(self,messages,**kwargs):
            self.count+=1
            if self.count==1:
                return ModelResponse(finish_reason="tool_calls",tool_calls=[ToolCall(id="read",name="read_webpage",arguments={"url":"https://example.com"})])
            value=json.loads(json.loads(next(message.content for message in reversed(messages) if message.role=="tool"))["result"])
            return ModelResponse(finish_reason="stop",content=json.dumps({"conclusion":"引用核对","citations":[{
                "url":value["source"],"artifact_id":value["artifact_id"],"start":0,"end":2,"quote":"Exact quote."
            }],"limitations":[],"conflicts":[],"uncertainties":[],"next_queries":[]}))
    ledger=BudgetLedger(BudgetLimits(model_calls=3,tool_calls=3))
    result=ResearchService(Model(),Search(),Reader(),artifacts).run("核对",ledger.start_segment(BudgetLimits(model_calls=3,tool_calls=3)))
    assert result["status"]=="completed"
    citation=result["evidence"]["citations"][0]
    assert citation["start"]==7 and citation["end"]==19 and citation["offset_normalized"]


def test_two_searches_do_not_force_finalization_before_available_read():
    sessions=SessionStore(); context=sessions.create(uuid4()); artifacts=ArtifactStore(sessions,context)
    class Reader:
        def read(self,url,**kwargs):
            identity=artifacts.save("A verified rule.",source=url)
            return {"artifact_id":identity,"source":url,"start":0,"end":16,"text":"A verified rule."}
    class Model:
        count=0
        def generate(self,messages,*,tools=None):
            self.count+=1
            if self.count==1:
                return ModelResponse(finish_reason="tool_calls",tool_calls=[
                    ToolCall(id="s1",name="search",arguments={"query":"rule"}),
                    ToolCall(id="s2",name="search",arguments={"query":"rule exception"})])
            if self.count==2:
                assert tools, "仍有两次工具额度时必须允许读取"
                return ModelResponse(finish_reason="tool_calls",tool_calls=[
                    ToolCall(id="read",name="read_webpage",arguments={"url":"https://example.com/fact"})])
            value=json.loads(json.loads(next(m.content for m in reversed(messages) if m.role=="tool"))["result"])
            return ModelResponse(finish_reason="stop",content=json.dumps({"conclusion":"有依据","citations":[{
                "url":value["source"],"artifact_id":value["artifact_id"],"start":0,"end":16,"quote":"A verified rule."
            }],"limitations":[],"conflicts":[],"uncertainties":[],"next_queries":[]}))
    ledger=BudgetLedger(BudgetLimits(model_calls=4,tool_calls=4))
    result=ResearchService(Model(),Search(),Reader(),artifacts).run("规则",ledger.start_segment(BudgetLimits(model_calls=4,tool_calls=4)))
    assert result["status"]=="completed" and result["evidence"]["citations"]
