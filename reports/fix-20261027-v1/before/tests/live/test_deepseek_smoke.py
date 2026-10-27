import os

import pytest

from min_agent.llm.smoke import run_live_smoke


@pytest.mark.live
def test_deepseek_public_question_and_calculator():
    if os.environ.get("AGENT_HARNESS_LIVE_DEEPSEEK") != "1":
        pytest.skip("未显式启用已获授权的DeepSeek公开烟测")
    path = os.environ.get("AGENT_HARNESS_CREDENTIALS_FILE")
    if not path:
        pytest.fail("必须显式指定凭据文件路径，不回退默认配置")
    result = run_live_smoke(path, allow_live=True, provider="deepseek")
    assert result["plain_ok"] and result["calculator_ok"]
    assert result["requests"] <= 3
