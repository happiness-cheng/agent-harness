"""按预设脚本响应，记录传入消息；不解析用户意图。"""

from collections import deque
from collections.abc import Iterable
from copy import deepcopy

from min_agent.models import Message, ModelResponse


class ScriptedModel:
    def __init__(self, script: Iterable[ModelResponse | BaseException]) -> None:
        self._script = deque(script)
        self.calls: list[list[Message]] = []
        self.tool_schemas: list[list[dict[str, object]]] = []

    def generate(
        self, messages: list[Message], *, tools: list[dict[str, object]] | None = None
    ) -> ModelResponse:
        self.calls.append([message.model_copy(deep=True) for message in messages])
        self.tool_schemas.append(deepcopy(tools) if tools is not None else [])
        if not self._script:
            raise AssertionError("脚本响应已用尽")
        item = self._script.popleft()
        if isinstance(item, BaseException):
            raise item
        return item
