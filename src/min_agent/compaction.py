"""受限独立调用压缩；校验后返回候选，失败按会话与源版本熔断。"""

from copy import deepcopy
from hashlib import sha256
from importlib.resources import files
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

from min_agent.context import ContextLimit
from min_agent.memory import MemoryJournal
from min_agent.models import Message
from min_agent.artifacts import ArtifactStore
from min_agent.compression_selector import GroundedSelector
from min_agent.compression_text import ExcerptOverflow, plan_excerpts, save_selection_review, text_units
from min_agent.compression_evidence import read_evidence, save_evidence
from min_agent.memory_records import source_text
from min_agent.skill_retention import skill_history_projection


class CompactionBlocked(ContextLimit):
    pass


class Summary(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    facts: list[str] = Field(max_length=30)
    constraints: list[str] = Field(max_length=30)
    corrections: list[str] = Field(max_length=30)
    open_questions: list[str] = Field(max_length=30)
    source_references: list[dict] = Field(max_length=120)
    selection_review: dict | None = None
    representation: Literal["extractive", "semantic"] = "extractive"
    unverified: bool = True


class Compactor:
    def __init__(self, model, sessions, context, budget, *, input_window=None, control=None):
        from min_agent.model_context import context_window
        self.model, self.sessions, self.context, self.budget = model, sessions, context, budget
        self.input_window = context_window(model, override=input_window)
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
            objective = getattr(self, "objective", "保留当前任务的事实、约束、用户纠正和未解决问题")
            units = self._source_units(source, objective)
            selector = GroundedSelector(self.model, self.budget,
                input_window=self.input_window, control=self.control,
                prompt=files("min_agent.prompts").joinpath(f"compact_{phase}.md").read_text(encoding="utf-8"))
            selected = selector.select(units, objective)
            categories = {index: category for index, category in selected}
            try:
                plan = plan_excerpts(units, objective, max_bytes=3500, selected=categories, reviewer=selector)
            except ExcerptOverflow as error:
                plan = error.plan
            value = {name: [] for name in ("facts", "constraints", "corrections", "open_questions")}
            value["source_references"] = []
            value["selection_review"] = save_selection_review(plan, units, objective, ArtifactStore(self.sessions, self.context))
            for unit in plan.excerpts:
                category = categories.get(unit["unit_id"], "constraints" if unit["rule_matched"] else "facts")
                position = len(value[category])
                value[category].append(unit["quote"])
                value["source_references"].append({"section": category, "item": position,
                    **unit["source"], "start": unit["start"], "end": unit["end"],
                    **{name: unit[name] for name in ("model_selected", "rule_matched", "relevance_review")}})
            try:
                extracted = Summary.model_validate(value)
            except ValueError:
                extracted = None
            if extracted is not None and len(json.dumps(extracted.model_dump(mode="json"), ensure_ascii=False).encode()) <= 6000:
                return extracted.model_dump(mode="json")
            evidence = save_evidence(plan.excerpts, ArtifactStore(self.sessions, self.context))
            def render(items):
                candidate = {name: [] for name in ("facts", "constraints", "corrections", "open_questions")}
                candidate.update(source_references=[], selection_review=value["selection_review"], representation="semantic")
                for item in items:
                    category = item["category"]
                    position = len(candidate[category])
                    candidate[category].append(item["text"])
                    candidate["source_references"].append({"section": category, "item": position,
                        "semantic": True, "evidence": evidence, "source_ids": item["source_ids"],
                        "summary_sha256": sha256(item["text"].encode()).hexdigest()})
                return json.dumps(Summary.model_validate(candidate).model_dump(mode="json"), ensure_ascii=False)
            items = selector.compress(plan.excerpts, objective, max_bytes=6000, render=render)
            return json.loads(render(items))
        except Exception:
            failures[key] = failures.get(key, 0) + 1
            self.sessions.set_extension(self.context, "compaction_failures", failures)
            raise CompactionBlocked("压缩失败，原始历史和可见版本保留") from None

    def _source_units(self, source, objective):
        texts = []
        seen_evidence = set()
        def collect(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key == "quote" and isinstance(item, str):
                        texts.append((item, "unverified_summary", None))
                    elif key in {"facts", "constraints", "corrections", "open_questions"} and isinstance(item, list):
                        references = value.get("source_references", [])
                        for index, part in enumerate(item):
                            if isinstance(part, str):
                                matching = [ref for ref in references if isinstance(ref, dict)
                                    and ref.get("section") == key and ref.get("item") == index]
                                if any(ref.get("semantic") is True for ref in matching):
                                    for reference in matching:
                                        if (reference.get("semantic") is not True
                                                or reference.get("summary_sha256") != sha256(part.encode()).hexdigest()):
                                            raise ValueError("已有语义摘要版本不匹配")
                                        originals = read_evidence(reference["evidence"], reference["source_ids"],
                                            ArtifactStore(self.sessions, self.context))
                                        for original in originals:
                                            location = (original["source"]["artifact_id"], original["start"], original["end"])
                                            if location not in seen_evidence:
                                                seen_evidence.add(location)
                                                texts.append((original["quote"], "unverified_summary",
                                                    {**original["source"], "start": original["start"], "end": original["end"]}))
                                    continue
                                reference = next((ref for ref in references if isinstance(ref, dict)
                                    and ref.get("section") == key and ref.get("item") == index), None)
                                texts.append((part, "unverified_summary", reference))
                    elif isinstance(item, (dict, list)) and key != "source_references":
                        collect(item)
            elif isinstance(value, list):
                for item in value: collect(item)
        if isinstance(source, dict) and "messages" in source:
            for item in source["messages"]:
                message = Message.model_validate(item)
                texts.append((source_text(message), message.role, None))
            collect(source.get("previous_memory"))
        else:
            collect(source)
        artifacts = ArtifactStore(self.sessions, self.context)
        known = self.sessions.extension(self.context, "compression_sources", {})
        units = []
        for text, source_kind, reference in texts:
            if reference is not None:
                # 再压缩仍回指原始材料，不把“上一份摘要”变成唯一出处。
                original = artifacts.get(reference.get("artifact_id"))
                start, end = reference.get("start"), reference.get("end")
                if (type(start) is not int or type(end) is not int or not 0 <= start <= end <= len(original["content"])
                    or reference.get("sha256") != sha256(original["content"].encode()).hexdigest()
                    or original["content"][start:end] != text):
                    raise ValueError("已有摘要的原文引用不能校验")
                excerpts, _ = text_units(text, objective)
                units.extend({**item, "start": start + item["start"], "end": start + item["end"],
                    "source": {"artifact_id": original["id"], "sha256": original["sha256"],
                        "source_kind": source_kind}} for item in excerpts)
                continue
            digest = sha256(text.encode()).hexdigest()
            if digest not in known:
                known[digest] = artifacts.save(text, kind="compression_source")
            excerpts, _ = text_units(text, objective)
            units.extend({**item, "source": {"artifact_id": known[digest], "sha256": digest,
                "source_kind": source_kind}} for item in excerpts)
        self.sessions.set_extension(self.context, "compression_sources", known)
        return units

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
