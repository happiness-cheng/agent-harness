"""HTTPX传输与Responses协议薄适配；Runtime仍负责工具循环。"""

from threading import RLock
from urllib.parse import urlsplit
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from math import ceil

import httpx

from min_agent.llm.config import OpenAISettings
from min_agent.llm.openai_protocol import build_input, build_tools, load_json, parse_response
from min_agent.models import Message


_PROTOCOL_ISSUES = {
    "DeepSeek不支持OpenAI摘要或加密状态": "unsupported_deepseek_state",
    "文本字段类型错误": "invalid_text_type",
    "原生条目标识无效": "invalid_item_id",
    "完成响应中存在未完成条目": "unfinished_item",
    "原生消息无效": "invalid_message_shape",
    "未支持的消息内容": "unsupported_content",
    "原生推理状态格式错误": "invalid_reasoning_shape",
    "未支持的原生状态内容": "unsupported_reasoning_part",
    "工具调用尚未完成": "unfinished_tool",
    "未支持的远端输出类型": "unsupported_item",
    "未知响应状态": "unknown_status",
    "JSON含重复字段": "invalid_json",
}


class LLMError(RuntimeError):
    """仅携带固定代码和白名单诊断，不附加响应正文或认证请求。"""

    def __init__(self, code: str, *, diagnostics: dict | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.diagnostics = {}
        if not isinstance(diagnostics, dict):
            return
        categories = {
            "api_error_code": {"rate_limit_exceeded", "slow_down", "insufficient_quota", "credit_balance_exhausted",
                               "organization_spend_limit_exceeded", "project_spend_limit_exceeded", "organization_usage_limit_exceeded"},
            "api_error_type": {"rate_limit_error", "insufficient_quota", "invalid_request_error", "server_error", "authentication_error", "permission_error"},
            "protocol_issue": set(_PROTOCOL_ISSUES.values()) | {"missing_field", "invalid_shape"},
        }
        for name, allowed in categories.items():
            value = diagnostics.get(name)
            if isinstance(value, str) and value in allowed:
                self.diagnostics[name] = value
        for name, (minimum, maximum) in {"http_status": (100, 599), "request_count": (1, 100), "retry_after_seconds": (0, 604800)}.items():
            value = diagnostics.get(name)
            if type(value) is int and minimum <= value <= maximum:
                self.diagnostics[name] = value


def _retry_after_seconds(value: str | None) -> int | None:
    if not value or len(value) > 128:
        return None
    value = value.strip()
    try:
        if value.isascii() and value.isdecimal():
            seconds = int(value)
        else:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                return None
            seconds = max(0, ceil((date - datetime.now(UTC)).total_seconds()))
        return seconds if 0 <= seconds <= 604800 else None
    except (ValueError, TypeError, OverflowError):
        return None


def _http_error(response: httpx.Response, request_count: int) -> LLMError:
    details = {"http_status": response.status_code, "request_count": request_count}
    delay = _retry_after_seconds(response.headers.get("Retry-After"))
    if delay is not None:
        details["retry_after_seconds"] = delay
    try:
        data = bytearray()
        for chunk in response.iter_bytes():
            if len(data) + len(chunk) > 64 * 1024:
                break
            data.extend(chunk)
        else:
            raw = load_json(data)
            error = raw.get("error") if isinstance(raw, dict) else None
            if isinstance(error, dict):
                details.update(api_error_code=error.get("code"), api_error_type=error.get("type"))
    except (httpx.HTTPError, ValueError, TypeError, RecursionError):
        # 诊断读取失败不能覆盖已收到的HTTP状态；不保留原始异常。
        pass
    return LLMError(f"http_{response.status_code}", diagnostics=details)


class OpenAIResponsesModel:
    _provider = "openai.responses"
    _endpoint = "https://api.openai.com/v1/responses"
    _model_id = "gpt-6.1-sol"

    def __init__(self, settings: OpenAISettings, *, transport: httpx.BaseTransport | None = None, proxy: str | None = None) -> None:
        if settings.model != self._model_id:
            raise LLMError("model_provider_mismatch")
        if proxy is not None:
            try:
                if not isinstance(proxy, str) or not proxy or not proxy.isascii() or any(character.isspace() for character in proxy) or not proxy.isprintable():
                    raise ValueError("代理格式无效")
                target = urlsplit(proxy)
                if (target.scheme != "http" or target.hostname not in {"127.0.0.1", "localhost", "::1"}
                    or target.username is not None or target.password is not None
                    or target.port is None or not 1 <= target.port <= 65535
                    or target.path not in {"", "/"} or target.query or target.fragment):
                    raise ValueError("代理不在已支持范围")
            except (ValueError, TypeError):
                raise LLMError("invalid_proxy") from None
            # HTTPX的代理路由可能优先于注入传输，禁止组合以免离线测试意外联网。
            if transport is not None:
                raise LLMError("invalid_transport_configuration")
        self._settings = settings
        key = settings.api_key.get_secret_value()
        if not key or not key.isascii() or not key.isprintable() or any(character.isspace() for character in key):
            raise LLMError("invalid_api_key")
        self._client = httpx.Client(
            transport=transport, timeout=settings.timeout_seconds,
            follow_redirects=False, trust_env=False, verify=True, proxy=proxy,
        )
        self._lock = RLock()
        self._requests = 0
        self._usage = []

    def _build_body(self, messages, tools):
        return {
            "model": self._settings.model, "input": build_input(messages, model=self._settings.model, provider=self._provider),
            "tools": build_tools(tools), "parallel_tool_calls": False,
            "include": ["reasoning.encrypted_content"],
            "reasoning": {"effort": self._settings.reasoning_effort, "summary": "auto"},
            "store": False, "stream": False,
            "max_output_tokens": self._settings.max_output_tokens,
        }

    def generate(self, messages: list[Message], *, tools: list[dict[str, object]] | None = None):
        try:
            body = self._build_body(messages, tools)
        except (ValueError, TypeError, KeyError, AttributeError):
            raise LLMError("invalid_request") from None
        with self._lock:
            if self._client.is_closed:
                raise LLMError("client_closed")
            if self._requests >= self._settings.max_requests:
                raise LLMError("request_limit")
            self._requests += 1
        try:
            with self._client.stream(
                "POST", self._endpoint, json=body,
                headers={"Authorization": f"Bearer {self._settings.api_key.get_secret_value()}"},
            ) as response:
                if response.status_code != 200:
                    raise _http_error(response, self.request_count)
                data = bytearray()
                for chunk in response.iter_bytes():
                    if len(data) + len(chunk) > 4 * 1024 * 1024:
                        raise LLMError("response_too_large")
                    data.extend(chunk)
            raw = load_json(data)
            result = parse_response(raw, model=self._settings.model, provider=self._provider)
            usage = raw.get("usage", {})
            if isinstance(usage, dict):
                record = {key: value for key, value in usage.items()
                          if key in {"input_tokens", "output_tokens", "total_tokens"}
                          and type(value) is int and 0 <= value <= 1_000_000_000}
                if record:
                    with self._lock: self._usage.append({"request": self._requests, **record})
            return result
        except httpx.HTTPError:
            raise LLMError("network_error") from None
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            # 只映射本地固定类别，不输出原始异常、远端内容或字段值。
            issue = "missing_field" if isinstance(error, KeyError) else _PROTOCOL_ISSUES.get(str(error), "invalid_shape")
            raise LLMError("invalid_response", diagnostics={"protocol_issue": issue}) from None

    def close(self) -> None:
        self._client.close()

    @property
    def request_count(self) -> int:
        with self._lock:
            return self._requests

    @property
    def usage(self):
        with self._lock: return [dict(record) for record in self._usage]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
