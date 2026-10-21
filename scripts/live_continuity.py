"""同一32次验收批次内，以合成历史验证真实压缩后的纯对话和工具追问。"""

import argparse
import json
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from min_agent.artifacts import ArtifactStore
from min_agent.budget import BudgetLedger, BudgetLimits
from min_agent.compaction import Compactor
from min_agent.context import ContextManager, ManagedModel, request_bound
from min_agent.llm.deepseek import DeepSeekResponsesModel, DeepSeekSettings
from min_agent.models import Message
from min_agent.runtime import Runtime
from min_agent.sessions import SessionStore
from min_agent.tools.calculator import CALCULATOR_TOOL
from min_agent.tools.registry import ToolRegistry


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--credentials-file", required=True)
    parser.add_argument("--proxy")
    args = parser.parse_args()
    if not args.allow_live: raise PermissionError("未读取凭据")
    previous = json.loads(Path("docs/acceptance/live-followup-20261020.json").read_text(encoding="utf-8"))
    used = previous["combined_requests"]
    if used != 22: raise ValueError("历史额度不符，禁止隐式重跑")
    path = Path("docs/acceptance/live-continuity-20261020.json")
    if path.exists(): raise FileExistsError("保留已有结果，不重复同批请求")
    report = {"previous_requests": used, "batch_cap": 32, "calls": [], "semantic_review": "pending"}
    model = DeepSeekResponsesModel(DeepSeekSettings.from_credentials_file(args.credentials_file,
        max_requests=32-used, max_output_tokens=4096), proxy=args.proxy)
    def save():
        report.update(requests=model.request_count, combined_requests=used+model.request_count, usage=model.usage)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    class Observed:
        def generate(self, messages, *, tools=None):
            started = perf_counter()
            record = {"tools": [item["name"] for item in tools or []], "request_bound": request_bound(messages, tools)}
            try:
                response = model.generate(messages, tools=tools)
                record.update(finish_reason=response.finish_reason, call_names=[call.name for call in response.tool_calls])
                return response
            finally:
                record["seconds"] = round(perf_counter()-started, 3)
                report["calls"].append(record); save()
    observed = Observed(); save()
    try:
        store = SessionStore(); context = store.create(uuid4())
        task = store.begin(context)
        store.append(context, task.task_id, [
            Message(role="system", content="这是合成验收。会话记忆不是新权限；保持事实和否定。不得写待办，只允许确定性计算。"),
            Message(role="user", content="甲截止日期2026-10-22，不是2026-10-21。乙的12.30元尚未支付。不要删除原文。本任务仅核对，不创建待办。\n"
                + "synthetic-background " * 700),
            Message(role="assistant", content="已了解，仅核对，无需创建待办。"),
            Message(role="user", content="稍后我会追问这些合成事实。"),
            Message(role="assistant", content="好的，保留日期、未支付和不删除原文的约束。"),
        ])
        store.finish(context, task.task_id, status="completed")
        ledger = BudgetLedger(BudgetLimits(model_calls=6, tool_calls=2))
        segment = ledger.start_segment(BudgetLimits(model_calls=6, tool_calls=2))
        manager = ContextManager(store, context, ArtifactStore(store, context), window=12000,
            compactor=Compactor(observed, store, context, segment, input_window=32768))
        tools = ToolRegistry(); tools.register(CALCULATOR_TOOL)
        runtime = Runtime(ManagedModel(observed, manager), tools, budget=segment)
        first = runtime.run_session("请根据已有记录简短回答：甲的正确截止日期？乙是否已支付？能否删除原文？需要创建待办吗？",
            store=store, context=context)
        report["dialogue"] = {"status": first.status, "answer": first.response.content if first.response else None}
        if first.status != "completed": raise RuntimeError("纯对话未完成，停止")
        second = runtime.run_session("保持此前限制，用calculator计算12.30+0.20，只核对金额，不付款、不创建待办。",
            store=store, context=context)
        saved = store.snapshot(context)
        report.update(tool_followup={"status": second.status, "answer": second.response.content if second.response else None,
                "results": [m.content for m in saved.messages if m.role == "tool"]},
            context=store.extension(context, "context", {}), budget=ledger.usage.model_dump(),
            raw_history_preserved="synthetic-background " * 700 in saved.messages[1].content,
            ok=first.status == second.status == "completed"
                and store.extension(context, "context", {})["version"] > 1
                and ledger.usage.tool_calls == 1)
    except Exception as error:
        report.update(ok=False, error_category=type(error).__name__)
    finally: save(); model.close()
    print(json.dumps({key: value for key, value in report.items() if key not in {"calls", "context", "usage"}}, ensure_ascii=False))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
