"""模型适配器与自写 Runtime 之间的最小数据契约。"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class ToolCall(BaseModel):
    """模型提出的调用请求；arguments 仅保存数据，不执行工具。"""

    model_config = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False)

    id: str = Field(min_length=1, pattern=r"\S")
    name: str = Field(min_length=1, pattern=r"\S")
    arguments: dict[str, JsonValue]


class ProviderState(BaseModel):
    """提供商原生输出，用于协议回传；加密状态不解密或作为摘要展示。"""

    model_config = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False)
    provider: Literal["openai.responses", "deepseek.responses"]
    model: str = Field(min_length=1, pattern=r"\S")
    response_model: str = Field(min_length=1, pattern=r"\S")
    output: list[dict[str, JsonValue]] = Field(repr=False)


class Message(BaseModel):
    """一条对话消息；工具结果必须指明对应的调用 ID。"""

    model_config = ConfigDict(strict=True, extra="forbid")

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None = Field(default=None, min_length=1, pattern=r"\S")
    tool_calls: list[ToolCall] = Field(default_factory=list)
    provider_state: ProviderState | None = None

    @model_validator(mode="after")
    def validate_tool_reference(self) -> Self:
        if self.role == "tool" and self.tool_call_id is None:
            raise ValueError("工具消息必须提供 tool_call_id")
        if self.role != "tool" and self.tool_call_id is not None:
            raise ValueError("只有工具消息可以提供 tool_call_id")
        if self.tool_calls and self.role != "assistant":
            raise ValueError("只有 assistant 消息可以携带工具请求")
        if self.provider_state is not None and self.role != "assistant":
            raise ValueError("只有assistant消息可以携带原生提供商状态")
        call_ids = [call.id for call in self.tool_calls]
        if len(call_ids) != len(set(call_ids)):
            raise ValueError("同一条消息的工具调用 ID 不能重复")
        return self


class ModelResponse(BaseModel):
    """一次模型响应；正文、调用和结束原因分别保存。"""

    model_config = ConfigDict(strict=True, extra="forbid")

    finish_reason: Literal["stop", "tool_calls", "length", "refusal", "error"]
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    reasoning_summary: str | None = None
    provider_state: ProviderState | None = None

    @model_validator(mode="after")
    def validate_response_shape(self) -> Self:
        if self.finish_reason == "stop":
            if self.content is None or not self.content.strip():
                raise ValueError("stop 响应必须包含非空白正文")
            if self.tool_calls:
                raise ValueError("包含工具请求的响应不能标为 stop")
        if self.finish_reason == "tool_calls" and not self.tool_calls:
            raise ValueError("tool_calls 响应必须包含工具请求")

        call_ids = [call.id for call in self.tool_calls]
        if len(call_ids) != len(set(call_ids)):
            raise ValueError("同一次响应的工具调用 ID 不能重复")
        return self
