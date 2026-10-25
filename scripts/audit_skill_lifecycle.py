"""先于实现的Skill生命周期验收基线；失败结果保留，不调用外部模型。"""

import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

from min_agent.artifacts import ArtifactStore
from min_agent.context import ContextLimit, ContextManager, ManagedModel, request_bound
from min_agent.models import Message, ModelResponse, ToolCall
from min_agent.sessions import SessionStore


def read(identity, body):
    return [Message(role="assistant", content="", tool_calls=[
        ToolCall(id=identity, name="read_skill", arguments={"name": "example"})]),
        Message(role="tool", tool_call_id=identity,
                content=json.dumps({"ok": True, "result": body}, ensure_ascii=False))]


def count_body(messages, body):
    def count(value):
        if isinstance(value, str): return int(value == body)
        if isinstance(value, dict): return sum(count(v) for v in value.values())
        if isinstance(value, list): return sum(count(v) for v in value)
        return 0
    total = 0
    for message in messages:
        try: value = json.loads(message.content)
        except json.JSONDecodeError: value = message.content
        total += count(value)
    return total


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    suite = json.loads(args.suite.read_text(encoding="utf-8"))
    with args.report.open("x", encoding="utf-8") as output: output.write("{}")
    records = []
    for case in suite["cases"]:
        store = SessionStore()
        context = store.create(uuid4())
        manager = ContextManager(store, context, ArtifactStore(store, context), window=60000)
        body = "# 规程\n保留事实边界，核对结果。"
        if case["scenario"] == "long":
            body = "# 规程\n" + "\n".join(f"步骤{i}：核对输入来源和处理结果。" for i in range(130))
        messages = [Message(role="user", content="继续按已加载规程处理")] + read("first", body)
        older = None
        if case["scenario"] == "duplicate": messages += read("again", body)
        if case["scenario"] == "version":
            older = body
            body += "\n新版规则：保留用户提供的日期。"
            messages += read("new-version", body)
        if case["scenario"] in {"compacted", "version"}:
            messages += [Message(role="assistant", content="上一轮已结束")]
            covered = len(messages)
            store.set_extension(context, "context", {"version": 2, "projections": {}, "summary": "旧轮次已整理",
                "covered": covered, "covered_hashes": [sha256(m.model_dump_json().encode()).hexdigest() for m in messages]})
            messages += [Message(role="user", content="继续遵循刚才的规程，不要重新读取文件")]
        if case["scenario"] == "unloaded": messages = [Message(role="user", content="普通问题，不加载Skill")]
        if case["scenario"] == "budget": manager.window = 1200
        original = deepcopy(messages)
        captured = []

        class Probe:
            def generate(self, visible, **kwargs):
                captured.extend(deepcopy(visible))
                return ModelResponse(finish_reason="stop", content="离线探针答复")

        error = None
        try: ManagedModel(Probe(), manager).generate(messages)
        except ContextLimit as exc: error = str(exc)
        observed = {"model_called": bool(captured), "full_body_count": count_body(captured, body),
                    "raw_history_unchanged": messages == original,
                    "older_body_count": count_body(captured, older) if older is not None else 0,
                    "request_bytes_bound": request_bound(captured) if captured else None,
                    "error": error}
        passed = all(observed[key] == value for key, value in case["expected"].items())
        records.append({"id": case["id"], "group": case["group"], "expected": case["expected"],
                        "observed": observed, "passed": passed, "skill_body": body,
                        "input_messages": [m.model_dump(mode="json") for m in original],
                        "visible_messages": [m.model_dump(mode="json") for m in captured]})
    result = {"suite_version": suite["version"], "suite_sha256": sha256(args.suite.read_bytes()).hexdigest(),
              "live_requests": 0, "passed": sum(r["passed"] for r in records), "total": len(records),
              "scope": "真实ManagedModel输入；压缩场景预置合法covered状态，未验证自动压缩触发或模型语义",
              "records": records}
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "total": result["total"],
                      "cases": [{"id": r["id"], "passed": r["passed"]} for r in records]}, ensure_ascii=False))
    # 基线失败属于测量结果，不等于探针运行失败；报告保留逐项判定。
    return 0


if __name__ == "__main__": raise SystemExit(main())
