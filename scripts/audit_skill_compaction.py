"""固定选择结果的 Skill/压缩集成探针；无外部请求，失败项写入报告。"""

import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

from min_agent.artifacts import ArtifactStore
from min_agent.compaction import Compactor
from min_agent.context import ContextLimit, ContextManager, ManagedModel
from min_agent.models import Message, ModelResponse, ToolCall
from min_agent.sessions import SessionStore


BODIES = ["润色规程：不得新增事实。POLISH_FULL_S07",
          "核验规程：保留来源和日期。EVIDENCE_FULL_S07",
          "排错规程：先复现，再对照验证。DEBUG_FULL_S07"]
NAMES = ["polish", "evidence", "debug"]


class FixedSelector:
    def __init__(self, decision):
        self.decision, self.calls = decision, []

    def select(self, candidates, task_context):
        self.calls.append({"candidates": deepcopy(candidates), "task_context": deepcopy(task_context)})
        return deepcopy(self.decision)


class Capture:
    def __init__(self):
        self.calls = []

    def generate(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        return ModelResponse(finish_reason="stop", content="固定离线返回，不作为模型效果")


class OfflineCompactor(Compactor):
    def _summarize(self, state, source, phase):
        return {"facts": ["较早交互已整理"], "constraints": [], "corrections": [],
                "open_questions": [], "source_references": []}


def read(identity, name, body):
    return [Message(role="assistant", content="", tool_calls=[
        ToolCall(id=identity, name="read_skill", arguments={"name": name})]),
        Message(role="tool", tool_call_id=identity,
                content=json.dumps({"ok": True, "result": body}, ensure_ascii=False))]


def body_count(messages, body):
    # 检查整个序列化输入，防止正文偷偷留在近期工具结果里。
    return sum(message.content.count(body) for message in messages)


def run_case(case):
    store = SessionStore()
    context = store.create(uuid4())
    bodies = list(BODIES)
    if case["scenario"] == "budget":
        bodies[0] *= 100
    messages = [Message(role="user", content="旧的过程记录。" * 4000)]
    for identity, name, body in zip(("p", "e", "d"), NAMES, bodies):
        messages += read(identity, name, body)
    messages += [Message(role="assistant", content="已读规程"),
        Message(role="user", content="继续润色和排错，核验工作已经结束"),
        Message(role="assistant", content="继续处理"), Message(role="user", content="按原规程收尾")]
    decision = {"keep": ["p"], "release": ["e"], "uncertain": ["d"]}
    if case["scenario"] == "release_all":
        decision = {"keep": [], "release": ["p", "e", "d"], "uncertain": []}
    if case["scenario"] == "invalid":
        decision = {"keep": ["invented"], "release": [], "uncertain": []}
    selector = FixedSelector(decision)
    capture = Capture()
    window = 16000
    if case["scenario"] == "no_pressure":
        window = 300000
    if case["scenario"] in {"budget", "preparation_failure"}:
        window = 8000 if case["scenario"] == "budget" else 1200
    manager = ContextManager(store, context, ArtifactStore(store, context), window=window,
        compactor=OfflineCompactor(None, store, context, None))
    # 基线没有此机制时也能运行，观察结果应失败，而不是构造接口报错。
    manager.skill_selector = selector
    original = deepcopy(messages)
    error = None
    try:
        ManagedModel(capture, manager).generate(messages)
        if case["scenario"] in {"reopen", "reload"}:
            if case["scenario"] == "reload":
                messages += read("e-reloaded", NAMES[1], bodies[1])
            manager = ContextManager(store, context, ArtifactStore(store, context), window=30000)
            manager.skill_selector = selector
            ManagedModel(capture, manager).generate(messages)
    except ContextLimit as exc:
        error = str(exc)
    visible = capture.calls[-1] if capture.calls else []
    observed = {"model_called": bool(capture.calls), "body_counts": [body_count(visible, body) for body in bodies],
        "selector_calls": len(selector.calls), "raw_unchanged": messages[:len(original)] == original,
        "selection_committed": bool(store.extension(context, "context", {}).get("skill_retention"))}
    record = {"id": case["id"], "group": case["group"], "scenario": case["scenario"],
        "expected": case["expected"], "observed": observed,
        "passed": all(observed[key] == value for key, value in case["expected"].items()),
        "error": error, "input_messages": [m.model_dump(mode="json") for m in original],
        "selector_calls": selector.calls,
        "visible_messages": [m.model_dump(mode="json") for m in visible]}
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    suite = json.loads(args.suite.read_text(encoding="utf-8"))
    with args.report.open("x", encoding="utf-8") as output:
        output.write("{}")
    records = [run_case(case) for case in suite["cases"]]
    report = {"suite_sha256": sha256(args.suite.read_bytes()).hexdigest(), "live_requests": 0,
        "scope": suite["scope"], "passed": sum(r["passed"] for r in records),
        "total": len(records), "records": records}
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "total": len(records),
        "cases": [{"id": r["id"], "passed": r["passed"]} for r in records]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
