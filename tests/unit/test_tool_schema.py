"""MCP 原始 schema 的保守本地校验：只判定 required 与单一简单类型。"""

import pytest

from min_agent.tools.schema import validate_arguments


SCHEMA = {
    "type": "object",
    "properties": {
        "message": {"type": "string"},
        "count": {"type": "integer"},
        "ratio": {"type": "number"},
        "enabled": {"type": "boolean"},
        "items": {"type": "array"},
        "payload": {"type": "object"},
    },
    "required": ["message"],
}


def test_required_and_simple_types_are_checked():
    assert validate_arguments(SCHEMA, {"message": "hi"}) == []
    assert validate_arguments(SCHEMA, {}) == [
        {"field": "message", "reason": "缺少必需参数"}]
    assert validate_arguments(SCHEMA, {"message": 1}) == [
        {"field": "message", "reason": "类型应为 string"}]
    assert validate_arguments(SCHEMA, {"message": "hi", "count": True}) == [
        {"field": "count", "reason": "类型应为 integer"}]
    assert validate_arguments(SCHEMA, {"message": "hi", "ratio": 3}) == []
    assert validate_arguments(SCHEMA, {"message": "hi", "items": {}}) == [
        {"field": "items", "reason": "类型应为 array"}]
    assert validate_arguments(SCHEMA, {"message": "hi", "payload": []}) == [
        {"field": "payload", "reason": "类型应为 object"}]


def test_ambiguous_schemas_are_left_to_the_server():
    schema = {
        "type": "object",
        "properties": {
            "either": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
            "maybe": {"type": ["string", "null"]},
            "choice": {"type": "string", "enum": ["a", "b"]},
            "ref": {"$ref": "#/$defs/thing"},
        },
        "required": ["either"],
    }
    assert validate_arguments(schema, {"either": 1, "maybe": None,
                                       "choice": "z", "ref": 123}) == []


def test_unknown_properties_and_non_object_arguments():
    assert validate_arguments(SCHEMA, {"message": "hi", "extra": [1]}) == []
    assert validate_arguments(SCHEMA, ["hi"]) == [
        {"field": "<arguments>", "reason": "参数必须是 JSON 对象"}]


@pytest.mark.parametrize("schema", [
    {},
    {"type": "object"},
    {"type": "object", "required": None, "properties": None},
])
def test_missing_or_malformed_schema_rules_do_not_reject_valid_arguments(schema):
    assert validate_arguments(schema, {"anything": "goes"}) == []