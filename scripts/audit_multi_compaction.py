"""主上下文A→B→C连续压缩回归；仅使用离线选择模型，不涉及其他模块。"""

import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

from min_agent.artifacts import ArtifactStore
from min_agent.budget import BudgetLedger, BudgetLimits
from min_agent.compaction import Compactor
from min_agent.context import ContextLimit, ContextManager, ManagedModel, request_bound
from min_agent.models import Message, ModelResponse
from min_agent.sessions import SessionStore


RULES = {
    "A": "不得删除原文，标识A_RULE_UNIQUE。",
    "B": "必须核验最终结论的来源，标识B_RULE_UNIQUE。",
    "C": "禁止将未知执行结果报告为成功，标识C_RULE_UNIQUE。",
}


def hashes(messages):
    return [sha256(message.model_dump_json().encode()).hexdigest() for message in messages]


def rendered(value):
    return json.dumps(value, ensure_ascii=False)


class OfflineSelector:
    def __init__(self):
        self.phase, self.invalid, self.calls = "A", False, []

    def generate(self, messages):
        payload = json.loads(messages[-1].content)
        self.calls.append({"phase": self.phase, "messages": [m.model_dump(mode="json") for m in messages]})
        if self.invalid:
            return ModelResponse(finish_reason="stop", content="invalid offline response")
        # 固定选择规则仅验证来源与传递，不冒充模型的任务理解能力。
        return ModelResponse(finish_reason="stop", content=json.dumps({"selections": [
            {"unit_id": row["unit_id"], "category": "constraints"}
            for row in payload["units"] if "_RULE_UNIQUE" in row["text"]]}))


class AuditCompactor(Compactor):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.history_calls = []

    def compact_history(self, state, messages, source_hashes, end):
        self.history_calls.append({"phase": self.model.phase, "start": state["covered"], "end": end,
            "message_hashes": hashes(messages), "covered_hashes": list(source_hashes),
            "previous_memory": deepcopy(state["summary"])})
        return super().compact_history(state, messages, source_hashes, end)


class MainProbe:
    def __init__(self):
        self.calls = []

    def generate(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        return ModelResponse(finish_reason="stop", content="固定离线答复")


def run_regression():
    store = SessionStore()
    context = store.create(uuid4())
    artifacts = ArtifactStore(store, context)
    selector, probe = OfflineSelector(), MainProbe()
    ledger = BudgetLedger(BudgetLimits(model_calls=30, tool_calls=0))
    budget = ledger.start_segment(BudgetLimits(model_calls=30, tool_calls=0))
    compactor = AuditCompactor(selector, store, context, budget, input_window=200000)
    manager = ContextManager(store, context, artifacts, window=16000, output_reserve=1000,
                             safety_margin=512, compactor=compactor)
    stages = []
    unchanged = True

    def phase(name, batch):
        nonlocal unchanged
        selector.phase = name
        task = store.begin(context)
        store.append(context, task.task_id, batch + [Message(role="user", content="继续按所有已确认约束处理")])
        original = deepcopy(store.snapshot(context).messages)
        ManagedModel(probe, manager).generate(original)
        unchanged = unchanged and store.snapshot(context).messages == original
        state = store.extension(context, "context", {})
        visible = [message.model_dump(mode="json") for message in probe.calls[-1]]
        stages.append({"phase": name, "state": deepcopy(state), "input_messages": [
            message.model_dump(mode="json") for message in original], "visible_messages": visible,
            "request_bytes_bound": request_bound(probe.calls[-1]),
            "raw_history_unchanged": store.snapshot(context).messages == original})
        store.append(context, task.task_id, [Message(role="assistant", content="固定离线答复")])
        store.finish(context, task.task_id, status="completed")

    def hundred_rounds(name):
        messages = []
        for turn in range(1, 101):
            text = f"TRACE_{name}_{turn:03d}，一般工作记录。"
            if turn == 50 and name in RULES:
                text += RULES[name]
            messages += [Message(role="user", content=text), Message(role="assistant",
                content=f"阶段{name}处理记录{turn}。" + "普通背景材料。" * 12)]
        return messages

    phase("A", [Message(role="user", content=RULES["A"]),
        Message(role="assistant", content="普通背景材料。" * 1200),
        Message(role="user", content="复核已有要求"), Message(role="assistant", content="已了解")])
    phase("B", hundred_rounds("B"))
    phase("C", hundred_rounds("C"))
    current = store.snapshot(context).messages
    stable_state = deepcopy(store.extension(context, "context", {}))
    b_text, c_text = rendered(stages[1]["visible_messages"]), rendered(stages[2]["visible_messages"])
    outcomes = {
        "R01": all(RULES[key] in b_text for key in ("A", "B")),
        "R02": all(RULES[key] in c_text for key in ("A", "B", "C")),
    }
    covered = [stage["state"]["covered"] for stage in stages]
    chain = 0 < covered[0] < covered[1] < covered[2]
    for stage in stages:
        source = [Message.model_validate(m) for m in stage["input_messages"]]
        chain = chain and stage["state"]["covered_hashes"] == hashes(source[:stage["state"]["covered"]])
    for call in compactor.history_calls:
        stage = next(value for value in stages if value["phase"] == call["phase"])
        original = [Message.model_validate(m) for m in stage["input_messages"]]
        chain = chain and call["message_hashes"] == hashes(original[call["start"]:call["end"]])
        chain = chain and len(call["message_hashes"]) == call["end"] - call["start"]
    archive_coverage = set()
    archive_valid = True
    for record in store.extension(context, "memory_journal", {}).values():
        start, end = record["start"], record["end"]
        original = [Message.model_validate(m) for m in record["messages"]]
        archive_valid = archive_valid and original == current[start:end]
        archive_coverage.update(range(start, end))
    round_coverage = {}
    for name in ("B", "C"):
        actual = rendered([call for call in selector.calls if call["phase"] == name]) + rendered(
            next(stage for stage in stages if stage["phase"] == name)["visible_messages"])
        round_coverage[name] = [turn for turn in range(1, 101) if f"TRACE_{name}_{turn:03d}" not in actual]
    outcomes["R03"] = bool(chain and archive_valid and not (set(range(covered[-1])) - archive_coverage)
        and not round_coverage["B"] and not round_coverage["C"])
    summary = stable_state["summary"]
    references_valid = True
    for ref in summary["source_references"]:
        original = artifacts.get(ref["artifact_id"])
        quote = summary[ref["section"]][ref["item"]]
        references_valid = references_valid and sha256(original["content"].encode()).hexdigest() == ref["sha256"]
        references_valid = references_valid and original["content"][ref["start"]:ref["end"]] == quote
    last_input = [Message.model_validate(m) for m in stages[-1]["input_messages"]]
    tail = [m.model_dump(mode="json") for m in last_input[covered[-1]:]]
    outcomes["R04"] = bool(unchanged and references_valid and stages[-1]["visible_messages"][-len(tail):] == tail)
    reopened = ContextManager(store, context, artifacts, window=16000, output_reserve=1000,
                              safety_margin=512, compactor=compactor)
    calls_before = len(selector.calls)
    visible = reopened.prepare(current)
    outcomes["R05"] = (len(selector.calls) == calls_before and visible[:-1] == probe.calls[-1]
                        and visible[-1] == current[-1] and store.extension(context, "context", {}) == stable_state)
    mismatch_errors = []
    edited = deepcopy(current)
    edited[0] = edited[0].model_copy(update={"content": "旧内容被改写"})
    for invalid in (edited, current[1:]):
        try:
            ManagedModel(probe, reopened).generate(invalid)
        except ContextLimit as error:
            mismatch_errors.append(str(error))
    outcomes["R06"] = (len(mismatch_errors) == 2 and len(selector.calls) == calls_before
                        and store.extension(context, "context", {}) == stable_state
                        and store.snapshot(context).messages == current)
    selector.phase, selector.invalid = "D-invalid", True
    task = store.begin(context)
    store.append(context, task.task_id, hundred_rounds("D") + [Message(role="user", content="继续按所有已确认约束处理")])
    expanded = deepcopy(store.snapshot(context).messages)
    main_before = len(probe.calls)
    invalid_error = None
    try:
        ManagedModel(probe, reopened).generate(expanded)
    except ContextLimit as error:
        invalid_error = str(error)
    outcomes["R07"] = (invalid_error is not None and len(probe.calls) == main_before
        and store.extension(context, "context", {}) == stable_state and store.snapshot(context).messages == expanded)
    limited_ledger = BudgetLedger(BudgetLimits(model_calls=1, tool_calls=0))
    limited = limited_ledger.start_segment(BudgetLimits(model_calls=1, tool_calls=0))
    limited_manager = ContextManager(store, context, artifacts, window=16000, output_reserve=1000,
        safety_margin=512, compactor=Compactor(selector, store, context, limited, input_window=200000))
    calls_before = len(selector.calls)
    budget_error = None
    try:
        ManagedModel(probe, limited_manager).generate(expanded)
    except ContextLimit as error:
        budget_error = str(error)
    outcomes["R08"] = (budget_error is not None and len(selector.calls) == calls_before
        and limited_ledger.usage.model_calls == 0 and len(probe.calls) == main_before
        and store.extension(context, "context", {}) == stable_state and store.snapshot(context).messages == expanded)
    return {"outcomes": outcomes, "stages": stages, "covered": covered,
        "round_coverage_missing": round_coverage, "history_calls": compactor.history_calls,
        "compression_requests": selector.calls, "local_model_calls": ledger.usage.model_calls,
        "errors": {"mismatch": mismatch_errors, "invalid_response": invalid_error, "budget": budget_error}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    suite = json.loads(args.suite.read_text(encoding="utf-8"))
    with args.report.open("x", encoding="utf-8") as output:
        output.write("{}")
    result = run_regression()
    records = [{**case, "observed": result["outcomes"][case["id"]],
        "passed": result["outcomes"][case["id"]] == case["expected"]} for case in suite["cases"]]
    report = {"suite_sha256": sha256(args.suite.read_bytes()).hexdigest(), "scope": suite["scope"],
        "live_requests": 0, "passed": sum(record["passed"] for record in records), "total": len(records),
        "records": records, "evidence": result}
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "total": len(records), "covered": result["covered"],
                      "round_coverage_missing": result["round_coverage_missing"]}, ensure_ascii=False))
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
