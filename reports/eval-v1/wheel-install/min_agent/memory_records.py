"""确定性生成逐轮事件和原文引用，不从自然语言推断业务事实。"""

import json
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator

from min_agent.checkpoints import history_hash


class SourceReference(BaseModel):
    """引用只是读取线索，既不证明摘要正确，也不授予写权限。"""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    archive_id: UUID = Field(strict=False)
    session_id: UUID = Field(strict=False)
    message_index: int = Field(ge=0)
    message_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    start: int = Field(ge=0)
    end: int = Field(ge=0)

    @model_validator(mode="after")
    def check_range(self):
        if self.end < self.start:
            raise ValueError("引用结束位置不能早于起点")
        return self


def source_text(message):
    """工具请求同时保留参数；不把provider内部状态展示为原文正文。"""
    if message.tool_calls:
        return json.dumps({"content": message.content,
            "tool_calls": [call.model_dump(mode="json") for call in message.tool_calls]},
            ensure_ascii=False, sort_keys=True)
    return message.content


def runtime_state(snapshot):
    """记录归档当时的控制状态；不从助手文字重建目标或授权。"""
    return {"work_state": snapshot.work_state.model_dump(mode="json"),
            "task": snapshot.task.model_dump(mode="json") if snapshot.task else None}


def interaction_events(snapshot, archive_id, messages, start):
    events, requests = [], {}
    for index, message in enumerate(messages, start):
        text = source_text(message)
        source = SourceReference(archive_id=UUID(archive_id), session_id=snapshot.session_id,
            message_index=index, message_hash=history_hash([message]), start=0, end=len(text))

        def append(kind, **details):
            events.append({"id": str(uuid5(UUID(archive_id), f"event:{len(events)}")),
                "kind": kind, "source": source.model_dump(mode="json"), **details})

        if message.role == "user":
            append("user_request", text=message.content[:1000],
                   truncated=len(message.content) > 1000, verified=False)
        elif message.role == "assistant":
            if message.content:
                append("assistant_statement", text=message.content[:1000],
                       truncated=len(message.content) > 1000, verified=False)
            for call in message.tool_calls:
                requests[call.id] = call
                append("tool_requested", call_id=call.id, tool_name=call.name,
                       arguments=call.arguments)
        elif message.role == "tool":
            try:
                outcome = json.loads(message.content)
            except (ValueError, TypeError):
                outcome = None
            ok = outcome.get("ok") if isinstance(outcome, dict) else None
            ok = ok if type(ok) is bool else None
            error = outcome.get("error") if isinstance(outcome, dict) else None
            execution = "returned" if ok is True else (
                error.get("execution_state", "unknown") if isinstance(error, dict) else "unknown")
            if execution not in {"returned", "not_started", "unknown"}:
                execution = "unknown"
            request = requests.get(message.tool_call_id)
            append("tool_result", call_id=message.tool_call_id,
                   tool_name=request.name if request else None, ok=ok,
                   execution_state=execution)
    return events
