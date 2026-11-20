"""模型可见消息的组装：系统提示词 + 历史副本 + 可刷新状态尾部。"""

import json
from collections.abc import Callable

from min_agent.contracts.clock import Clock
from min_agent.context.manager import ContextLimit
from min_agent.contracts.models import Message
from min_agent.context.state_bar import RuntimeContextMessage


class PromptView:
    """把运行历史投影成一次模型请求；调用方只拿到不可复用的副本。"""

    def __init__(self, tools, budget, system_prompt: str | Callable[[], str] | None) -> None:
        self._tools = tools
        self._budget = budget
        self._system_prompt = system_prompt

    def _render_system_prompt(self) -> str:
        value = self._system_prompt
        return value() if callable(value) else value

    def __call__(self, messages: list[Message]) -> list[Message]:
        result = [message.model_copy(deep=True) for message in messages]
        if self._system_prompt is None:
            return result

        def payload():
            try:
                state = self._tools.work_state()
                status = self._tools.state_bar(messages, self._budget)
                mode = status.get("execution", {}).get("mode", state.mode) if status is not None else state.mode
                view = {"application_state": {"mode": mode, "current_date": Clock().today().isoformat()}}
                if status is not None:
                    view["agent_status"] = status
                memory_view = self._tools.memory_view(messages)
                if memory_view:
                    view['untrusted_user_memory'] = memory_view
                return json.dumps(view, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            except Exception as error:
                raise ContextLimit("当前状态投影不可用，停止本次模型请求") from error

        tail = RuntimeContextMessage(role="user", content=payload())
        tail._refresh = payload
        return [Message(role="system", content=self._render_system_prompt())] + result + [tail]
