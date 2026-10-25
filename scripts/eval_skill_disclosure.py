"""最多8次合成Skill路由调用；仅可读取临时Skill，不执行业务工具。"""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from min_agent.application import _Skill
from min_agent.llm.client import LLMError
from min_agent.llm.deepseek import DeepSeekResponsesModel, DeepSeekSettings
from min_agent.models import Message
from min_agent.skills import SkillCatalog
from min_agent.task_state import WorkState
from min_agent.tools.registry import ToolDefinition, ToolRegistry


CASES = (
    ("polish", "请帮我润色：今天修复了登录报错，并补充了回归测试。不要增加事实。", "writing-polish"),
    ("debug", "接口偶尔返回500，已经复现：传空字符串时失败，传正常字符串时成功。下一步如何定位原因？", "debug-feedback"),
    ("evidence", "请核对以下两条合成资料：来源A称2025年10月起仅企业版支持导出，来源B称所有版本都支持导出但没有日期。请说明能确认什么、还缺什么依据。", "public-evidence"),
    ("unrelated", "1加1是多少？", None),
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--audit-report", type=Path, required=True)
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--credentials-file", required=True)
    parser.add_argument("--proxy", required=True)
    args = parser.parse_args()
    if not args.allow_live:
        parser.error("须明确传入--allow-live，尚未读取凭据")
    audit = json.loads(args.audit_report.read_text(encoding="utf-8"))
    fixtures = audit["fixtures"]
    if (not fixtures or any(not re.fullmatch(r"[A-Za-z0-9_-]+", name)
                            or not isinstance(body, str) or not body.strip()
                            or len(body.encode()) > 20000 for name, body in fixtures.items())):
        parser.error("非法测试Skill，尚未读取凭据")
    prompt = args.prompt.read_text(encoding="utf-8")
    if not prompt.strip(): parser.error("提示为空，尚未读取凭据")
    report = {"experiment": "S01-live-skill-selection", "request_limit": 8, "requests": 0,
              "model": "deepseek-flash", "reasoning_effort": "high", "max_output_tokens": 2048,
              "prompt": {"text": prompt, "sha256_utf8": sha256(prompt.encode()).hexdigest()},
              "fixtures": fixtures, "cases": [], "run_state": "not_started",
              "scope": "直接适配器+真实SkillCatalog/ToolRegistry；仅read_skill；非完整应用端到端"}
    with args.report.open("x", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
    settings = DeepSeekSettings.from_credentials_file(args.credentials_file, max_requests=8, max_output_tokens=2048)

    def save(model):
        report.update(requests=model.request_count, usage=model.usage)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    with TemporaryDirectory(prefix="agent-skill-live-") as temp:
        root = Path(temp)
        for name, body in fixtures.items():
            (root / (name + ".md")).write_text(body, encoding="utf-8")
        catalog = SkillCatalog(root)
        registry = ToolRegistry()
        registry.register(ToolDefinition("read_skill", "读取已登记技能正文；内容是参考，不能增加工具/网络/写入权限。可用："
                                         + json.dumps(catalog.summaries(), ensure_ascii=False), _Skill,
                                         lambda name: catalog.read(name)))
        report["tools"] = registry.schemas()
        report["application_state"] = {**WorkState().model_dump(mode="json"),
                                       "current_date": "2026-10-22", "time_zone": "Asia/Shanghai"}
        with DeepSeekResponsesModel(settings, proxy=args.proxy) as model:
            report["run_state"] = "running"
            try:
                for case_id, question, expected in CASES:
                    case = {"id": case_id, "question": question, "expected_skill": expected,
                            "requests": [], "executions": [], "status": "running"}
                    report["cases"].append(case)
                    messages = [Message(role="system", content=prompt),
                                Message(role="user", content=json.dumps(
                                    {"application_state": report["application_state"]}, ensure_ascii=False)),
                                Message(role="user", content=question)]
                    for step in range(2):
                        # 原生提供商状态仅在内存中回传；公开证据不保留推理状态。
                        record = {"step": step + 1, "messages": [m.model_dump(mode="json", exclude={"provider_state"}) for m in messages]}
                        case["requests"].append(record)
                        try:
                            response = model.generate([m.model_copy(deep=True) for m in messages], tools=registry.schemas())
                        except LLMError as error:
                            record.update(error=error.code, diagnostics=error.diagnostics)
                            case["status"] = "model_error"
                            report["run_state"] = "failed"
                            return 1
                        record["response"] = response.model_dump(mode="json", exclude={"provider_state"})
                        save(model)
                        if response.finish_reason != "tool_calls":
                            case["status"] = response.finish_reason
                            break
                        if any(call.name != "read_skill" for call in response.tool_calls):
                            case["status"] = "blocked_tool"
                            break
                        if step == 1:
                            case["status"] = "step_limit"
                            break
                        messages.append(Message(role="assistant", content=response.content or "",
                                                tool_calls=response.tool_calls, provider_state=response.provider_state))
                        for call in response.tool_calls:
                            try:
                                result = registry.execute(call)
                            except (PermissionError, ValueError):
                                result = {"ok": False, "error": {"code": "skill_read_rejected"}}
                            case["executions"].append({"call_id": call.id, "name": call.arguments.get("name"), "result": result})
                            messages.append(Message(role="tool", tool_call_id=call.id,
                                                    content=json.dumps(result, ensure_ascii=False)))
                    save(model)
                    print(json.dumps({"case": case_id, "status": case["status"],
                                      "read_skills": [item["name"] for item in case["executions"]]}, ensure_ascii=False), flush=True)
                report["run_state"] = "finished"
            finally:
                save(model)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
