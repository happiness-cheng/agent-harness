"""只读、有界的语义改写；引用覆盖和预算可校验，语义真实性不由引用证明。"""

import json
from importlib.resources import files
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from min_agent.models import Message, ModelResponse


class SummaryItem(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    text: str = Field(min_length=1, max_length=12000)
    source_ids: list[int] = Field(min_length=1, max_length=2000)
    category: Literal["facts", "constraints", "corrections", "open_questions"] = "facts"


class SummaryResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    items: list[SummaryItem] = Field(min_length=1, max_length=30)


class SemanticCompressor:
    def __init__(self, model, budget, *, input_window=32768, control=None,
                 reserve_model_calls=1, max_calls=4):
        self.model, self.budget, self.input_window = model, budget, input_window
        self.control, self.reserve_model_calls, self.max_calls = control, reserve_model_calls, max_calls
        self.prompt = files("min_agent.prompts").joinpath("compress_selection.md").read_text(encoding="utf-8")

    def _messages(self, rows, objective, max_bytes, render, feedback=None):
        payload = {"objective": objective, "target_bytes": max_bytes,
            "text_budget_bytes": max(0, max_bytes - len(render([]).encode())), "units": rows}
        if feedback:
            payload["feedback"] = feedback
        return [Message(role="system", content=self.prompt),
            Message(role="user", content=json.dumps(payload, ensure_ascii=False))]

    def _fits(self, messages, max_bytes):
        from min_agent.context import request_bound
        return request_bound(messages) + max(4096, max_bytes + 1024) + 1024 <= self.input_window

    def _check_stop(self):
        if self.control is not None and self.control.snapshot()["stop"]:
            raise ValueError("用户已停止，不启动语义压缩")

    def _invoke(self, rows, objective, max_bytes, render, limits):
        required = {identity for row in rows for identity in row["source_ids"]}
        feedback = None
        while True:
            self._check_stop()
            messages = self._messages(rows, objective, max_bytes, render, feedback)
            if not self._fits(messages, max_bytes):
                raise ValueError("语义压缩完整请求超出输入窗口，不截断依据")
            pending = limits.get("pending", 0)
            if (limits["calls"] + pending >= self.max_calls
                    or self.budget.remaining.model_calls <= self.reserve_model_calls + pending):
                raise ValueError("语义压缩调用预算不足，保留收尾额度")
            self.budget.consume("model_calls", purpose="compaction")
            limits["calls"] += 1
            response = self.model.generate(messages)
            try:
                if not isinstance(response, ModelResponse) or response.finish_reason != "stop" or response.tool_calls:
                    raise ValueError("摘要响应不完整或试图调用工具")
                result = SummaryResponse.model_validate_json(response.content)
                covered = set()
                for item in result.items:
                    if (not item.text.strip() or len(set(item.source_ids)) != len(item.source_ids)
                            or any(type(identity) is not int or identity not in required for identity in item.source_ids)):
                        raise ValueError("摘要包含空文本、伪造或重复来源ID")
                    covered.update(item.source_ids)
                if covered != required:
                    raise ValueError("摘要缺少保留候选的来源覆盖")
                items = [item.model_dump() for item in result.items]
                if len(render(items).encode()) > max_bytes:
                    raise ValueError("包含引用和元数据的完整摘要超出预算")
                return items
            except ValueError:
                if limits["repaired"]:
                    raise ValueError("语义摘要一次修正后仍未通过校验") from None
                limits["repaired"] = True
                feedback = "上次格式、来源覆盖或完整结果长度未通过；使用全部原始输入修正并进一步精简。"

    def compress(self, units, objective, *, max_bytes, render=None):
        render = render or (lambda items: json.dumps({"items": items}, ensure_ascii=False))
        self._check_stop()
        if type(max_bytes) is not int or max_bytes <= len(render([]).encode()):
            raise ValueError("摘要元数据已占满预算，不能靠模型删除引用")
        identities = [unit["unit_id"] for unit in units]
        if (not identities or len(set(identities)) != len(identities)
                or any(type(identity) is not int or identity < 0 for identity in identities)):
            raise ValueError("语义压缩需要非空且唯一的来源ID")
        rows = [{"source_ids": [unit["unit_id"]], "text": unit["quote"],
            "source_kind": unit.get("source", {}).get("source_kind", "tool_report"),
            "model_selected": unit.get("model_selected", False),
            "rule_matched": unit.get("rule_matched", False),
            "relevance_review": unit.get("relevance_review", "not_reviewed")} for unit in units]
        limits = {"calls": 0, "repaired": False, "pending": 0}
        if self._fits(self._messages(rows, objective, max_bytes, render), max_bytes):
            return self._invoke(rows, objective, max_bytes, render, limits)
        chunks, chunk = [], []
        for row in rows:
            self._check_stop()
            if not self._fits(self._messages([row], objective, max_bytes, render), max_bytes):
                raise ValueError("单条原句超过语义压缩输入窗口，不截断依据")
            if chunk and not self._fits(self._messages(chunk + [row], objective, max_bytes, render), max_bytes):
                chunks.append(chunk)
                chunk = []
            chunk.append(row)
        if chunk:
            chunks.append(chunk)
        # 最多一层分块加一次汇总；启动前为全部块、汇总和主任务收尾预留。
        minimum = len(chunks) + 1
        if minimum > self.max_calls or minimum + self.reserve_model_calls > self.budget.remaining.model_calls:
            raise ValueError("语义分块压缩预算不能覆盖全部块及汇总，不启动部分处理")
        items = []
        for position, chunk in enumerate(chunks):
            limits["pending"] = len(chunks) - position
            items.extend(self._invoke(chunk, objective, max_bytes, render, limits))
        try:
            if len(render(items).encode()) <= max_bytes:
                return items
        except ValueError:
            # 分块各自合法，合在一起可能超过条数等结构约束，仍应尝试唯一一次汇总。
            pass
        limits["pending"] = 0
        merged = [{"text": item["text"], "source_ids": item["source_ids"],
            "source_kind": "unverified_summary"} for item in items]
        return self._invoke(merged, objective, max_bytes, render, limits)
