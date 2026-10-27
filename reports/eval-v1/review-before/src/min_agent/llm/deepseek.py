"""DeepSeek官方Responses适配器；仅复用传输，不混用提供商凭据/状态。"""

from typing import ClassVar, Literal

from min_agent.llm.client import OpenAIResponsesModel
from min_agent.llm.config import OpenAISettings
from min_agent.llm.openai_protocol import build_input, build_tools


class DeepSeekSettings(OpenAISettings):
    _credential_key: ClassVar[str] = "deepseek_api_key"
    _provider_name: ClassVar[str] = "DeepSeek"
    model: Literal["deepseek-flash"] = "deepseek-flash"


class DeepSeekResponsesModel(OpenAIResponsesModel):
    _provider = "deepseek.responses"
    _endpoint = "https://api.deepseek.com/responses"
    _model_id = "deepseek-flash"

    def context_limits(self):
        """只读发现并按客户端缓存；不发送对话、不占生成请求次数。"""
        from min_agent.model_context import ModelContextLimits
        from min_agent.llm.openai_protocol import load_json
        import httpx
        with self._lock:
            cached = getattr(self, '_context_limits_cache', None)
            if cached is not None:
                return cached
            try:
                with self._client.stream('GET', 'https://api.deepseek.com/models',
                        headers={'Authorization': f'Bearer {self._settings.api_key.get_secret_value()}'}) as response:
                    if response.status_code != 200:
                        raise ValueError('metadata_http_error')
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        if len(data) + len(chunk) > 256 * 1024:
                            raise ValueError('metadata_too_large')
                        data.extend(chunk)
                raw = load_json(data)
                rows = [row for row in raw['data'] if row['id'] == self._settings.model]
                if len(rows) != 1:
                    raise ValueError('metadata_model_missing_or_ambiguous')
                row = rows[0]
                limits = ModelContextLimits(model=self._settings.model, context_window=row['context_window'],
                    max_output_tokens=row['max_output_tokens'], source='https://api.deepseek.com/models')
            except (httpx.HTTPError, ValueError, TypeError, KeyError, RuntimeError):
                # 不回显服务端正文或认证数据，不回退到另一个固定窗口。
                raise ValueError('无法读取当前DeepSeek模型的上下文能力；未采用猜测窗口') from None
            self._context_limits_cache = limits
            return limits

    def _build_body(self, messages, tools):
        # 不发送DeepSeek不支持或静默忽略的OpenAI专有字段。
        return {
            "model": self._settings.model,
            "input": build_input(messages, model=self._settings.model, provider=self._provider),
            "tools": build_tools(tools),
            "reasoning": {"effort": self._settings.reasoning_effort},
            "stream": False,
            "max_output_tokens": self._settings.max_output_tokens,
        }
