"""组件系统提示词：基础规则 + 按请求生成的段。"""

import pytest

from min_agent.context.system_prompt import SystemPrompt


def test_sections_are_evaluated_on_each_call_and_empty_sections_skipped():
    values = iter(["first", "second"])
    prompt = SystemPrompt("BASE", (lambda: next(values), "", "STATIC"))

    assert prompt() == "BASE\n\nfirst\n\nSTATIC"
    assert prompt() == "BASE\n\nsecond\n\nSTATIC"


def test_blank_base_is_rejected():
    with pytest.raises(ValueError):
        SystemPrompt("   ")
