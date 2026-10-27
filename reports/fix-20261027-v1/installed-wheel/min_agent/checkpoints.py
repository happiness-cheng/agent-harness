"""软阈值策略与本次运行返回的检查点；不授予继续执行或业务写入权限。"""

from typing import Annotated, Literal
from hashlib import sha256
import json
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from min_agent.budget import BudgetUsage


class CheckpointPolicy(BaseModel):
    """以剩余次数预留收尾空间；不是固定百分比或独占的并发配额。"""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    reserve_model_calls: int = Field(default=1, ge=1)
    reserve_tool_calls: int = Field(default=0, ge=0)


_Text = Annotated[str, Field(min_length=1, max_length=1000, pattern=r"\S")]
_Section = Annotated[tuple[_Text, ...], Field(max_length=20)]


class CheckpointNote(BaseModel):
    """模型整理的未核实文字；不能用它替代业务事实或授权状态。"""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    achievements: _Section
    pending: _Section
    blockers: _Section
    next_steps: _Section


class ToolResultRef(BaseModel):
    """当前完整消息列表中的结果引用；不复制业务正文或伪造成功。"""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    message_index: int = Field(ge=0)
    call_id: str
    ok: bool | None
    execution_state: Literal["returned", "not_started", "unknown"]


class Checkpoint(BaseModel):
    """Runtime返回的局部检查点，持久化/定向继续另由可信入口实现。"""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    checkpoint_id: UUID = Field(default_factory=uuid4)
    segment_id: UUID
    budget_id: UUID | None = None
    session_id: UUID | None = None
    reason: Literal["model_reserve", "tool_reserve", "hard_budget", "context_limit", "no_progress", "user_stopped", "interrupted", "failed"]
    closing_status: Literal["returned", "not_started", "failed", "invalid_response"]
    closing_error: Literal["budget_exhausted", "budget_persistence_failed", "model_call_failed", "checkpoint_invalid_response", "context_limit", "no_progress", "user_stopped", "interrupted"] | None = None
    model_note: CheckpointNote | None = None
    tool_results: tuple[ToolResultRef, ...] = ()
    history_length: int = Field(ge=0)
    task_usage: BudgetUsage
    segment_usage: BudgetUsage


class CheckpointRecord(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    checkpoint: Checkpoint
    owner_id: UUID
    goal_id: UUID | None
    history_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    known_segments: tuple[UUID, ...]
    resumed_by: UUID | None = None


def history_hash(messages):
    data = [message.model_dump(mode="json") for message in messages]
    return sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


CLOSING_INSTRUCTION = """本执行片段已到软阈值，正在使用预留的一次调用整理检查点。
评估目前是否已达到目标；不能因预算不足仓促宣称完成，不执行或请求任何工具。
只返回JSON对象，必须含achievements、pending、blockers、next_steps四个字符串数组，
每组最多20项，每项1到1000字符。记录已有成果、未完成项、阻塞和建议下一步；
不确定的结果必须写明不确定。此文字只是未核实的整理，不改变业务事实或权限。
用户确认继续后才能另分配新片段；不能要求扩大任务累计上限或重复已成功的写入。"""
