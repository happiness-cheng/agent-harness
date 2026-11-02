"""Ollama原生/api/chat适配器；只连接显式配置的本机服务，不读取云端凭据或环境代理。

内部仍使用 Message/ToolCall/ModelResponse；本模块仅做协议转换和一次HTTP请求，
不执行工具，也不改变Runtime的循环、预算、权限或检查点行为。
"""

import json
import re
from threading import RLock
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from min_agent.llm.client import LLMError
from min_agent.llm.openai_protocol import load_json
from min_agent.model_context import ModelContextLimits
from min_agent.models import Message, ModelResponse, ToolCall


_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
# 现有上下文管理按UTF-8字节保守估算；中文在Qwen分词中最多约1token/字符(3字节)，
# 因此3字节/token是“不会低估token数”的保守上界，宁可提前压缩也不静默截断。
BYTES_PER_TOKEN = 3
# Ollama在输入超出num_ctx时会把prompt截到接近上限；留出固定余量用于识别这种截断。
TRUNCATION_MARGIN = 16
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_MAX_REQUEST_BYTES = 8 * 1024 * 1024
_TOOL_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")
_CALL_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_KEEP_ALIVE = re.compile(r"-?[0-9]{1,7}(?:ns|us|ms|s|m|h)?")


def normalize_base_url(value: str) -> str:
    """只接受本机Ollama地址；拒绝凭据、路径、查询串和局域网/公网主机。"""
    if not isinstance(value, str) or not value or not value.isascii() or any(character.isspace() for character in value):
        raise ValueError("Ollama地址无效：需要显式ASCII地址")
    target = urlsplit(value)
    if target.scheme not in {"http", "https"}:
        raise ValueError("Ollama地址只支持http或https")
    if target.hostname not in _LOOPBACK_HOSTS:
        raise ValueError("只允许连接本机Ollama（127.0.0.1/localhost/::1），不接受局域网或公网地址")
    if target.username is not None or target.password is not None:
        raise ValueError("Ollama地址不能包含用户名或密码")
    try:
        port = target.port
    except ValueError:
        raise ValueError("Ollama端口无效") from None
    port = 11434 if port is None else port
    if target.path not in {"", "/"} or target.query or target.fragment:
        raise ValueError("Ollama地址不能包含路径、查询串或锚点")
    host = f"[{target.hostname}]" if ":" in target.hostname else target.hostname
    return f"{target.scheme}://{host}:{port}"


class OllamaSettings(BaseModel):
    """本地Ollama的显式配置；没有API Key，也没有代理入口。"""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", allow_inf_nan=False)

    model: str = Field(default="qwen3.5:4b", pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
    base_url: str = "http://127.0.0.1:11434"
    # 上限对齐qwen3.5:4b声明的262144；默认32768来自本机32K长上下文多轮验收，
    # 低内存环境可显式--ollama-num-ctx 16384。
    num_ctx: int = Field(default=32768, ge=2048, le=262144)
    max_output_tokens: int = Field(default=1024, ge=64, le=8192)
    temperature: float = Field(default=0.0, ge=0, le=2)
    keep_alive: str = Field(default="30m", pattern=r"^-?[0-9]{1,7}(?:ns|us|ms|s|m|h)?$")
    think: bool = False
    stream: Literal[False] = False
    timeout_seconds: float = Field(default=180.0, gt=0, le=600)
    max_requests: int = Field(default=100, ge=1, le=1000)

    @field_validator("base_url")
    @classmethod
    def _check_base_url(cls, value: str) -> str:
        return normalize_base_url(value)

    @model_validator(mode="after")
    def _check_window(self):
        if self.max_output_tokens > self.num_ctx:
            raise ValueError("输出上限不能超过上下文窗口")
        return self

    @property
    def chat_url(self) -> str:
        return self.base_url + "/api/chat"

    @property
    def tags_url(self) -> str:
        return self.base_url + "/api/tags"


def build_tools(tools: list[dict[str, object]] | None) -> list[dict[str, object]]:
    """把内部工具Schema包装成Ollama的function定义；不做字符串拼接。"""
    if tools is None:
        return []
    if not isinstance(tools, list):
        raise TypeError("工具定义必须为列表")
    result, names = [], set()
    for tool in tools:
        if not isinstance(tool, dict) or set(tool) != {"name", "description", "parameters"}:
            raise ValueError("工具定义字段不符")
        name = tool["name"]
        if not isinstance(name, str) or _TOOL_NAME.fullmatch(name) is None or name in names:
            raise ValueError("工具名称无效或重复")
        if not isinstance(tool["description"], str) or not tool["description"].strip():
            raise ValueError("工具描述无效")
        parameters = tool["parameters"]
        if not isinstance(parameters, dict) or parameters.get("type") != "object":
            raise ValueError("工具Schema无效")
        names.add(name)
        result.append({"type": "function", "function": {
            "name": name, "description": tool["description"], "parameters": parameters,
        }})
    # 统一经过严格JSON往返，拒绝非有限数值或不可序列化对象。
    return load_json(json.dumps(result, ensure_ascii=False, allow_nan=False))


def build_messages(messages: list[Message]) -> list[dict[str, object]]:
    """把内部消息转换为Ollama chat消息，并用历史tool_calls解析tool_name。

    Runtime只保存tool_call_id；Ollama工具消息需要tool_name。未知、重复或无法
    对应的调用ID一律失败，不猜测工具名称；其他提供商的provider_state不发送。
    """
    if not isinstance(messages, list) or not messages:
        raise ValueError("必须提供非空消息列表")
    checked = []
    for message in messages:
        if not isinstance(message, Message):
            raise TypeError("消息类型错误")
        checked.append(Message.model_validate(message.model_dump(warnings=False)))
    names: dict[str, str] = {}
    answered: set[str] = set()
    result: list[dict[str, object]] = []
    for message in checked:
        if message.role == "tool":
            call_id = message.tool_call_id
            if call_id not in names:
                raise ValueError("工具结果没有可对应的工具调用，不能猜测工具名称")
            if call_id in answered:
                raise ValueError("同一次工具调用收到重复结果")
            answered.add(call_id)
            result.append({"role": "tool", "content": message.content, "tool_name": names[call_id]})
            continue
        if set(names) - answered:
            raise ValueError("工具结果尚未配齐，不能追加下一轮消息")
        if message.tool_calls:
            for call in message.tool_calls:
                if call.id in names:
                    raise ValueError("工具调用ID在历史中重复")
                names[call.id] = call.name
            result.append({"role": "assistant", "content": message.content, "tool_calls": [
                {"type": "function", "function": {"name": call.name, "arguments": call.arguments}}
                for call in message.tool_calls
            ]})
            continue
        result.append({"role": message.role, "content": message.content})
    if set(names) - answered:
        raise ValueError("工具结果尚未配齐")
    return result


def _arguments(value: object) -> dict[str, object]:
    """工具参数必须是结构化对象；兼容模型把对象编码成JSON字符串的常见返回。"""
    if isinstance(value, dict):
        return load_json(json.dumps(value, ensure_ascii=False, allow_nan=False))
    if isinstance(value, str):
        parsed = load_json(value)
        if not isinstance(parsed, dict):
            raise ValueError("工具参数必须是对象")
        return parsed
    raise ValueError("工具参数必须是结构化对象")


def _new_call_id(used: set[str]) -> str:
    while True:
        candidate = "ollama-" + uuid4().hex
        if candidate not in used:
            return candidate


def parse_chat_response(raw: object) -> ModelResponse:
    """把/api/chat响应转换为ModelResponse；空响应、拒绝态和截断都不伪装成成功。"""
    if not isinstance(raw, dict):
        raise ValueError("响应必须是对象")
    if raw.get("done") is False:
        raise ValueError("非流式响应未完成")
    if raw.get("error") is not None:
        raise ValueError("模型返回错误状态")
    message = raw.get("message")
    if not isinstance(message, dict):
        raise ValueError("响应消息格式错误")
    if message.get("role", "assistant") != "assistant":
        raise ValueError("响应角色错误")
    content = message.get("content") or ""
    if not isinstance(content, str):
        raise ValueError("响应正文类型错误")
    reason = raw.get("done_reason")
    if reason not in {None, "stop", "length"}:
        return ModelResponse(finish_reason="error")
    if reason == "length":
        # 长度截断时工具参数可能不完整，禁止执行；正文按原样返回并标记length。
        return ModelResponse(finish_reason="length", content=content or None)
    raw_calls = message.get("tool_calls") or []
    if not isinstance(raw_calls, list):
        raise ValueError("工具调用格式错误")
    if raw_calls:
        used: set[str] = set()
        calls = []
        for item in raw_calls:
            if not isinstance(item, dict) or not isinstance(item.get("function"), dict):
                raise ValueError("工具调用格式错误")
            function = item["function"]
            name = function.get("name")
            if not isinstance(name, str) or not name.strip() or len(name) > 64:
                raise ValueError("工具名称无效")
            arguments = _arguments(function.get("arguments"))
            returned = item.get("id")
            call_id = returned if isinstance(returned, str) and _CALL_ID.fullmatch(returned) else ""
            if not call_id or call_id in used:
                call_id = _new_call_id(used)
            used.add(call_id)
            calls.append(ToolCall(id=call_id, name=name, arguments=arguments))
        return ModelResponse(finish_reason="tool_calls", content=content if content.strip() else None, tool_calls=calls)
    if content.strip():
        return ModelResponse(finish_reason="stop", content=content)
    return ModelResponse(finish_reason="error")


def available_models(settings: OllamaSettings, *, transport: httpx.BaseTransport | None = None) -> list[str]:
    """只读查询本机模型列表；不下载模型、不发对话请求。"""
    try:
        with httpx.Client(transport=transport, timeout=min(10.0, settings.timeout_seconds),
                          trust_env=False, follow_redirects=False) as client:
            response = client.get(settings.tags_url)
            if response.status_code != 200:
                raise LLMError(f"http_{response.status_code}")
            raw = load_json(response.content)
    except httpx.HTTPError:
        raise LLMError("network_error") from None
    except (ValueError, TypeError):
        raise LLMError("invalid_response") from None
    models = raw.get("models") if isinstance(raw, dict) else None
    if not isinstance(models, list):
        raise LLMError("invalid_response")
    return [item["name"] for item in models
            if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"]]


def require_model(settings: OllamaSettings, *, transport: httpx.BaseTransport | None = None) -> None:
    """启动前确认模型已在本机；缺失时明确失败，不自动下载或切换模型。"""
    try:
        names = available_models(settings, transport=transport)
    except LLMError as error:
        raise ValueError(f"无法连接本机Ollama（{error.code}）；未回退其他模型，也未读取云端凭据") from None
    if settings.model not in names and settings.model + ":latest" not in names:
        raise ValueError(f"本机Ollama未安装模型 {settings.model}；本次不会自动下载其他模型")


class OllamaModel:
    """Runtime所需的最小模型接口；每次generate只发一次非流式POST。"""

    _provider = "ollama"

    def __init__(self, settings: OllamaSettings, *, transport: httpx.BaseTransport | None = None) -> None:
        if not isinstance(settings, OllamaSettings):
            raise TypeError("settings必须是OllamaSettings")
        self._settings = settings
        # trust_env=False：不使用系统HTTP(S)_PROXY，避免把本机请求送去云端代理。
        self._client = httpx.Client(transport=transport, timeout=settings.timeout_seconds,
                                    follow_redirects=False, trust_env=False)
        self._lock = RLock()
        self._requests = 0
        self._usage: list[dict[str, object]] = []

    def _build_body(self, messages: list[Message], tools: list[dict[str, object]] | None) -> dict[str, object]:
        body: dict[str, object] = {
            "model": self._settings.model,
            "messages": build_messages(messages),
            "stream": False,
            "think": self._settings.think,
            "keep_alive": self._settings.keep_alive,
            "options": {
                "num_ctx": self._settings.num_ctx,
                "num_predict": self._settings.max_output_tokens,
                "temperature": self._settings.temperature,
            },
        }
        if tools is not None:
            body["tools"] = build_tools(tools)
        return body

    def context_limits(self) -> ModelContextLimits:
        """把token窗口按保守字节上界映射给现有ContextManager，不冒充精确分词。"""
        return ModelContextLimits(
            model=self._settings.model,
            context_window=self._settings.num_ctx * BYTES_PER_TOKEN,
            max_output_tokens=self._settings.max_output_tokens,
            source=f"ollama:num_ctx={self._settings.num_ctx};{BYTES_PER_TOKEN}bytes/token",
        )

    def generate(
        self, messages: list[Message], *, tools: list[dict[str, object]] | None = None,
    ) -> ModelResponse:
        try:
            body = self._build_body(messages, tools)
            encoded = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
        except (ValueError, TypeError, KeyError, AttributeError):
            raise LLMError("invalid_request") from None
        # 与ContextManager使用同一保守字节窗口；宁可明确失败，也不让Ollama静默截断。
        if len(encoded) > self._settings.num_ctx * BYTES_PER_TOKEN:
            from min_agent.context import ContextLimit
            raise ContextLimit("请求超过本机模型的保守字节窗口；未发送该请求")
        if len(encoded) > _MAX_REQUEST_BYTES:
            raise LLMError("request_too_large")
        with self._lock:
            if self._client.is_closed:
                raise LLMError("client_closed")
            if self._requests >= self._settings.max_requests:
                raise LLMError("request_limit")
            self._requests += 1
            request_number = self._requests
        try:
            with self._client.stream("POST", self._settings.chat_url, content=encoded,
                                     headers={"Content-Type": "application/json"}) as response:
                if response.status_code != 200:
                    raise LLMError(f"http_{response.status_code}")
                data = bytearray()
                for chunk in response.iter_bytes():
                    if len(data) + len(chunk) > _MAX_RESPONSE_BYTES:
                        raise LLMError("response_too_large")
                    data.extend(chunk)
            raw = load_json(data)
            prompt_tokens = raw.get("prompt_eval_count") if isinstance(raw, dict) else None
            if type(prompt_tokens) is int and prompt_tokens > self._settings.num_ctx - TRUNCATION_MARGIN:
                # 宁可明确失败，也不把被截断的回答当成有效结果。
                from min_agent.context import ContextLimit
                raise ContextLimit("当前输入超出本机模型上下文，Ollama可能已截断；未采用该响应")
            result = parse_chat_response(raw)
            usage: dict[str, object] = {"request": request_number}
            for key in ("prompt_eval_count", "eval_count", "total_duration", "load_duration",
                        "prompt_eval_duration", "eval_duration"):
                value = raw.get(key) if isinstance(raw, dict) else None
                if type(value) is int and 0 <= value <= 1_000_000_000_000:
                    usage[key] = value
            with self._lock:
                self._usage.append(usage)
            return result
        except httpx.HTTPError:
            raise LLMError("network_error") from None
        except (ValueError, TypeError, KeyError, AttributeError):
            # 只映射固定类别；不输出原始异常、响应正文或模型输出。
            raise LLMError("invalid_response", diagnostics={"protocol_issue": "invalid_shape"}) from None

    def close(self) -> None:
        self._client.close()

    @property
    def request_count(self) -> int:
        with self._lock:
            return self._requests

    @property
    def usage(self) -> list[dict[str, object]]:
        with self._lock:
            return [dict(record) for record in self._usage]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
