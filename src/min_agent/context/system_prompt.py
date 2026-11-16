"""组件化系统提示词：基础规则 + 各组件按请求生成的说明段。

MCP / 技能 / 记忆都可以各提供一段 ``() -> str``；返回空字符串就不追加。
提示词只在每次模型请求时组装，不写入会话历史。
"""

from typing import Callable, Sequence

Section = str | Callable[[], str]


class SystemPrompt:
    """不可变的基础提示词 + 可调用段；调用一次生成完整文本。"""

    def __init__(self, base: str, sections: Sequence[Section] = ()) -> None:
        if not isinstance(base, str) or not base.strip():
            raise ValueError("基础系统提示词不能为空白")
        self._base = base
        self._sections = tuple(sections)

    def __call__(self) -> str:
        parts = [self._base]
        for section in self._sections:
            text = section() if callable(section) else section
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
        return "\n\n".join(parts)
