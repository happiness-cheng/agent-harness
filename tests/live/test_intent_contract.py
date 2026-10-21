"""获准后执行固定公开语义用例；自动结构通过后仍需人工审阅回答。"""

import json
import os

import pytest

from min_agent.llm.deepseek import DeepSeekResponsesModel, DeepSeekSettings
from tests.scenarios.intent_cases import CASES, MAX_REQUESTS, run_case


@pytest.mark.live
def test_deepseek_intent_contract():
    if os.environ.get("AGENT_HARNESS_LIVE_INTENT") != "1":
        pytest.skip("未显式启用已获授权的S18公开语义验收")
    path = os.environ.get("AGENT_HARNESS_CREDENTIALS_FILE")
    proxy = os.environ.get("AGENT_HARNESS_PROXY")
    if not path or not path.strip() or not proxy or not proxy.strip():
        pytest.fail("必须显式指定凭据文件及已批准本机代理，不自动回退")
    settings = DeepSeekSettings.from_credentials_file(
        path, max_requests=MAX_REQUESTS, max_output_tokens=4096,
    )
    reports = []
    with DeepSeekResponsesModel(settings, proxy=proxy) as model:
        for case in CASES:
            report = run_case(case, model)
            reports.append(report)
            print(json.dumps(report, ensure_ascii=False))
            if not report["structural_ok"]:
                break
        print(json.dumps({
            "cases_run": len(reports), "requests": model.request_count,
            "request_limit": MAX_REQUESTS, "semantic_review": "pending",
        }, ensure_ascii=False))
    if len(reports) != len(CASES) or not all(report["structural_ok"] for report in reports):
        pytest.fail(f"结构验收失败并停止：{reports[-1]['case_id']}；不自动重试")
