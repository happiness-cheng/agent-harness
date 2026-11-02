r"""显式授权的本机Ollama验收脚本；默认拒绝运行，不下载模型、不发云端请求。

用法（PowerShell，项目根目录）：
  .\.venv\Scripts\python.exe -X utf8 scripts\ollama_live_acceptance.py --allow-live
"""

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from min_agent.application import HarnessApplication
from min_agent.llm.ollama import OllamaModel, OllamaSettings, require_model
from min_agent.runtime import Runtime


def case_a(settings):
    """普通对话：不提供工具，只验证中文回答和正常结束。"""
    started = time.perf_counter()
    with OllamaModel(settings) as model:
        result = Runtime(model).run("用两句话介绍什么是 Agent。")
        usage = model.usage
    return {
        "case": "A-plain-chat", "status": result.status, "error_code": result.error_code,
        "duration_seconds": round(time.perf_counter() - started, 2),
        "answer": result.response.content if result.response else None,
        "model_calls": len(usage), "usage": usage,
        "passed": result.status == "completed" and bool(result.response and result.response.content),
    }


def _application_case(settings, database, prompt, judge):
    started = time.perf_counter()
    harness = HarnessApplication(database, model=OllamaModel(settings))
    try:
        session = harness.create_session("ollama验收")["session_id"]
        harness.submit(session, prompt)
        harness.wait(session, timeout=600)
        snapshot = harness.snapshot(session)
        usage = harness.model.usage
    finally:
        harness.close()
    messages = snapshot["messages"]
    # 从已持久化会话还原真实调用/结果对应关系；不依赖被替换的handler。
    calls = {}
    executed_calculator = []
    for message in messages:
        for call in message.get("tool_calls") or []:
            calls[call["id"]] = call
        if message["role"] != "tool":
            continue
        call = calls.get(message.get("tool_call_id")) or {}
        try:
            value = json.loads(message["content"])
        except (TypeError, ValueError):
            value = {"ok": None}
        executed_calculator.append({
            "name": call.get("name"), "arguments": call.get("arguments"),
            "ok": value.get("ok") if isinstance(value, dict) else None,
            "result": value.get("result") if isinstance(value, dict) else None,
        })
    record = {
        "status": snapshot["task"]["status"], "error_code": snapshot["task"].get("error_code"),
        "duration_seconds": round(time.perf_counter() - started, 2),
        "model_calls": len(usage), "usage": usage,
        "tool_calls": [[call["name"] + ":" + json.dumps(call["arguments"], ensure_ascii=False)
                        for call in message["tool_calls"]] for message in messages if message["tool_calls"]],
        "tool_results": [entry for entry in executed_calculator],
        "answer": next((message["content"] for message in reversed(messages)
                        if message["role"] == "assistant" and message["content"]), None),
        "trace": [{"operation": event["operation"], "status": event["status"],
                   "error_code": event["error_code"]} for event in snapshot["trace"]],
        "budget_usage": snapshot["budget"]["usage"] if snapshot["budget"] else None,
        "executed_calculator": executed_calculator,
    }
    record["passed"] = bool(record["status"] == "completed" and judge(record))
    return record


def case_b(settings, database):
    """calculator：真实ToolRegistry执行并回传703。"""
    record = _application_case(
        settings, database, "请使用 calculator 工具计算 37 乘以 19，并告诉我结果。",
        lambda record: any(entry["name"] == "calculator" and entry["result"] == "703"
                           for entry in record["executed_calculator"])
        and "703" in (record["answer"] or ""),
    )
    record["case"] = "B-calculator-tool"
    return record


def case_c(settings, database):
    """多轮工具反馈：至少两次模型响应和两次真实工具结果，最终84。"""
    record = _application_case(
        settings, database,
        "请分两步完成，每步都必须调用 calculator 工具，不要心算："
        "第一步，用 calculator 计算 12+30，等工具返回后再继续；"
        "第二步，用 calculator 把上一步的结果乘以 2。完成后告诉我最终结果。",
        lambda record: [entry["result"] for entry in record["executed_calculator"]
                        if entry["name"] == "calculator"] == ["42", "84"]
        and "84" in (record["answer"] or ""),
    )
    record["case"] = "C-multi-round-tools"
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description="显式授权的本机Ollama验收（案例A/B/C）")
    parser.add_argument("--allow-live", action="store_true", help="必须显式提供，才允许调用本机Ollama")
    parser.add_argument("--model", default="qwen3.5:4b")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--num-ctx", type=int, default=32768)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--database", default="data/ollama-test.sqlite3", help="独立测试库，不碰工作库")
    parser.add_argument("--report", type=Path, help="写出脱敏JSON验收报告")
    parser.add_argument("--cases", default="ABC", help="要运行的案例，例如ABC或AB")
    options = parser.parse_args()
    if options.allow_live is not True:
        print(json.dumps({"ok": False, "error": "需要--allow-live显式授权；未调用本机模型"}, ensure_ascii=False))
        return 2
    settings = OllamaSettings(model=options.model, base_url=options.base_url,
                              num_ctx=options.num_ctx, timeout_seconds=options.timeout)
    try:
        require_model(settings)
    except ValueError as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 1
    # 使用独立的测试库；不打开、不清空现有工作库。
    root = Path(options.database)
    report = {
        "started_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        "model": settings.model, "base_url": settings.base_url, "num_ctx": settings.num_ctx,
        "stream": False, "think": settings.think, "cases": {},
    }
    runners = {
        "A": lambda: case_a(settings),
        "B": lambda: case_b(settings, root),
        "C": lambda: case_c(settings, root),
    }
    for name in options.cases.upper():
        if name not in runners:
            print(json.dumps({"ok": False, "error": f"未知案例 {name}"}, ensure_ascii=False))
            return 2
        print(f"运行案例 {name} ...", flush=True)
        report["cases"][name] = runners[name]()
        case = report["cases"][name]
        print(f"案例 {name}: status={case['status']} passed={case['passed']} "
              f"duration={case['duration_seconds']}s", flush=True)
    report["finished_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    report["passed"] = all(case["passed"] for case in report["cases"].values())
    if options.report is not None:
        options.report.parent.mkdir(parents=True, exist_ok=True)
        options.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"报告已写入 {options.report}", flush=True)
    print(json.dumps({"ok": report["passed"],
                      "cases": {name: case["passed"] for name, case in report["cases"].items()}},
                     ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
