"""MCP 服务器配置与命名规则；命令、参数、环境只来自可信 host 配置。"""

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_DISALLOWED = re.compile(r"[^A-Za-z0-9_-]")
MAX_TOOL_NAME = 64


class MCPServerSpec(BaseModel):
    """一个 MCP 服务器的静态配置；模型永远只能选择 ``name``。"""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    name: str
    description: str = Field(min_length=1, max_length=500)
    command: str = Field(min_length=1, max_length=1024)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    policy: Literal["allow", "confirm"] = "allow"
    timeout_seconds: int = Field(default=20, ge=1, le=300)

    @field_validator("name")
    @classmethod
    def check_name(cls, value: str) -> str:
        if not _NAME_PATTERN.match(value):
            raise ValueError("MCP 服务器名只能包含字母、数字、下划线和连字符，长度 1-32")
        return value

    @field_validator("description", "command")
    @classmethod
    def check_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("MCP 配置文本不能为空白")
        if "\x00" in value:
            raise ValueError("MCP 配置不能包含空字符")
        return value

    @field_validator("args")
    @classmethod
    def check_args(cls, value: list[str]) -> list[str]:
        for item in value:
            if "\x00" in item:
                raise ValueError("MCP 参数不能包含空字符")
        return value

    @field_validator("env")
    @classmethod
    def check_env(cls, value: dict[str, str]) -> dict[str, str]:
        for key, item in value.items():
            if not key or "=" in key or "\x00" in key or "\x00" in item:
                raise ValueError("MCP 环境变量名或值无效")
        return value

    @model_validator(mode="after")
    def check_derived(self) -> "MCPServerSpec":
        if self.policy not in {"allow", "confirm"}:
            raise ValueError("MCP 策略只能是 allow 或 confirm")
        return self


def load_mcp_config(path) -> tuple[MCPServerSpec, ...]:
    """从 JSON 读取服务器列表；重复名字直接拒绝，不做静默去重。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("servers"), list):
        raise ValueError("MCP 配置必须是 {\"servers\": [...]} 结构")
    servers = tuple(MCPServerSpec.model_validate(item) for item in data["servers"])
    names = [server.name for server in servers]
    if len(set(names)) != len(names):
        raise ValueError("MCP 服务器名重复")
    return servers


def normalize_tool_name(name: str) -> str:
    """把服务器返回的工具名映射到模型可接受的字符集；空结果直接拒绝。"""
    if not isinstance(name, str):
        raise ValueError("MCP 工具名必须是字符串")
    normalized = _DISALLOWED.sub("_", name.strip())
    if not normalized:
        raise ValueError("MCP 工具名规范化后为空")
    return normalized


def prefixed_tool_name(server: str, tool: str) -> str:
    """``mcp__<server>__<tool>``；超过 64 字符直接拒绝，避免悄悄截断。"""
    name = f"mcp__{normalize_tool_name(server)}__{normalize_tool_name(tool)}"
    if len(name) > MAX_TOOL_NAME:
        raise ValueError(f"MCP 工具名超过 {MAX_TOOL_NAME} 字符：{name}")
    return name
