"""独立上下文、只读工具、共享预算的自写研究循环。"""

import json
from importlib.resources import files
from pydantic import BaseModel, ConfigDict, Field

from min_agent.budget import BudgetLimits
from min_agent.context import ContextLimit, request_bound
from min_agent.models import Message
from min_agent.progress_guard import ProgressGuard
from min_agent.runtime import Runtime
from min_agent.tools.registry import ToolDefinition, ToolRegistry


class _Parameters(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class SearchParameters(_Parameters):
    query: str = Field(min_length=1, max_length=400)


class ReadParameters(_Parameters):
    url: str | None = Field(default=None, min_length=1, max_length=2000)
    question: str = Field(default="", max_length=500)
    artifact_id: str | None = None
    start: int = Field(default=0, ge=0)
    max_chars: int = Field(default=4000, ge=200, le=8000)


class Citation(_Parameters):
    url: str
    artifact_id: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    quote: str = Field(min_length=1, max_length=4000)
    offset_normalized: bool = False


class Evidence(_Parameters):
    conclusion: str = Field(max_length=4000)
    citations: list[Citation] = Field(max_length=20)
    limitations: list[str] = Field(max_length=20)
    conflicts: list[str] = Field(max_length=20)
    uncertainties: list[str] = Field(max_length=20)
    next_queries: list[str] = Field(max_length=20)


class ResearchService:
    def __init__(self, model, search, reader, artifacts, *, input_window=None):
        self.model, self.search, self.reader, self.artifacts = model, search, reader, artifacts
        from min_agent.model_context import context_window, output_reserve
        self.input_window = context_window(model, override=input_window)
        self.output_reserve = output_reserve(model)

    def run(self, objective, parent_budget, *, constraints=(), control=None, trace_context=None, trace=None):
        if not isinstance(objective, str) or not objective.strip() or len(objective) > 2000:
            raise ValueError("研究目标无效")
        execution = {}
        if parent_budget.binding is not None:
            self.artifacts.sessions.check_budget_binding(self.artifacts.context, parent_budget)
            execution = {"store": self.artifacts.sessions, "execution_context": self.artifacts.context}
        parent_remaining = parent_budget.remaining
        budget = parent_budget.child(BudgetLimits(model_calls=min(6, parent_remaining.model_calls),
                                                  tool_calls=min(8, parent_remaining.tool_calls)), purpose="research")
        tools = ToolRegistry(); read_ids = set()
        def search(query):
            return json.dumps(self.search.search(query), ensure_ascii=False)
        def read(url=None, question="", artifact_id=None, start=0, max_chars=4000):
            if artifact_id is not None:
                if url is not None or artifact_id not in read_ids: raise PermissionError("只能补读本轮已获取原文")
                result = self.reader.read_artifact(artifact_id, start=start, limit=max_chars)
            elif url is not None:
                result = self.reader.read(url, question=question, max_chars=max_chars)
            else:
                raise ValueError("需要公开URL或当前原文引用")
            read_ids.add(result["artifact_id"])
            return json.dumps(result, ensure_ascii=False)
        tools.register(ToolDefinition("search", "公开网络搜索；摘要不是已读正文；可自主改查询补查。", SearchParameters, search))
        tools.register(ToolDefinition("read_webpage", "首次用url读取公开来源；后续可设url=null、提供本轮artifact_id与start补读原文。返回位置和截断标记，保留反例与限制。", ReadParameters, read))
        underlying = self.model
        input_window, reserve = self.input_window, self.output_reserve
        prompt = files("min_agent").joinpath("prompts/research.md").read_text(encoding="utf-8")
        class IsolatedModel:
            def generate(self, messages, **kwargs):
                projected = []
                for message in messages:
                    candidate = message.model_copy(deep=True)
                    # 网页读取本身有显式分页和位置；不能再静默截掉所读片段尾部。
                    projected.append(candidate)
                visible = [Message(role="system", content=prompt)] + projected
                # 最终文字不消耗工具额度；保留最后一次可读机会，硬门仍由 Runtime.consume 检查。
                closing = budget.remaining.model_calls <= 1 or budget.remaining.tool_calls == 0
                if closing:
                    kwargs["tools"] = None
                    visible.append(Message(role="user", content="已到研究收尾预留，不再搜索或读网页。仅依据实际已读原文返回最终Evidence JSON；证据不够必须写limitations，不捏造引用。"))
                if request_bound(visible, kwargs.get("tools"), model=underlying) + reserve + 1024 > input_window:
                    raise ContextLimit("研究上下文超过保守窗口，暂停")
                response = underlying.generate(visible, **kwargs)
                if closing and response.finish_reason == "tool_calls":
                    raise ContextLimit("研究收尾阶段不能继续调用工具")
                return response
        result = Runtime(IsolatedModel(), tools, budget=budget, max_tool_error_rounds=1,
                         progress_guard=ProgressGuard(), control=control, trace=trace).run(
            json.dumps({"objective": objective, "constraints": list(constraints)}, ensure_ascii=False),
            trace_context=trace_context, **execution,
        )
        if result.status != "completed":
            return {"status": result.status, "error": result.error_code or "research_incomplete", "evidence": None}
        try:
            evidence = Evidence.model_validate_json(result.response.content)
            for citation in evidence.citations:
                if citation.artifact_id not in read_ids:
                    raise ValueError("不是本轮读取的证据")
                original = self.artifacts.get(citation.artifact_id)
                if original["source"] != citation.url:
                    raise ValueError("引用与原文范围不符")
                if citation.quote != original["content"][citation.start:citation.end]:
                    position = original["content"].find(citation.quote)
                    if position < 0 or original["content"].find(citation.quote, position + 1) >= 0:
                        raise ValueError("引文不存在或位置有歧义")
                    citation.start, citation.end, citation.offset_normalized = position, position + len(citation.quote), True
            if not evidence.citations and not evidence.limitations:
                raise ValueError("无证据时必须说明限制")
            return {"status": "completed", "evidence": evidence.model_dump(mode="json"), "error": None}
        except (ValueError, TypeError):
            return {"status": "partial", "error": "invalid_evidence", "evidence": None}
