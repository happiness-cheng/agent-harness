import json

import pytest
from pydantic import BaseModel, ConfigDict, Field, RootModel

from min_agent.tools.registry import ToolDefinition, ToolRegistry


class ExpressionArguments(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    expression: str = Field(min_length=1, description="待计算的表达式")


def sentinel_handler(**kwargs):
    raise AssertionError("注册表操作不应执行 handler")


@pytest.fixture
def definition():
    return ToolDefinition(
        name="calculator",
        description="需要确定性数值计算时使用；这里只是测试定义。",
        parameters=ExpressionArguments,
        handler=sentinel_handler,
    )


def test_empty_registry_exposes_no_tools():
    assert ToolRegistry().schemas() == []


def test_registered_definition_can_be_found_without_executing_handler(definition):
    registry = ToolRegistry()

    registry.register(definition)
    found = registry.get("calculator")

    assert found.name == "calculator"
    assert found.parameters is ExpressionArguments
    assert found.handler is sentinel_handler


def test_model_metadata_contains_description_and_generated_schema_only(definition):
    registry = ToolRegistry()
    registry.register(definition)

    exposed = registry.schemas()

    assert len(exposed) == 1
    assert set(exposed[0]) == {"name", "description", "parameters"}
    assert exposed[0]["name"] == "calculator"
    assert exposed[0]["description"] == definition.description
    schema = exposed[0]["parameters"]
    assert schema["type"] == "object"
    assert schema["required"] == ["expression"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["expression"]["type"] == "string"
    assert schema["properties"]["expression"]["minLength"] == 1
    assert schema["properties"]["expression"]["description"] == "待计算的表达式"
    json.dumps(exposed, allow_nan=False)


def test_multiple_tools_keep_registration_order(definition):
    registry = ToolRegistry()
    registry.register(definition)
    registry.register(
        ToolDefinition("search", "搜索测试定义", ExpressionArguments, sentinel_handler)
    )

    assert [item["name"] for item in registry.schemas()] == ["calculator", "search"]


def test_duplicate_registration_does_not_replace_the_original(definition):
    registry = ToolRegistry()
    registry.register(definition)
    replacement = ToolDefinition(
        "calculator", "不同的说明", ExpressionArguments, lambda: "不应替换"
    )

    with pytest.raises(ValueError, match="重复"):
        registry.register(replacement)

    assert registry.get("calculator") is definition
    assert len(registry.schemas()) == 1


def test_unknown_tool_lookup_reports_the_requested_name():
    with pytest.raises(KeyError, match="unknown_tool"):
        ToolRegistry().get("unknown_tool")


def test_modifying_exported_metadata_does_not_change_the_registry(definition):
    registry = ToolRegistry()
    registry.register(definition)

    exposed = registry.schemas()
    exposed[0]["name"] = "changed"
    exposed[0]["parameters"]["properties"]["expression"]["type"] = "integer"
    exposed.clear()

    fresh = registry.schemas()
    assert fresh[0]["name"] == "calculator"
    assert fresh[0]["parameters"]["properties"]["expression"]["type"] == "string"


def test_registry_instances_do_not_share_registered_tools(definition):
    first = ToolRegistry()
    second = ToolRegistry()
    first.register(definition)

    assert second.schemas() == []
    with pytest.raises(KeyError):
        second.get("calculator")


@pytest.mark.parametrize(
    "changes",
    [
        {"name": ""},
        {"name": " "},
        {"name": 123},
        {"description": ""},
        {"description": None},
    ],
)
def test_invalid_metadata_is_rejected_without_partial_registration(changes):
    registry = ToolRegistry()
    values = {
        "name": "calculator",
        "description": "测试定义",
        "parameters": ExpressionArguments,
        "handler": sentinel_handler,
    }
    values.update(changes)

    with pytest.raises(ValueError):
        registry.register(ToolDefinition(**values))

    assert registry.schemas() == []


@pytest.mark.parametrize("parameters", [dict, BaseModel, ExpressionArguments(expression="1")])
def test_parameters_must_be_a_concrete_pydantic_model_class(parameters):
    registry = ToolRegistry()

    with pytest.raises(TypeError):
        registry.register(ToolDefinition("invalid", "测试定义", parameters, sentinel_handler))

    assert registry.schemas() == []


def test_parameter_schema_must_describe_an_object():
    registry = ToolRegistry()

    with pytest.raises(ValueError, match="对象"):
        registry.register(ToolDefinition("invalid", "测试定义", RootModel[str], sentinel_handler))

    assert registry.schemas() == []


def test_handler_must_be_callable():
    registry = ToolRegistry()

    with pytest.raises(TypeError, match="handler"):
        registry.register(ToolDefinition("invalid", "测试定义", ExpressionArguments, None))

    assert registry.schemas() == []
