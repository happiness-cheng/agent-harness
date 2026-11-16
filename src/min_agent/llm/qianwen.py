"""千问AI平台（OpenAI兼容Chat Completions）适配器；不复用Responses解析器。

只做三件事：把内部 Message 编码成 chat 消息、把 choices[0] 解析成 ModelResponse、
把 HTTP/网络/协议故障映射成固定 LLMError 码。不保存响应正文、不回显凭据。
"""

import json
from pathlib import Path
from threading import RLock
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from min_agent.llm.client import LLMError, _http_error
from min_agent.llm.openai_protocol import load_json
from min_agent.contracts.models import Message, ModelResponse, ToolCall

BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
CHAT_URL = BASE_URL + "/chat/completions"
_MAX_REQUEST_BYTES = 4 * 1024 * 1024
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024

# 平台声明值（未实测）：只用于 ContextManager 的压缩阈值，不代表真实容量。
# 依据 docs/2026-11-14-qwen-model-selector-design.md §6。
DECLARED_LIMITS: dict[str, tuple[int, int]] = {
    "qwen3.8-flash": (1_048_576, 32_768),
    "qwen3.8-max": (1_048_576, 32_768),
    "qwen3.7-plus": (131_072, 16_384),
    "deepseek-v4.1-flash": (131_072, 16_384),
}
MODEL_LABELS = {
    "qwen3.8-flash": "Qwen3.8 Flash · 快",
    "qwen3.8-max": "Qwen3.8 Max · 强",
    "qwen3.7-plus": "Qwen3.7 Plus · 均衡",
    "deepseek-v4.1-flash": "DeepSeek V4.1 Flash · 跨厂商",
}


class QianwenSettings(BaseModel):
    """千问平台的一次运行配置；密钥不参与 repr 或序列化。"""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", allow_inf_nan=False)

    api_key: SecretStr = Field(repr=False, exclude=True)
    model: str = Field(default="qwen3.8-flash", pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
    max_output_tokens: int = Field(default=4096, ge=64, le=32768)
    max_requests: int = Field(default=100, ge=1, le=1000)
    timeout_seconds: float = Field(default=120.0, gt=0, le=300)

    @model_validator(mode="after")
    def _declared(self):
        limits = DECLARED_LIMITS.get(self.model)
        if limits is None:
            raise ValueError("模型不在声明表")
        if self.max_output_tokens > limits[1]:
            raise ValueError("输出上限超过声明能力")
        return self

    @classmethod
    def from_credentials_file(cls, path: str | Path, **options):
        """只读显式凭据文件里的 qianwen_api_key；不查环境变量或工具配置。"""
        try:
            data = load_json(Path(path).read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError("凭据文件必须是对象")
            key = data["qianwen_api_key"]
            if (not isinstance(key, str) or not key or not key.isascii()
                    or not key.isprintable() or any(character.isspace() for character in key)):
                raise ValueError("密钥格式无效")
            return cls(api_key=SecretStr(key), **options)
        except (OSError, UnicodeError, ValueError, TypeError, KeyError):
            raise ValueError("无法加载指定千问凭据或配置") from None


def build_tools(tools: list[dict[str, object]] | None) -> list[dict[str, object]]:
    """内部工具Schema → Chat Completions 的 function 定义。"""
    if tools is None:
        return []
    if not isinstance(tools, list):
        raise TypeError("工具定义必须为列表")
    result, names = [], set()
    for tool in tools:
        if not isinstance(tool, dict) or set(tool) != {"name", "description", "parameters"}:
            raise ValueError("工具定义字段不符")
        name = tool["name"]
        if not isinstance(name, str) or not name.strip() or len(name) > 64 or name in names:
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
    return load_json(json.dumps(result, ensure_ascii=False, allow_nan=False))


def build_messages(messages: list[Message]) -> list[dict[str, object]]:
    """内部消息 → chat 消息；工具请求与结果必须成对，缺一失败而不是猜。"""
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
        if message.provider_state is not None:
            # 其他提供商的原生状态不能伪装成本平台状态回传。
            raise ValueError("不支持跨提供商原生状态")
        if message.role == "tool":
            call_id = message.tool_call_id
            if call_id not in names:
                raise ValueError("工具结果没有可对应的工具调用")
            if call_id in answered:
                raise ValueError("同一次工具调用收到重复结果")
            answered.add(call_id)
            result.append({"role": "tool", "tool_call_id": call_id, "content": message.content})
            continue
        if set(names) - answered:
            raise ValueError("工具结果尚未配齐，不能追加下一轮消息")
        if message.tool_calls:
            for call in message.tool_calls:
                if call.id in names:
                    raise ValueError("工具调用ID在历史中重复")
                names[call.id] = call.name
            result.append({"role": "assistant", "content": message.content, "tool_calls": [
                {"id": call.id, "type": "function", "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False, allow_nan=False),
                }} for call in message.tool_calls
            ]})
            continue
        result.append({"role": message.role, "content": message.content})
    if set(names) - answered:
        raise ValueError("工具结果尚未配齐")
    return result


def _arguments(value: object) -> dict[str, object]:
    """工具参数必须是对象；兼容被编码成 JSON 字符串的常见返回。"""
    if isinstance(value, dict):
        return load_json(json.dumps(value, ensure_ascii=False, allow_nan=False))
    if isinstance(value, str):
        parsed = load_json(value)
        if not isinstance(parsed, dict):
            raise ValueError("工具参数必须是对象")
        return parsed
    raise ValueError("工具参数必须是结构化对象")


def _call_id(returned: object, used: set[str]) -> str:
    if isinstance(returned, str) and returned and len(returned) <= 128 and returned.isprintable() \
            and not any(character.isspace() for character in returned) and returned not in used:
        return returned
    while True:
        candidate = "qwen-" + uuid4().hex
        if candidate not in used:
            return candidate


def parse_chat_completion(raw: object) -> ModelResponse:
    """choices[0] → ModelResponse；截断、拒答、空响应都不伪装成成功。"""
    if not isinstance(raw, dict):
        raise ValueError("响应必须是对象")
    if raw.get("error") is not None:
        raise ValueError("模型返回错误状态")
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("响应缺少choices")
    choice = choices[0]
    if not isinstance(choice, dict):
        raise ValueError("choice格式错误")
    message = choice.get("message")
    if not isinstance(message, dict):
        raise ValueError("响应消息格式错误")
    content = message.get("content")
    if content is None:
        content = ""
    if not isinstance(content, str):
        raise ValueError("响应正文类型错误")
    reasoning = message.get("reasoning_content")
    if not content.strip() and isinstance(reasoning, str) and reasoning.strip():
        # 思考模型可能只回 reasoning_content；本批把它当正文呈现，兼容性未验证。
        content = reasoning
    finish = choice.get("finish_reason")
    if finish not in {None, "stop", "length", "tool_calls", "content_filter"}:
        return ModelResponse(finish_reason="error")
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
            call_id = _call_id(item.get("id"), used)
            used.add(call_id)
            calls.append(ToolCall(id=call_id, name=name, arguments=_arguments(function.get("arguments"))))
        if finish == "length":
            # 参数可能被截断，禁止执行；正文照返回并标 length。
            return ModelResponse(finish_reason="length", content=content or None)
        return ModelResponse(finish_reason="tool_calls",
                             content=content if content.strip() else None, tool_calls=calls)
    if finish == "length":
        return ModelResponse(finish_reason="length", content=content or None)
    if finish == "content_filter":
        return ModelResponse(finish_reason="refusal", content=content or None)
    if content.strip():
        return ModelResponse(finish_reason="stop", content=content)
    return ModelResponse(finish_reason="error")


class QianwenChatModel:
    """Runtime所需的最小模型接口；每次 generate 只发一次非流式 POST。"""

    _provider = "qianwen.chat"

    def __init__(self, settings: QianwenSettings, *, transport: httpx.BaseTransport | None = None) -> None:
        if not isinstance(settings, QianwenSettings):
            raise TypeError("settings必须是QianwenSettings")
        self._settings = settings
        # trust_env=False：不读取系统代理，避免把请求交给未声明的出口。
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
            "max_tokens": self._settings.max_output_tokens,
        }
        if tools is not None:
            body["tools"] = build_tools(tools)
        return body

    def context_limits(self):
        """声明值优先；显式输出上限已由 Settings 校验不超过声明能力。"""
        from min_agent.context.model_context import ModelContextLimits
        window, max_out = DECLARED_LIMITS[self._settings.model]
        return ModelContextLimits(model=self._settings.model, context_window=window,
                                  max_output_tokens=max_out, source="declared:qianwen-table")

    def generate(
        self, messages: list[Message], *, tools: list[dict[str, object]] | None = None,
    ) -> ModelResponse:
        try:
            body = self._build_body(messages, tools)
            encoded = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
        except (ValueError, TypeError, KeyError, AttributeError):
            raise LLMError("invalid_request") from None
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
            with self._client.stream(
                "POST", CHAT_URL, content=encoded,
                headers={"Authorization": f"Bearer {self._settings.api_key.get_secret_value()}",
                         "Content-Type": "application/json"},
            ) as response:
                if response.status_code != 200:
                    raise _http_error(response, request_number)
                data = bytearray()
                for chunk in response.iter_bytes():
                    if len(data) + len(chunk) > _MAX_RESPONSE_BYTES:
                        raise LLMError("response_too_large")
                    data.extend(chunk)
            raw = load_json(data)
            result = parse_chat_completion(raw)
            usage = raw.get("usage") if isinstance(raw, dict) else None
            record: dict[str, object] = {"request": request_number}
            if isinstance(usage, dict):
                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    value = usage.get(key)
                    if type(value) is int and 0 <= value <= 1_000_000_000:
                        record[key] = value
            if len(record) > 1:
                with self._lock:
                    self._usage.append(record)
            return result
        except httpx.HTTPError:
            raise LLMError("network_error") from None
        except LLMError:
            raise
        except (ValueError, TypeError, KeyError, AttributeError):
            # 只映射固定类别；不输出原始异常、响应正文或模型输出。
            raise LLMError("invalid_response", diagnostics={"protocol_issue": "invalid_shape"}) from None

    def _finish_stream(self, text: str, calls: dict[int, dict], finish: str | None) -> ModelResponse:
        """把流式分片拼成一次完整响应；工具参数分片在此拼齐后再解析。"""
        ordered = [calls[key] for key in sorted(calls)]
        if finish == "length":
            return ModelResponse(finish_reason="length", content=text or None)
        if ordered:
            used: set[str] = set()
            parsed = []
            for slot in ordered:
                name = slot["name"]
                if not isinstance(name, str) or not name.strip() or len(name) > 64:
                    raise ValueError("工具名称无效")
                call_id = _call_id(slot["id"], used)
                used.add(call_id)
                parsed.append(ToolCall(id=call_id, name=name, arguments=load_json(slot["arguments"] or "{}")))
            return ModelResponse(finish_reason="tool_calls",
                                 content=text if text.strip() else None, tool_calls=parsed)
        if finish == "content_filter":
            return ModelResponse(finish_reason="refusal", content=text or None)
        if text.strip():
            return ModelResponse(finish_reason="stop", content=text)
        return ModelResponse(finish_reason="error")

    def generate_stream(self, messages, *, tools=None, on_delta):
        """流式生成：增量正文走 on_delta，返回的仍是完整 ModelResponse。

        工具参数按 index 分片累积，拼齐后才解析；分片非法一律固定错误码，
        不把远端正文或异常文本带回上层。
        """
        if not callable(on_delta):
            raise TypeError("on_delta 必须可调用")
        try:
            body = self._build_body(messages, tools)
            body["stream"] = True
            encoded = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
        except (ValueError, TypeError, KeyError, AttributeError):
            raise LLMError("invalid_request") from None
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
            with self._client.stream(
                "POST", CHAT_URL, content=encoded,
                headers={"Authorization": f"Bearer {self._settings.api_key.get_secret_value()}",
                         "Content-Type": "application/json"},
            ) as response:
                if response.status_code != 200:
                    raise _http_error(response, request_number)
                parts: list[str] = []
                calls: dict[int, dict] = {}
                finish: str | None = None
                usage = None
                size = 0
                for line in response.iter_lines():
                    size += len(line)
                    if size > _MAX_RESPONSE_BYTES:
                        raise LLMError("response_too_large")
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    chunk = load_json(payload)
                    if not isinstance(chunk, dict):
                        raise ValueError("流式分片必须是对象")
                    if chunk.get("error") is not None:
                        raise ValueError("流式分片包含错误")
                    if isinstance(chunk.get("usage"), dict):
                        usage = chunk["usage"]
                    choices = chunk.get("choices")
                    if not isinstance(choices, list) or not choices:
                        continue
                    choice = choices[0] if isinstance(choices[0], dict) else {}
                    delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
                    piece = delta.get("content")
                    if isinstance(piece, str) and piece:
                        parts.append(piece)
                        on_delta(piece)
                    raw_calls = delta.get("tool_calls")
                    if isinstance(raw_calls, list):
                        for item in raw_calls:
                            if not isinstance(item, dict):
                                continue
                            index = item.get("index")
                            index = index if type(index) is int and index >= 0 else len(calls)
                            slot = calls.setdefault(index, {"id": None, "name": None, "arguments": ""})
                            if isinstance(item.get("id"), str) and item["id"]:
                                slot["id"] = item["id"]
                            function = item.get("function") if isinstance(item.get("function"), dict) else {}
                            if isinstance(function.get("name"), str):
                                slot["name"] = function["name"]
                            if isinstance(function.get("arguments"), str):
                                slot["arguments"] += function["arguments"]
                    if isinstance(choice.get("finish_reason"), str):
                        finish = choice["finish_reason"]
            result = self._finish_stream("".join(parts), calls, finish)
            record: dict[str, object] = {"request": request_number}
            if isinstance(usage, dict):
                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    value = usage.get(key)
                    if type(value) is int and 0 <= value <= 1_000_000_000:
                        record[key] = value
            if len(record) > 1:
                with self._lock:
                    self._usage.append(record)
            return result
        except httpx.HTTPError:
            raise LLMError("network_error") from None
        except LLMError:
            raise
        except (ValueError, TypeError, KeyError, AttributeError):
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


class QianwenCatalog:
    """白名单 + 按需实例化；同一模型只保留一个实例，close 统一释放。"""

    def __init__(self, settings_for, *, models: tuple[str, ...] | None = None) -> None:
        if not callable(settings_for):
            raise TypeError("settings_for 必须可调用")
        ids = tuple(models) if models is not None else tuple(DECLARED_LIMITS)
        if not ids or any(model not in DECLARED_LIMITS for model in ids):
            raise ValueError("模型不在声明表")
        if len(set(ids)) != len(ids):
            raise ValueError("模型重复")
        self._settings_for = settings_for
        self.models = ids
        self._instances: dict[str, QianwenChatModel] = {}
        self._lock = RLock()

    @property
    def default_model(self) -> str:
        return self.models[0]

    def validate(self, model: object) -> str:
        if not isinstance(model, str) or model not in self.models:
            raise ValueError("模型不在白名单")
        return model

    def options(self) -> list[dict[str, object]]:
        return [{"id": model, "label": MODEL_LABELS.get(model, model),
                 "context_window": DECLARED_LIMITS[model][0]} for model in self.models]

    def model_for(self, model: str | None = None) -> QianwenChatModel:
        chosen = self.validate(model or self.default_model)
        with self._lock:
            instance = self._instances.get(chosen)
            if instance is None:
                instance = QianwenChatModel(self._settings_for(chosen))
                self._instances[chosen] = instance
            return instance

    def close(self) -> None:
        with self._lock:
            for instance in self._instances.values():
                instance.close()
            self._instances.clear()