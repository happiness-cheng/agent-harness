"""真实模型层：OpenAI 兼容的模型适配器（Groq / Gemini）。

仓库自带的模型只有 Ollama / 千问 / DeepSeek，没有 Gemini / Groq。
这两个都是 OpenAI 兼容接口，所以这里只写一个通用适配器，
不改 src/ 里任何一行。

- Groq：https://api.groq.com/openai/v1/chat/completions
  免费档主力 openai/gpt-oss-120b（每分钟 30 次、每天 1000 次、每天 20 万 tokens，
  按账号算；以 Groq 控制台显示为准）。
- Gemini：https://generativelanguage.googleapis.com/v1beta/openai/
  免费档按项目算，不同模型每天次数不同（Flash-Lite 约 500 次，Flash 约 20 次，
  以 AI Studio 显示为准）。Gemini 题量大时先跑 Groq，Gemini 只跑子集。
  注意：gemini-2.5-flash 已不对新用户开放（2026-11-23 实测 404），
  默认改用 gemini-3-flash-preview；gemini-3.8-flash 同日实测 503（高峰期
  临时不可用），不是 key 的问题。

Key 怎么来：绝不贴聊天里。走 Secure Vault 的 API 接入卡片，
存在连接器存储里；本模块只从调用方传进来的字符串拿 key，
不读环境变量、不读文件、不打印 key。
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from min_agent.context.model_context import ModelContextLimits
from min_agent.contracts.models import Message, ModelResponse, ToolCall

_PROVIDERS = {
    # base_url, 默认模型, 上下文窗口（token）
    "groq": ("https://api.groq.com/openai/v1", "openai/gpt-oss-120b", 131072),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "gemini-3-flash-preview", 1048576),
    # 千问（EVAL-DIAG-001）：DashScope 的 OpenAI 兼容端点，默认模型与仓库
    # qianwen.py 的默认一致（qwen3.8-flash，窗口 1,048,576）。key 由用户
    # 在千问平台开（platform.qianwenai.com），走 Secure Vault 卡片进来。
    "qianwen": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen3.8-flash", 1048576),
}

_TOOL_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}$")


class _Settings:
    """只给 output_reserve() 看：它读 model._settings.max_output_tokens。"""

    def __init__(self, max_output_tokens: int = 1024) -> None:
        self.max_output_tokens = max_output_tokens


class OpenAICompatibleModel:
    """Runtime 要的最小模型接口：generate(messages, tools=...) -> ModelResponse。"""

    def __init__(self, *, provider: str, api_key: str | None = None, model: str | None = None,
                 temperature: float = 0.0, max_output_tokens: int = 1024,
                 timeout_seconds: float = 180.0, max_requests: int = 500,
                 connector_helper: str | None = None) -> None:
        if provider not in _PROVIDERS:
            raise ValueError(f"未支持的 provider：{provider}，可选 {sorted(_PROVIDERS)}")
        # api_key 为空时走连接器：key 不进本进程，由 helper 里的 surrogate
        # 在 authd 换成真 key。只支持 groq（已接通 custom.groq）。
        self._connector_helper = connector_helper
        if api_key is None and connector_helper is None:
            raise ValueError("需要 API key（由调用方从 Secure Vault 传入，不贴聊天）"
                             "或 connector_helper（走已接通的连接器）")
        base_url, default_model, context_window = _PROVIDERS[provider]
        self._provider = provider
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.model = model or default_model
        self._context_window = context_window
        self._settings = _Settings(max_output_tokens)
        self._temperature = temperature
        self._timeout = timeout_seconds
        self._max_requests = max_requests
        self._requests = 0
        self.usage: list[dict] = []
        self.last_error: str | None = None  # 最近一次 generate() 失败的原因，供 Runner 写进报告
        self.retries = 0  # 瞬时失败重试的次数，每次都记下来，不静默
        self._client = httpx.Client(timeout=timeout_seconds, follow_redirects=False, trust_env=False)

    def describe(self) -> dict:
        """报告头用：实际跑的模型名、温度、max_tokens（EVAL-DIAG-002：
        上轮报告没写模型名，Claude 审批第 1 点要求补上）。"""
        return {"model": self.model, "temperature": self._temperature,
                "max_tokens": self._settings.max_output_tokens}

    def context_limits(self) -> ModelContextLimits:
        return ModelContextLimits(
            model=self.model, context_window=self._context_window,
            max_output_tokens=self._settings.max_output_tokens,
            source=f"{self._provider}:context_window={self._context_window}",
        )

    @staticmethod
    def _to_openai(messages: list[Message]) -> list[dict]:
        out: list[dict] = []
        for message in messages:
            if message.role == "tool":
                out.append({"role": "tool", "tool_call_id": message.tool_call_id,
                            "content": message.content})
            elif message.tool_calls:
                out.append({"role": "assistant", "content": message.content or None,
                            "tool_calls": [{
                                "id": call.id, "type": "function",
                                "function": {"name": call.name,
                                             "arguments": json.dumps(call.arguments, ensure_ascii=False)},
                            } for call in message.tool_calls]})
            else:
                out.append({"role": message.role, "content": message.content})
        return out

    @staticmethod
    def _to_tools(tools: list[dict] | None) -> list[dict]:
        if not tools:
            return []
        result = []
        for tool in tools:
            if not _TOOL_NAME.fullmatch(tool["name"]):
                raise ValueError(f"工具名无效：{tool['name']}")
            result.append({"type": "function", "function": {
                "name": tool["name"], "description": tool["description"],
                "parameters": tool["parameters"]}})
        return result

    def generate(self, messages: list[Message], *, tools: list[dict] | None = None) -> ModelResponse:
        if self._requests >= self._max_requests:
            raise RuntimeError(f"本轮模型请求已到上限 {self._max_requests}，停下不继续烧额度")
        self._requests += 1
        body = {
            "model": self.model,
            "messages": self._to_openai(messages),
            "temperature": self._temperature,
            "max_tokens": self._settings.max_output_tokens,
            "stream": False,
        }
        built_tools = self._to_tools(tools)
        if built_tools:
            body["tools"] = built_tools
        started = time.perf_counter()
        if self._api_key is None:
            # 瞬时失败（helper/网络/Cloudflare 抖动）最多重试 3 次，退避 5/15/30 秒；
            # 429 限流不重试。旧策略只重试 1 次，导致连接器抖动直接把运行打断，
            # 看起来像“模型只看不做”（GATE-MIN-001 诊断，见 VALIDATION_LOG）。
            # 重试次数记在 self.retries 里，写进报告。
            raw = None
            for attempt in range(4):
                try:
                    raw = self._call_via_connector(body)
                    break
                except RuntimeError as error:
                    if "429" in str(error) or "限流" in str(error):
                        self.last_error = str(error)
                        raise
                    self.retries += 1
                    self.last_error = f"第 {attempt + 1} 次调用失败后重试：{str(error)[:800]}"
                    if attempt == 3:
                        raise
                    time.sleep((5, 15, 30)[attempt])
        else:
            try:
                response = self._client.post(
                    f"{self._base_url}/chat/completions", json=body,
                    headers={"Authorization": f"Bearer {self._api_key}",
                             "Content-Type": "application/json"})
            except httpx.HTTPError as error:
                self.last_error = f"模型请求网络失败：{type(error).__name__}"
                raise RuntimeError(self.last_error) from error
            if response.status_code == 429:
                self.last_error = "模型返回 429（限流）：停下，不重试、不换接口"
                raise RuntimeError(self.last_error)
            if response.status_code != 200:
                self.last_error = f"模型返回 HTTP {response.status_code}：{response.text[:200]}"
                raise RuntimeError(self.last_error)
            raw = response.json()
        usage = raw.get("usage") or {}
        self.usage.append({"request": self._requests,
                           "prompt_tokens": usage.get("prompt_tokens"),
                           "completion_tokens": usage.get("completion_tokens"),
                           "elapsed_ms": int((time.perf_counter() - started) * 1000)})
        choice = (raw.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        raw_calls = message.get("tool_calls") or []
        calls = []
        for item in raw_calls:
            function = item.get("function") or {}
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except ValueError:
                arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            calls.append(ToolCall(id=item.get("id") or f"call-{self._requests}-{len(calls)}",
                                  name=function.get("name") or "",
                                  arguments=arguments))
        finish = choice.get("finish_reason")
        if calls:
            return ModelResponse(finish_reason="tool_calls",
                                 content=content if content.strip() else None, tool_calls=calls)
        if finish == "length":
            return ModelResponse(finish_reason="length", content=content or None)
        if content.strip():
            return ModelResponse(finish_reason="stop", content=content)
        return ModelResponse(finish_reason="error")

    def _call_via_connector(self, body: dict) -> dict:
        """把请求交给连接器 helper（groq-chat.py），key 只在 authd 里换。

        helper 在 stdout 吐 provider 的原始 JSON；出错时 stderr 里只有
        HTTP 状态，不含 key。helper 退出码非 0 就停下，不重试。
        """
        if self._connector_helper is None:
            raise RuntimeError("连接器模式需要 connector_helper")
        helper = Path(self._connector_helper).expanduser()
        if not helper.exists():
            raise RuntimeError(f"连接器 helper 不存在：{helper}")
        try:
            proc = subprocess.run(
                ["python3", str(helper), "chat"], input=json.dumps(body),
                capture_output=True, text=True, timeout=self._timeout)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(f"连接器 helper 超时 {self._timeout} 秒") from error
        if proc.returncode != 0:
            # 取 stderr 的尾巴（前 500 字是 traceback 头，真正的 HTTP 状态在尾巴）；
            # 429 要显式标出来，不然上层的“429 不重试”判断看不到（EVAL-DIAG-001：
            # Gemini 429 被截掉后重试了 3 次，白白烧掉 50 秒才失败）。
            err = proc.stderr.strip()
            tail = err[-500:]
            prefix = "429 限流：" if "429" in err else ""
            raise RuntimeError(f"连接器 helper 失败：{prefix}{tail}")
        try:
            return json.loads(proc.stdout)
        except ValueError as error:
            raise RuntimeError(f"连接器 helper 返回的不是 JSON：{proc.stdout[:200]}") from error

    @property
    def request_count(self) -> int:
        return self._requests

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        self.close()
