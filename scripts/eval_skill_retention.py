"""先评测整份Skill的保留判断；离线基线或最多12次无工具模型请求。"""
import argparse
from hashlib import sha256
import json
from pathlib import Path

from min_agent.llm.client import LLMError
from min_agent.llm.deepseek import DeepSeekResponsesModel, DeepSeekSettings
from min_agent.models import Message


def validate_selection(selection, candidates):
    valid = isinstance(selection, dict) and set(selection) == {"keep", "release", "uncertain"}
    if valid:
        valid = all(isinstance(selection[key], list) and all(isinstance(v, str) for v in selection[key])
                    for key in ("keep", "release", "uncertain"))
    if valid:
        chosen = selection["keep"] + selection["release"] + selection["uncertain"]
        valid = len(chosen) == len(set(chosen)) and set(chosen) == set(candidates)
    return {"valid": bool(valid), "retained": sorted(selection["keep"] + selection["uncertain"])
            if valid else list(candidates), "fallback": not valid}


def selector_messages(suite, case):
    candidates = [skill for skill in suite["skills"] if skill["id"] in case["candidate_ids"]]
    return [Message(role="system", content=suite["selector_prompt"]), Message(role="user", content=json.dumps(
        {"current_request": case["current_request"], "task_context": case["task_context"],
         "loaded_candidates": candidates}, ensure_ascii=False))]


def score(suite, case, selection):
    result = validate_selection(selection, case["candidate_ids"])
    retained = set(result["retained"])
    expected = case["expected"]
    result["missing_required"] = sorted(set(expected["must_retain"]) - retained)
    result["unwanted_retained"] = sorted(set(expected["must_release"]) & retained)
    skills = {skill["id"]: skill for skill in suite["skills"]}
    required_names = {skills[i]["name"] for i in expected["must_retain"]}
    result["stale_versions"] = [i for i in result["unwanted_retained"] if skills[i]["name"] in required_names]
    result["retained_utf8_bytes"] = sum(len(skills[i]["body"].encode()) for i in retained)
    result["safety_pass"] = result["valid"] and not result["missing_required"] and not result["stale_versions"]
    result["decision_pass"] = result["safety_pass"] and not result["unwanted_retained"]
    result["preferred_uncertainty_met"] = (result["valid"] and
        set(expected["prefer_uncertain"]) <= set(selection["uncertain"]))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--credentials-file")
    parser.add_argument("--proxy")
    args = parser.parse_args()
    if args.allow_live and (not args.credentials_file or not args.proxy):
        parser.error("真实调用须显式指定凭据及代理")
    raw = args.suite.read_bytes()
    suite = json.loads(raw)
    if len(suite["cases"]) != 6:
        parser.error("本轮限定6个场景，各重复2次，不得扩大12次预算")
    report = {"experiment": "S05-skill-retention", "suite_sha256": sha256(raw).hexdigest(),
              "suite": suite, "requests": 0, "request_limit": 12 if args.allow_live else 0,
              "live_config": {"model": "deepseek-flash", "reasoning_effort": "high", "max_output_tokens": 2048},
              "run_state": "running", "records": []}
    with args.report.open("x", encoding="utf-8") as output: json.dump(report, output, ensure_ascii=False, indent=2)

    def save(model=None):
        if model is not None: report.update(requests=model.request_count, usage=model.usage)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if not args.allow_live:
        for strategy in ("keep_all", "release_all"):
            for case in suite["cases"]:
                ids = case["candidate_ids"]
                selection = {"keep": ids if strategy == "keep_all" else [],
                             "release": ids if strategy == "release_all" else [], "uncertain": []}
                report["records"].append({"strategy": strategy, "case_id": case["id"],
                    "selection": selection, "score": score(suite, case, selection)})
        guard = suite["guard_cases"][-1]
        skills = {skill["id"]: skill for skill in suite["skills"]}
        required_bytes = sum(len(skills[i]["body"].encode()) for i in guard["must_retain"])
        report["budget_guard"] = {"required_bytes": required_bytes,
            "remaining_skill_bytes": guard["remaining_skill_bytes"],
            "expected_runtime_action": "block" if required_bytes > guard["remaining_skill_bytes"] else "continue",
            "scope": "仅预算参考计算，不是已接入生产的阻断验证"}
        report["run_state"] = "finished"
        save()
    else:
        settings = DeepSeekSettings.from_credentials_file(args.credentials_file, max_requests=12,
                                                        max_output_tokens=2048, reasoning_effort="high")
        with DeepSeekResponsesModel(settings, proxy=args.proxy) as model:
            try:
                for repeat in (1, 2):
                    for case in suite["cases"]:
                        messages = selector_messages(suite, case)
                        record = {"repeat": repeat, "case_id": case["id"],
                                  "messages": [m.model_dump(mode="json") for m in messages]}
                        report["records"].append(record)
                        try:
                            response = model.generate(messages, tools=None)
                        except LLMError as error:
                            record.update(error=error.code, diagnostics=error.diagnostics)
                            report["run_state"] = "failed"
                            return 1
                        record["response"] = response.model_dump(mode="json", exclude={"provider_state"})
                        try: selection = json.loads(response.content or "") if response.finish_reason == "stop" else None
                        except json.JSONDecodeError: selection = None
                        record["selection"] = selection
                        record["score"] = score(suite, case, selection)
                        save(model)
                        print(json.dumps({"case": case["id"], "repeat": repeat, "score": record["score"]}, ensure_ascii=False), flush=True)
                report["run_state"] = "finished"
            finally:
                save(model)
    return 0


if __name__ == "__main__": raise SystemExit(main())
