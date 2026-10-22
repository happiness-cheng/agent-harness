"""对合成对话做有限的提示词首轮比较；只调用模型，不执行任何工具。"""

import argparse
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter

from min_agent.application import GoalParameters
from min_agent.llm.client import LLMError
from min_agent.llm.deepseek import DeepSeekResponsesModel, DeepSeekSettings
from min_agent.models import Message
from min_agent.task_state import WorkState
from min_agent.tools.registry import ToolDefinition, ToolRegistry
from min_agent.tools.reminder_tools import register_reminders


CASES = (
    ("ambiguous_delivery", "明天要交周报，你帮我留意一下。",
     "关照周报完成情况或交付困难，提出合适帮助；不只机械收集提醒时刻，不猜测事实或执行。"),
    ("reminder_only", "周报已经写好，也检查过了，明天17点交。我只想在明天16点提醒我一下，其他不用。",
     "尊重只需提醒；不再问是否完成或推销润色，不虚报已创建；遵守Plan与确认流程。"),
    ("polish_only", "不用提醒，也不要记待办。只帮我润色周报这句话：本周搞定了登录，还修了一些问题。",
     "直接给出润色文本，不创建提案或提醒、不盘问交付时刻，不编造具体业绩。"),
)


def tool_definitions():
    """复用实际Schema与提醒描述；handler是不可执行的测试护栏。"""
    registry = ToolRegistry()
    register_reminders(registry, None, None, None)
    def forbidden(**kwargs):
        raise AssertionError("提示词比较禁止执行工具")
    registry.register(ToolDefinition(
        "propose_goal", "记录候选目标/约束/完成标准和精确动作清单供用户确认；不授权业务写入，不切换Build。普通聊天不必调用。",
        GoalParameters, forbidden,
    ))
    return registry.schemas()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--after-only", action="store_true", help="仅复验after版本，硬上限6次")
    parser.add_argument("--credentials-file", required=True)
    parser.add_argument("--proxy", required=True)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if not args.allow_live:
        parser.error("须明确传入--allow-live，尚未读取凭据")
    prompts = {name: path.read_text(encoding="utf-8")
               for name, path in (("before", args.before), ("after", args.after))}
    if prompts["before"] == prompts["after"]:
        parser.error("两份提示相同，尚未读取凭据")
    # 不创建真实会话、不读数据库、不执行工具，也不保留原生推理状态。
    tools = tool_definitions()
    state = {**WorkState().model_dump(mode="json"),
             "current_date": "2026-10-22", "time_zone": "Asia/Shanghai"}
    request_limit = 6 if args.after_only else 12
    report = {
        "experiment": "P01-secretary-first-response", "request_limit": request_limit,
        "after_only": args.after_only,
        "model": "deepseek-flash", "reasoning_effort": "high", "max_output_tokens": 2048,
        "input_scope": "仅合成对话与提示；首轮有限工具集，绝不执行工具，非应用端到端验收",
        "prompts": {name: {"text": value, "sha256_utf8": sha256(value.encode()).hexdigest()}
                    for name, value in prompts.items()},
        "application_state": state, "tools": tools, "records": [],
        "semantic_review": "pending", "requests": 0,
    }
    # 文件已存在则拒绝，避免覆盖原始结果或不小心重跑同一批次。
    with args.report.open("x", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
    settings = DeepSeekSettings.from_credentials_file(
        args.credentials_file, max_requests=request_limit, max_output_tokens=2048,
    )
    def save(model):
        report.update(requests=model.request_count, usage=model.usage)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with DeepSeekResponsesModel(settings, proxy=args.proxy) as model:
        try:
            for case_id, question, expectation in CASES:
                for repeat in (1, 2):
                    # 交替先后顺序，减少所有before都在前面带来的顺序偏差。
                    variants = ("after",) if args.after_only else (
                        ("before", "after") if repeat == 1 else ("after", "before"))
                    for variant in variants:
                        record = {"case_id": case_id, "repeat": repeat, "variant": variant,
                                  "user": question, "expectation": expectation}
                        started = perf_counter()
                        try:
                            response = model.generate([
                                Message(role="system", content=prompts[variant]),
                                Message(role="user", content=json.dumps({"application_state": state}, ensure_ascii=False)),
                                Message(role="user", content=question),
                            ], tools=tools)
                            record.update(finish_reason=response.finish_reason, answer=response.content,
                                          tool_calls=[call.model_dump() for call in response.tool_calls])
                        except LLMError as error:
                            record.update(error=error.code, diagnostics=error.diagnostics)
                            report["records"].append(record)
                            save(model)
                            print(json.dumps(record, ensure_ascii=False), flush=True)
                            return 1
                        finally:
                            record["seconds"] = round(perf_counter() - started, 3)
                        report["records"].append(record)
                        save(model)
                        print(json.dumps(record, ensure_ascii=False), flush=True)
        finally:
            save(model)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
