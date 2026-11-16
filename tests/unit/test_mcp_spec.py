"""MCP 配置与命名规则的离线契约。"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from min_agent.mcp.spec import (
    MCPServerSpec, load_mcp_config, normalize_tool_name, prefixed_tool_name,
)


def spec(**overrides):
    payload = dict(name="docs", description="Documentation server.", command="node",
                   args=["server.js"], policy="allow")
    payload.update(overrides)
    return MCPServerSpec(**payload)


def test_config_loads_servers_and_rejects_duplicates(tmp_path):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"servers": [
        {"name": "docs", "description": "Docs", "command": "node", "args": ["a.js"]},
        {"name": "deploy", "description": "Deploy", "command": "node", "args": ["b.js"],
         "policy": "confirm"},
    ]}), encoding="utf-8")

    servers = load_mcp_config(path)

    assert [server.name for server in servers] == ["docs", "deploy"]
    assert servers[1].policy == "confirm"

    path.write_text(json.dumps({"servers": [
        {"name": "docs", "description": "A", "command": "node"},
        {"name": "docs", "description": "B", "command": "node"},
    ]}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_mcp_config(path)


@pytest.mark.parametrize(("overrides", "error"), [
    ({"name": "bad name"}, ValidationError),
    ({"name": ""}, ValidationError),
    ({"policy": "always"}, ValidationError),
    ({"description": "  "}, ValidationError),
    ({"env": {"A=B": "x"}}, ValidationError),
    ({"env": {"A": "x\x00"}}, ValidationError),
    ({"timeout_seconds": 0}, ValidationError),
])
def test_config_rejects_invalid_fields(overrides, error):
    with pytest.raises(error):
        spec(**overrides)


def test_tool_names_are_normalized_and_prefixed():
    assert normalize_tool_name("get version/2") == "get_version_2"
    assert prefixed_tool_name("docs", "get.version") == "mcp__docs__get_version"
    with pytest.raises(ValueError):
        normalize_tool_name("  ")
    with pytest.raises(ValueError):
        prefixed_tool_name("x" * 32, "y" * 40)


def test_config_rejects_non_list_servers(tmp_path):
    path = Path(tmp_path) / "mcp.json"
    path.write_text(json.dumps({"servers": {}}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_mcp_config(path)
