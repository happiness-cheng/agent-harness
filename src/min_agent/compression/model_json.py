"""模型 JSON 输出容错：只去掉 Markdown 代码围栏，不猜测或改写内容。

真实模型（本机 qwen3.5:4b 已复现）常把 JSON 包在 ```json 围栏里。压缩选择器
原先直接 ``model_validate_json``，导致工具摘要失败并让后续请求报 context_limit。
本模块只做**格式去壳**：内容仍由各自的 Pydantic 模型严格校验，非法 JSON 照常失败。
"""

from __future__ import annotations


def strip_code_fence(text: str) -> str:
    """去掉整段包裹的 ``` / ```json 围栏；不是围栏则只去首尾空白。"""
    if not isinstance(text, str):
        raise TypeError("模型输出必须是字符串")
    value = text.strip()
    if not value.startswith("```"):
        return value
    newline = value.find("\n")
    if newline == -1:
        return value
    value = value[newline + 1:]
    if value.rstrip().endswith("```"):
        value = value.rstrip()[:-3]
    return value.strip()