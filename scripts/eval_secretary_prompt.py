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


WORKFLOW_CASES = (
    ("clear_draft", "明天要给客户发项目进展邮件，我还没开始写。请直接帮我起草，150字以内。已知事实：登录接口已完成；支付接口还在联调；周五计划继续测试。不要加其他进度，不用提醒，也不用记待办。",
     "直接基于给定事实起草，不重复追问已知进度，不创建业务提案，不编造上线或验收。"),
    ("discussion_only", "最近工作安排有点乱。我只想聊聊怎么安排下周，不要记录待办、设置提醒或替我做决定。你先帮我理理思路。",
     "帮助梳理或问一个关键问题，不把讨论变成业务写入或授权流程，不替用户决定真实安排。"),
    ("unclear_meeting", "明天要和客户开个重要的会，你帮我准备一下。",
     "承接会议准备目标并核对影响准备方向的关键问题；不直接猜会议议题、交付物或提醒时刻，不一次盘问整套问题。"),
)


GENERAL_CASES = (
    ("direct_rewrite", "把这句话改成正式表达：这个接口有时会挂。只改表达，不分析原因。",
     "直接改写，不启动技术诊断或追问动机，不增加故障频率或影响范围等事实。"),
    ("grounded_learning", "我已经能读懂工具调用循环，但权限和检查点只看过概念。明天想花一小时继续学习，请结合这个进度帮我安排。",
     "依据已给进度建议优先级，不泛列全部Agent知识，不编造用户经验。"),
    ("conflicting_goals", "Agent框架、RAG、分布式系统我都想学，现在很乱，也不知道该先做什么。",
     "先核对目标和约束或提出待核对的原因假设，不直接断言用户缺乏知识体系或实践。"),
    ("travel_ambiguity", "下周要去杭州出差，你帮我安排一下。",
     "核对影响安排的关键歧义，不擅自订票、不编造日期或预算，也不机械先问工作进度。"),
    ("review_missing_input", "帮我看看这个项目方案有哪些明显问题，先只评审，不修改文件。",
     "缺方案时请求材料，不编造方案或问题，不自动执行修改。"),
    ("no_extra_guidance", "这次不需要学习建议。我只想知道工具调用请求和工具执行结果有什么区别。",
     "直接解释指定概念，不转为学习诊断、学习计划或多余的工具操作。"),
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
    parser.add_argument("--after-only", action="store_true", help="仅复验after版本；请求硬门按所选套件计算")
    parser.add_argument("--suite", choices=("secretary", "workflow", "general"), default="secretary")
    parser.add_argument("--case", help="只运行所选套件中的一个场景ID")
    parser.add_argument("--repeats", type=int, choices=(1, 2), help="每场景每版本重复次数，默认沿用套件设置")
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
    cases = {"secretary": CASES, "workflow": WORKFLOW_CASES, "general": GENERAL_CASES}[args.suite]
    if args.case:
        cases = tuple(case for case in cases if case[0] == args.case)
        if not cases:
            parser.error("所选套件没有此场景，尚未读取凭据")
    repeat_count = args.repeats if args.repeats is not None else (2 if args.suite == "secretary" else 1)
    repeats = tuple(range(1, repeat_count + 1))
    # 不创建真实会话、不读数据库、不执行工具，也不保留原生推理状态。
    tools = tool_definitions()
    state = {**WorkState().model_dump(mode="json"),
             "current_date": "2026-10-22", "time_zone": "Asia/Shanghai"}
    request_limit = len(cases) * len(repeats) * (1 if args.after_only else 2)
    report = {
        "experiment": {"secretary": "P01-secretary-first-response", "workflow": "P02-workflow-first-response",
                       "general": "P04-generalization-first-response"}[args.suite],
        "request_limit": request_limit, "suite": args.suite,
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
            for case_index, (case_id, question, expectation) in enumerate(cases):
                for repeat in repeats:
                    # 交替先后顺序，减少所有before都在前面带来的顺序偏差。
                    before_first = repeat == 1 if args.suite == "secretary" else case_index % 2 == 0
                    variants = ("after",) if args.after_only else (
                        ("before", "after") if before_first else ("after", "before"))
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
