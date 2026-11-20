"""JSON Schema 的保守本地校验：只判定能确定的 required 与简单类型。

MCP 工具的 ``inputSchema`` 来自外部服务器，本地不重写、不转换成 Pydantic；
完整校验仍由服务器负责。这里只补两道能可靠判定的闸：必需字段是否存在、
声明为单一 JSON 类型时类型是否匹配。``anyOf/oneOf/allOf/$ref``、类型数组、
``enum`` 等无法无歧义判定的规则一律跳过，宁可放行也不误拒合法输入。
"""

_JSON_TYPES = frozenset(
    {"string", "integer", "number", "boolean", "object", "array", "null"})

# 出现这些关键字时，字段的真实约束不是单一 type 能表达的，交给服务器。
_AMBIGUOUS_KEYWORDS = (
    "anyOf", "oneOf", "allOf", "$ref", "enum", "const", "not", "if", "then", "else",
)


def _type_matches(value: object, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "null":
        return value is None
    return True


def validate_arguments(schema: dict, arguments: object) -> list[dict[str, str]]:
    """返回本地可判定的参数问题；空列表表示「本地没有理由拒绝」。"""
    if not isinstance(arguments, dict):
        return [{"field": "<arguments>", "reason": "参数必须是 JSON 对象"}]
    issues: list[dict[str, str]] = []
    required = schema.get("required")
    if isinstance(required, list):
        for key in required:
            if isinstance(key, str) and key not in arguments:
                issues.append({"field": key, "reason": "缺少必需参数"})
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for key, rule in properties.items():
            if key not in arguments or not isinstance(rule, dict):
                continue
            expected = rule.get("type")
            if not isinstance(expected, str) or expected not in _JSON_TYPES:
                continue
            if any(keyword in rule for keyword in _AMBIGUOUS_KEYWORDS):
                continue
            if not _type_matches(arguments[key], expected):
                issues.append({"field": key, "reason": f"类型应为 {expected}"})
    return issues