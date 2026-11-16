"""执行引擎的数据契约与本次运行的可变簿记；不调用模型、不执行工具。"""

from dataclasses import dataclass, field
from typing import Literal, Protocol
from uuid import UUID

from min_agent.ledger.checkpoints import Checkpoint
from min_agent.contracts.models import Message, ModelResponse
from min_agent.contracts.trace import TraceContext


class Model(Protocol):
    """Runtime 所需的最小模型接口，不绑定服务商。"""

    def generate(
        self, messages: list[Message], *, tools: list[dict[str, object]] | None = None
    ) -> ModelResponse: ...


@dataclass(frozen=True)
class RunResult:
    """本次运行状态；completed 表示模型正常回答，不证明业务目标。"""

    status: Literal["completed", "waiting_user", "partial", "failed", "cancelled"]
    response: ModelResponse | None = None
    error_code: str | None = None
    run_id: UUID | None = None
    checkpoint: Checkpoint | None = None


@dataclass(frozen=True)
class ModelRound:
    """一次已校验的模型响应，以及后续工具 span 需要挂靠的父调用 ID。"""

    response: ModelResponse
    span_call_id: UUID


@dataclass(frozen=True)
class ToolRequest:
    """模型本轮提出的工具请求；按顺序交给工具阶段。"""

    response: ModelResponse
    span_call_id: UUID


@dataclass
class RunState:
    """一次运行内的可变簿记；只在单次 ``_run`` 内流转，不跨运行复用。"""

    context: TraceContext
    run_id: UUID
    parent_call_id: UUID
    messages: list[Message]
    seen_call_ids: set[str] = field(default_factory=set)
    error_rounds: int = 0
    had_request_errors: bool = False
    progress_correction: bool = False
    round: ModelRound | None = None
    request: ToolRequest | None = None
