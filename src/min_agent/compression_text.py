"""全文扫描、保守去噪和带位置的任务相关原句抽取。"""

import json
import re
from hashlib import sha256
from dataclasses import dataclass


CRITICAL = re.compile(r"不[\u4e00-\u9fff]|禁止|避免|仅|除非|如果|必须|未使用|至少|至多|\d+\s*(?:天|元|%|次)|\b(?:not|never|unless|only|except|within)\b|\d{4}-\d{2}-\d{2}", re.I)
NOISE = {"网站导航", "返回顶部", "NAVIGATION", "ADVERTISEMENT", "广告位"}


def text_units(text, objective=""):
    units, seen, dropped = [], set(), 0
    # 完全重复的整个短块只保留一次；不靠截断处理长输入。
    repeated = None
    if len(text) > 512:
        for length in range(2, min(201, len(text) // 3)):
            if len(text) % length == 0 and text == text[:length] * (len(text) // length):
                repeated = length
                break
    scanned = text if repeated is None else text[:repeated]
    if repeated:
        dropped += len(text) // repeated - 1
    for match in re.finditer(r".+?(?:\n|[。！？!?]|\.(?=\s|$)|$)", scanned, re.S):
        raw = match.group(); quote = raw.strip()
        if not quote:
            dropped += 1
            continue
        if quote in seen or (quote in NOISE and quote.casefold() not in objective.casefold()):
            dropped += 1
            continue
        seen.add(quote)
        start = match.start() + len(raw) - len(raw.lstrip())
        units.append({"quote": quote, "start": start, "end": start + len(quote)})
    return units, dropped


def task_terms(objective):
    terms = set(re.findall(r"[a-z0-9]+", objective.casefold()))
    for value in re.findall(r"[\u4e00-\u9fff]+", objective):
        terms.update(value[index:index + 2] for index in range(len(value) - 1))
    return terms


@dataclass
class ExcerptPlan:
    excerpts: list[dict]
    review: list[dict] | None = None
    selected_ids: tuple[int, ...] = ()


class ExcerptOverflow(ValueError):
    def __init__(self, plan):
        super().__init__("模型选中或规则补充的完整原句超出摘要预算，需要语义压缩")
        self.plan = plan


def plan_excerpts(units, objective, *, max_bytes=2800, selected=None, reviewer=None):
    """分别保留模型选择与规则提醒；仅规则命中不能直接等同于任务相关。"""
    terms = task_terms(objective)
    rule_matched = {index for index, unit in enumerate(units) if CRITICAL.search(unit["quote"])}
    model_selected = set() if selected is None else set(selected)
    rule_only = rule_matched - model_selected
    candidates = set(range(len(units))) if selected is None else model_selected | rule_matched
    mandatory = set(rule_matched)
    review = None
    if (selected is not None and reviewer is not None and rule_only
            and sum(len(units[index]["quote"].encode()) for index in candidates) > max_bytes):
        review = reviewer.review(units, objective, model_selected, rule_only)
        dropped = {item["unit_id"] for item in review if item["decision"] == "drop"}
        candidates -= dropped
        mandatory -= dropped
    decisions = {} if review is None else {item["unit_id"]: item["decision"] for item in review}
    def build(indices):
        return ExcerptPlan([{**units[index], "unit_id": index, "model_selected": index in model_selected,
            "rule_matched": index in rule_matched,
            "relevance_review": decisions.get(index, "not_reviewed" if index in rule_only else "not_applicable")}
            for index in sorted(indices)], review, tuple(sorted(model_selected)))
    if selected is not None:
        # 模型路径保留全部相关候选；放不下由调用者做语义压缩，不按关键词丢句。
        plan = build(candidates)
        if sum(len(unit["quote"].encode()) for unit in plan.excerpts) > max_bytes:
            raise ExcerptOverflow(plan)
        return plan
    ranked = sorted(candidates, key=lambda index: (
        index not in mandatory,
        -sum(term in units[index]["quote"].casefold() for term in terms), index))
    chosen, used = [], 0
    for index in ranked:
        size = len(units[index]["quote"].encode())
        if used + size > max_bytes:
            if index in mandatory:
                raise ValueError("摘要预算不足以保留完整否定、条件或日期；不能截句")
            continue
        chosen.append(index); used += size
    if candidates and not chosen:
        raise ValueError("完整原句超过摘要预算，需要分段处理或补读")
    return build(chosen)


def choose_excerpts(units, objective, *, max_bytes=2800, selected=None):
    # 无模型的结构化层仍保守保留规则命中，不声称做过语义判断。
    return plan_excerpts(units, objective, max_bytes=max_bytes, selected=selected).excerpts


def save_selection_review(plan, units, objective, artifacts):
    if plan.review is None:
        return None
    record = json.dumps({"objective": objective, "units": [
        {**unit, "unit_id": index, "model_selected": index in plan.selected_ids,
            "rule_matched": bool(CRITICAL.search(unit["quote"]))}
        for index, unit in enumerate(units)], "decisions": plan.review}, ensure_ascii=False)
    identity = artifacts.save(record, kind="selection_review")
    return {"artifact_id": identity, "sha256": sha256(record.encode()).hexdigest(),
        "dropped_units": sum(item["decision"] == "drop" for item in plan.review)}


def tool_summary(content, objective, artifacts, selector=None, *, max_bytes=2800):
    known = artifacts.sessions.extension(artifacts.context, "tool_summary_sources", {})
    def preserve(text):
        digest = sha256(text.encode()).hexdigest()
        if digest not in known:
            known[digest] = artifacts.save(text, kind="tool_result")
            artifacts.sessions.set_extension(artifacts.context, "tool_summary_sources", known)
        identity = known[digest]
        if artifacts.get(identity)["sha256"] != digest:
            raise ValueError("工具摘要原文版本不匹配")
        return identity, digest
    raw_identity, raw_digest = preserve(content)
    envelope, text = {}, content
    try:
        report = json.loads(content)
    except ValueError:
        report = None
    if isinstance(report, dict) and type(report.get("ok")) is bool:
        envelope["ok"] = report["ok"]
        error = report.get("error")
        if isinstance(error, dict):
            envelope["error"] = {key: error[key] for key in ("code", "execution_state", "recovery")
                if isinstance(error.get(key), str) and len(error[key]) <= 200}
        if isinstance(report.get("result"), str):
            # 先解码运行时结果封装，再全文分段；JSON转义换行不是正文换行。
            text = report["result"]
    identity, digest = preserve(text)
    units, dropped = text_units(text, objective)
    selection = selector.select(units, objective) if selector is not None and units else None
    try:
        plan = plan_excerpts(units, objective, max_bytes=max_bytes,
            selected=None if selection is None else [item[0] for item in selection], reviewer=selector)
    except ExcerptOverflow as error:
        plan = error.plan
    base = {"artifact_id": identity,
        "sha256": digest, "raw_artifact_id": raw_identity, "raw_sha256": raw_digest,
        "tool_envelope": envelope, "dropped_noise": dropped,
        "omitted_units": len(units) - len(plan.excerpts), "coverage": "full_source_scanned",
        "selection": "model" if selector else "deterministic",
        "selection_review": save_selection_review(plan, units, objective, artifacts),
        "read_more": "read_artifact按ID和范围核对原文；未选中内容不代表不存在"}
    extracted = json.dumps({**base, "method": "task_aware_extractive_summary", "excerpts": plan.excerpts}, ensure_ascii=False)
    if len(extracted.encode()) <= max_bytes:
        return extracted
    if selector is None or not plan.excerpts:
        raise ValueError("完整工具摘要超出预算且没有语义压缩能力")
    from min_agent.compression_evidence import save_evidence
    evidence = save_evidence(plan.excerpts, artifacts,
        default_source={"artifact_id": identity, "sha256": digest, "source_kind": "tool_report"})
    def render(items):
        return json.dumps({**base, "method": "task_aware_semantic_summary", "unverified": True,
            "excerpts": [], "evidence": evidence, "semantic_summary": items}, ensure_ascii=False)
    items = selector.compress(plan.excerpts, objective, max_bytes=max_bytes, render=render)
    return render(items)
