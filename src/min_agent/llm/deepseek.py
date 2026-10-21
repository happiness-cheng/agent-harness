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
