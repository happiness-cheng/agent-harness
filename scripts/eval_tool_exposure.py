"""单变量对比工具暴露或知识材料；最多8次首轮调用，不执行工具。"""

import argparse
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter

from eval_secretary_prompt import GENERAL_CASES, tool_definitions
from min_agent.llm.client import LLMError
from min_agent.llm.deepseek import DeepSeekResponsesModel, DeepSeekSettings
from min_agent.models import Message
from min_agent.task_state import WorkState


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--credentials-file", required=True)
    parser.add_argument("--proxy", required=True)
    parser.add_argument("--factor", choices=("tools", "knowledge"), default="tools")
    parser.add_argument("--knowledge-file", type=Path)
    args = parser.parse_args()
    if not args.allow_live:
        parser.error("须明确传入--allow-live，尚未读取凭据")
    prompt = args.prompt.read_text(encoding="utf-8")
    if not prompt.strip():
        parser.error("系统提示不能为空，尚未读取凭据")
    material = None
    if args.factor == "knowledge":
        if args.knowledge_file is None:
            parser.error("知识实验必须提供--knowledge-file，尚未读取凭据")
        material = args.knowledge_file.read_text(encoding="utf-8")
        if not material.strip():
            parser.error("知识材料不能为空，尚未读取凭据")
    tools = tool_definitions()
    cases = tuple(case for case in GENERAL_CASES if case[0] in {"grounded_learning", "travel_ambiguity"})
    conditions = {"tools_present": [tool["name"] for tool in tools], "no_tools": []}
    request_limit = 8
    if args.factor == "knowledge":
        cases = (("checkpoint_explanation",
                  "我在学习这个 Agent 项目。请用一个生活例子解释：检查点、人工确认和恢复执行分别做什么？"
                  "保存检查点是否表示用户已经批准继续或业务写入？结合你实际看到的项目实现说明，不知道的地方请明确说。",
                  "区分恢复状态与行动授权；不编造未见过的项目实现。"),)
        conditions = {"no_knowledge": [tool["name"] for tool in tools],
                      "with_knowledge": [tool["name"] for tool in tools]}
        request_limit = 4
    state = {**WorkState().model_dump(mode="json"),
             "current_date": "2026-10-22", "time_zone": "Asia/Shanghai"}
    report = {
        "experiment": "P07-knowledge-context" if material is not None else "P06-tool-exposure-ablation",
        "request_limit": request_limit, "requests": 0,
        "model": "deepseek-flash", "reasoning_effort": "high", "max_output_tokens": 2048,
        "prompt": {"text": prompt, "sha256_utf8": sha256(prompt.encode()).hexdigest()},
        "application_state": state, "available_tool_definitions": tools,
        "conditions": conditions,
        "input_scope": "固定提示、工具和问题，仅改变参考源码" if material is not None else "固定提示与消息，仅改变工具暴露",
        "records": [], "semantic_review": "pending", "run_state": "not_started",
    }
    if material is not None:
        report["knowledge_material"] = {"text": material, "sha256_utf8": sha256(material.encode()).hexdigest()}
    with args.report.open("x", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
    settings = DeepSeekSettings.from_credentials_file(
        args.credentials_file, max_requests=request_limit, max_output_tokens=2048,
    )

    def save(model):
        report.update(requests=model.request_count, usage=model.usage)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    with DeepSeekResponsesModel(settings, proxy=args.proxy) as model:
        report["run_state"] = "running"
        try:
            for case_index, (case_id, question, expectation) in enumerate(cases):
                messages = [
                    Message(role="system", content=prompt),
                    Message(role="user", content=json.dumps({"application_state": state}, ensure_ascii=False)),
                    Message(role="user", content=question),
                ]
                for repeat in (1, 2):
                    order = tuple(conditions)
                    if (case_index + repeat) % 2 == 0:
                        order = order[::-1]
                    for condition in order:
                        visible_tools = tools if conditions[condition] else None
                        request_messages = [message.model_copy(deep=True) for message in messages]
                        if material is not None:
                            request_messages.insert(2, Message(role="user", content=json.dumps(
                                {"reference_material": material if condition == "with_knowledge" else None},
                                ensure_ascii=False)))
                        message_data = json.dumps([message.model_dump(mode="json") for message in request_messages],
                                                  ensure_ascii=False, sort_keys=True)
                        record = {
                            "case_id": case_id, "repeat": repeat, "condition": condition,
                            "user": question, "expectation": expectation,
                            "messages_sha256": sha256(message_data.encode()).hexdigest(),
                            "tool_names": report["conditions"][condition],
                        }
                        started = perf_counter()
                        try:
                            response = model.generate(request_messages, tools=visible_tools)
                            record.update(finish_reason=response.finish_reason, answer=response.content,
                                          tool_calls=[call.model_dump() for call in response.tool_calls])
                        except LLMError as error:
                            record.update(error=error.code, diagnostics=error.diagnostics)
                            report["run_state"] = "failed"
                            report["records"].append(record)
                            return 1
                        finally:
                            record["seconds"] = round(perf_counter() - started, 3)
                        report["records"].append(record)
                        save(model)
                        print(json.dumps(record, ensure_ascii=False), flush=True)
            report["run_state"] = "finished"
        finally:
            save(model)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
