"""上下文任务材料接口回归；全部模型与摘要均离线，不发送外部数据。"""

import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

from min_agent.artifacts import ArtifactStore
from min_agent.budget import BudgetLedger, BudgetLimits
from min_agent.context import ContextManager, ManagedModel
from min_agent.models import Message, ModelResponse, ToolCall
from min_agent.sessions import SessionStore
from min_agent.skill_retention import SkillRetentionSelector


BODY = "接口测试规程全文：保持事实。BODY_C05_UNIQUE"


class OfflineSummary:
    def window_attempt(self, *args):
        return "offline-context-window"

    def compact_memory(self, state):
        return {**state, "summary": "接口回归占位摘要", "version": state["version"] + 1}

    def compact_history(self, state, messages, hashes, end):
        return {**state, "summary": "接口回归占位摘要", "version": state["version"] + 1,
                "covered": end, "covered_hashes": hashes}

    def reject_window(self, key):
        pass


class AuxiliaryModel:
    def __init__(self, *, release=False):
        self.calls, self.release = [], release

    def generate(self, messages, **kwargs):
        self.calls.append([m.model_dump(mode="json") for m in messages])
        return ModelResponse(finish_reason="stop", content=json.dumps({
            "keep": [] if self.release else ["p"], "release": ["p"] if self.release else [], "uncertain": []}))


class InputRecorder:
    def __init__(self, delegate):
        self.delegate, self.calls = delegate, []

    def select(self, candidates, task_context):
        self.calls.append({"candidates": deepcopy(candidates), "task_context": deepcopy(task_context)})
        return self.delegate.select(candidates, task_context)


class MainProbe:
    def __init__(self):
        self.calls = []

    def generate(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        return ModelResponse(finish_reason="stop", content="固定离线答复")


def rounds(phase):
    messages = []
    for turn in range(1, 101):
        text = f"TRACE_{phase}_{turn:03d}，工作要求。"
        if turn == 50:
            text += f"必须保留中间约束，MID_{phase}_C05。"
        messages.append(Message(role="user", content=text))
        if turn < 100:
            messages.append(Message(role="assistant", content=f"阶段{phase}处理中。" + "普通背景材料。" * 10))
    return messages


def run_case(case):
    store = SessionStore()
    context = store.create(uuid4())
    prefix = [Message(role="user", content="A点以前的工作"),
        Message(role="assistant", content="", tool_calls=[ToolCall(id="p", name="read_skill", arguments={"name":"procedure"})]),
        Message(role="tool", tool_call_id="p", content=json.dumps({"ok":True,"result":BODY}, ensure_ascii=False)),
        Message(role="assistant", content="已读取")]
    summary, phase = {"facts": ["SUMMARY_A_C05"]}, "B"
    if case["scenario"] == "after_b":
        prefix += rounds("B") + [Message(role="assistant", content="B点以前的已确认答复")]
        summary, phase = {"facts": ["SUMMARY_B_C05：保留A与B的已确认约束"]}, "C"
    covered = 0 if case["scenario"] == "no_summary" else len(prefix)
    if not covered:
        summary = None
    messages = prefix + rounds(phase)
    original = deepcopy(messages)
    state = {"version":2,"projections":{},"summary":deepcopy(summary),"covered":covered,
        "covered_hashes":[sha256(m.model_dump_json().encode()).hexdigest() for m in messages[:covered]]}
    store.set_extension(context, "context", state)
    task = store.begin(context)
    store.append(context, task.task_id, messages)
    ledger = BudgetLedger(BudgetLimits(model_calls=3, tool_calls=0))
    auxiliary = AuxiliaryModel(release=case["scenario"] == "overflow")
    recorder = InputRecorder(SkillRetentionSelector(auxiliary,
        ledger.start_segment(BudgetLimits(model_calls=3, tool_calls=0)),
        input_window=11000 if case["scenario"] == "overflow" else 200000))
    manager = ContextManager(store, context, ArtifactStore(store, context), window=16000,
        output_reserve=1000, safety_margin=512, compactor=OfflineSummary(), skill_selector=recorder)
    main = MainProbe()
    ManagedModel(main, manager).generate(messages)
    task_context = recorder.calls[0]["task_context"]
    received = task_context.get("uncompressed_messages", task_context.get("recent_messages", []))
    expected = [m.model_dump(mode="json", exclude={"provider_state"}) for m in original[covered:]]
    # 规程工具结果可投影成读取记录；所有普通消息和整体顺序必须完整。
    ordinary = lambda rows: [m for m in rows if m["role"] != "tool"]
    observed = {"all_uncovered_messages": len(received) == len(expected) and ordinary(received) == ordinary(expected),
        "middle_present": f"MID_{phase}_C05" in json.dumps(received, ensure_ascii=False),
        "summary_matches": task_context["previous_memory"] == summary,
        "raw_unchanged": messages == original and store.snapshot(context).messages == original,
        "provider_state_excluded": all("provider_state" not in m for m in received),
        "main_called": bool(main.calls), "selector_model_calls": len(auxiliary.calls),
        "selection_budget_spent": ledger.usage.model_calls,
        "fallback": store.extension(context, "context", {})["skill_retention"]["fallback"],
        "retained_body_count": sum(m.content.count(BODY) for m in main.calls[-1])}
    return {**case,"observed":observed,"passed":all(observed[k] == v for k,v in case["expected"].items()),
        "source_covered":covered,"expected_message_count":len(expected),"actual_message_count":len(received),
        "input_messages":[m.model_dump(mode="json") for m in original],
        "consumer_input":recorder.calls[0],"auxiliary_requests":auxiliary.calls,
        "main_input":[m.model_dump(mode="json") for m in main.calls[-1]]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    suite = json.loads(args.suite.read_text(encoding="utf-8"))
    with args.report.open("x", encoding="utf-8") as output:
        output.write("{}")
    records = [run_case(case) for case in suite["cases"]]
    report = {"scope":suite["scope"],"suite_sha256":sha256(args.suite.read_bytes()).hexdigest(),
        "live_requests":0,"passed":sum(r["passed"] for r in records),"total":len(records),"records":records}
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed":report["passed"],"total":len(records),"cases":[
        {"id":r["id"],"passed":r["passed"],"expected_messages":r["expected_message_count"],
         "actual_messages":r["actual_message_count"]} for r in records]}, ensure_ascii=False))
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
