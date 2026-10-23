"""受限独立调用压缩；校验后返回候选，失败按会话与源版本熔断。"""

from copy import deepcopy
from hashlib import sha256
from importlib.resources import files
import json
import re
from pydantic import BaseModel, ConfigDict, Field

from min_agent.context import ContextLimit, request_bound
from min_agent.memory import MemoryJournal
from min_agent.models import Message, ModelResponse
from min_agent.skill_retention import skill_history_projection


class CompactionBlocked(ContextLimit):
    pass


class Summary(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    facts: list[str] = Field(max_length=30)
    constraints: list[str] = Field(max_length=30)
    corrections: list[str] = Field(max_length=30)
    open_questions: list[str] = Field(max_length=30)
    source_references: list[str] = Field(max_length=30)


class Compactor:
    def __init__(self, model, sessions, context, budget, *, input_window=32768, control=None):
        self.model, self.sessions, self.context, self.budget = model, sessions, context, budget
        self.input_window = input_window
        self.control = control

    def window_attempt(self, state, messages, tools, window):
        source = json.dumps({"messages": [m.model_dump(mode="json") for m in messages],
            "tools": tools, "window": window}, sort_keys=True, ensure_ascii=False)
        key = str(state["version"]) + ":window:" + sha256(source.encode()).hexdigest()
        if self.sessions.extension(self.context, "compaction_failures", {}).get(key, 0) >= 2:
            raise CompactionBlocked("相同上下文压缩后仍超窗，已熔断")
        return key

    def reject_window(self, key):
        failures = self.sessions.extension(self.context, "compaction_failures", {})
        failures[key] = failures.get(key, 0) + 1
        self.sessions.set_extension(self.context, "compaction_failures", failures)

    def _summarize(self, state, source, phase):
        if self.control is not None and self.control.snapshot()["stop"]:
            raise ContextLimit("用户已停止，不启动压缩请求")
        serialized = json.dumps(source, ensure_ascii=False, sort_keys=True)
        key = str(state["version"]) + ":" + phase + ":" + sha256(serialized.encode()).hexdigest()
        failures = self.sessions.extension(self.context, "compaction_failures", {})
        if failures.get(key, 0) >= 2:
            raise CompactionBlocked("当前会话/上下文版本压缩已熔断")
        try:
            prompt = files("min_agent").joinpath("prompts/compact_" + phase + ".md").read_text(encoding="utf-8")
            messages = [Message(role="system", content=prompt), Message(role="user", content=serialized)]
            if request_bound(messages) + 4096 + 1024 > self.input_window:
                raise ValueError("压缩请求自身超过窗口")
            self.budget.consume("model_calls", purpose="compaction")
            response = self.model.generate(messages)
            if not isinstance(response, ModelResponse) or response.finish_reason != "stop":
                raise ValueError("压缩响应未完整结束")
            summary = Summary.model_validate_json(response.content)
            rendered = summary.model_dump_json()
            if len(rendered.encode()) > 6000:
                raise ValueError("摘要仍超出目标预算")
            for value in re.findall(r"\b\d{4}-\d{2}-\d{2}\b", serialized):
                if value not in rendered: raise ValueError("日期关键字段丢失")
            return summary.model_dump(mode="json")
        except Exception:
            failures[key] = failures.get(key, 0) + 1
            self.sessions.set_extension(self.context, "compaction_failures", failures)
            raise CompactionBlocked("压缩失败，原始历史和可见版本保留") from None

    def compact_memory(self, state):
        candidate = deepcopy(state)
        candidate["summary"] = self._summarize(state, state["summary"], "memory")
        candidate["version"] += 1
        return candidate

    def compact_history(self, state, messages, hashes, end):
        source = {"previous_memory": state["summary"],
                  "messages": [m.model_dump(mode="json") for m in skill_history_projection(messages)]}
        summary = self._summarize(state, source, "history")
        MemoryJournal(self.sessions, self.context).archive(messages, json.dumps(summary, ensure_ascii=False), start=state["covered"])
        candidate = deepcopy(state)
        candidate.update(summary=summary, covered=end, covered_hashes=hashes, version=state["version"] + 1)
        return candidate
