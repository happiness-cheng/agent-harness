"""MA-P5-013：点对点 Agent 消息（**只做投递，不做总线**）。

设计定位（DECISION_LOG → MA-P5-013 D1–D3）：

* **点对点，非总线**：只有 ``send`` 与 ``reader``，没有订阅发布、没有广播、
  没有持久队列。``to`` 必须是一个已登记的对端，``"*"`` 之类广播地址一律拒绝。
* **与任务内容分层**：任务的「做什么」走 :class:`min_agent.agents.manager.TaskEnvelope`
  （目标、约束、上游产物、工具结果）；本模块只传「谁知道什么」——派发、状态、
  结果、终止四类**通知投影**。``correlation_id`` 指回原 ``envelope_id``，让消息
  可回溯到任务，但消息本身不承载任务正文。
* **状态事实源仍是 ``LifecycleRegistry``**：``status_update`` 只是它的一条消息
  投影，**不能授权、不能完成任务、不能覆盖 verifier**。谁想改状态必须走
  ``WorkerHandle`` / ``TaskStore`` 的真实接口。
* **有界**：每个收件人一个定长信箱，写满**明确失败**（:class:`MailboxFull`），
  不无限占内存；信封 ID 全局去重，重复投递失败。
* **载荷白名单**：每种消息类型只允许固定字段名，且值只允许短字符串/整数。
  这样「往消息里塞任务正文、模型输出、工具正文、凭据、证据原文」在构造期就
  会被拒绝，而不是靠事后审计发现。

明确不做（不得当作已实现）：

* 不做消息重放：``read()`` 取走即移除，不提供历史回看；
* 不做跨进程/跨会话投递，不落盘；
* 不保证投递顺序与线程调度顺序一致——同一收件人的消息按 ``send`` 的加锁顺序
  FIFO 返回，但两个线程谁先拿到锁不确定；
* 不做「消息已读」回执，也不做投递失败重试。
"""

from __future__ import annotations

import json
import threading
from collections import deque
from typing import Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

__all__ = [
    "MessageType", "MESSAGE_TYPES", "STATUS_STATES", "PAYLOAD_SCHEMA",
    "AgentEnvelope", "StatusPayload", "MailboxReader", "PointToPointMailbox",
    "MailboxError", "MailboxViolation", "MailboxFull", "status_payload",
]

MessageType = Literal["task_assigned", "status_update", "result", "terminate"]
MESSAGE_TYPES: tuple[str, ...] = (
    "task_assigned", "status_update", "result", "terminate",
)

STATUS_STATES: tuple[str, ...] = (
    "pending", "running", "waiting_input",
    "completed", "failed", "cancelled", "expired",
)

# 每种消息类型：允许的字段名 → 必填字段名。**没有**承载正文的通用字段。
PAYLOAD_SCHEMA: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "task_assigned": (
        frozenset({"subtask_id", "worker_id", "role", "profile"}),
        frozenset({"subtask_id", "worker_id", "role", "profile"}),
    ),
    "status_update": (
        frozenset({"worker_id", "subtask_id", "role", "state", "step", "note"}),
        frozenset({"worker_id", "subtask_id", "role", "state", "step"}),
    ),
    "result": (
        frozenset({"subtask_id", "status", "verification", "error"}),
        frozenset({"subtask_id", "status", "verification"}),
    ),
    "terminate": (
        frozenset({"subtask_id", "reason"}),
        frozenset({"subtask_id", "reason"}),
    ),
}

_MAX_FIELD_TEXT = 512
"""单个载荷字段的最大长度；短标识/状态码够用，正文放不进来。"""


class MailboxError(RuntimeError):
    """Mailbox 基础错误。"""


class MailboxViolation(MailboxError):
    """投递违反边界：未知对端、自投递、载荷非法、重复信封。"""


class MailboxFull(MailboxError):
    """收件人信箱已满；**明确失败**，不丢弃旧消息。"""


class _Strict(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


def _check_payload(kind: str, payload: dict[str, JsonValue]) -> None:
    """按消息类型校验载荷字段名与值形状。"""
    allowed, required = PAYLOAD_SCHEMA[kind]
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"payload_field_not_allowed:{','.join(unknown)}")
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"payload_field_required:{','.join(missing)}")
    for name, value in payload.items():
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise ValueError(f"payload_value_type:{name}")
        if isinstance(value, str) and len(value) > _MAX_FIELD_TEXT:
            raise ValueError(f"payload_value_too_long:{name}")


class AgentEnvelope(_Strict):
    """一条点对点消息。

    ``payload`` 的类型限定在 :data:`PAYLOAD_SCHEMA`——消息**不是**第二个任务信封。
    """

    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", populate_by_name=True,
    )

    envelope_id: str = Field(min_length=1, max_length=64, pattern=r"^\S(?:.*\S)?$")
    kind: Literal["agent_message"] = "agent_message"
    from_: str = Field(alias="from", min_length=1, max_length=64, pattern=r"^\S(?:.*\S)?$")
    to: str = Field(min_length=1, max_length=64, pattern=r"^\S(?:.*\S)?$")
    type: MessageType
    correlation_id: str = Field(min_length=1, max_length=64, pattern=r"^\S(?:.*\S)?$")
    payload: dict[str, JsonValue]

    @model_validator(mode="after")
    def _validate(self) -> "AgentEnvelope":
        if self.from_ == "*" or self.to == "*":
            raise ValueError("broadcast_not_supported")
        if self.from_ == self.to:
            raise ValueError("self_message_not_supported")
        _check_payload(self.type, self.payload)
        return self

    def wire(self) -> dict[str, object]:
        """按线上字形（``from``）导出；不含任何隐藏内容。"""
        return self.model_dump(by_alias=True)

    def encoded_size(self) -> int:
        """载荷 JSON 字节数；用于有界检查，不写盘。"""
        return len(json.dumps(
            self.payload, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode("utf-8"))


class StatusPayload(_Strict):
    """``status_update`` 的结构化视图，字段与 progress 白名单一致。

    **不含**任务正文、模型输出、工具正文、凭据、证据原文——这些字段根本没有
    位置可放。它只是 :class:`StatusPayload` 的投影，不是授权来源。
    """

    worker_id: str = Field(min_length=1, max_length=64)
    subtask_id: str = Field(min_length=1, max_length=64)
    role: str = Field(min_length=1, max_length=64)
    state: str = Field(min_length=1, max_length=32)
    step: str = Field(min_length=1, max_length=64)
    note: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def _known_state(self) -> "StatusPayload":
        if self.state not in STATUS_STATES:
            raise ValueError(f"unknown_state:{self.state}")
        return self

    def as_payload(self) -> dict[str, JsonValue]:
        return self.model_dump()


def status_payload(*, worker_id: str, subtask_id: str, role: str,
                   state: str, step: str, note: str = "") -> dict[str, JsonValue]:
    """构造并校验一条 ``status_update`` 载荷；非法状态直接拒绝。"""
    return StatusPayload(
        worker_id=worker_id, subtask_id=subtask_id, role=role,
        state=state, step=step, note=note,
    ).as_payload()


class MailboxReader:
    """**一个收件人**的只读投递口；不能读别人的信箱，也没有广播入口。"""

    def __init__(self, mailbox: "PointToPointMailbox", recipient: str) -> None:
        self._mailbox = mailbox
        self._recipient = recipient

    @property
    def recipient(self) -> str:
        return self._recipient

    def read(self, *, types: tuple[MessageType, ...] | None = None
             ) -> tuple[AgentEnvelope, ...]:
        """取走并移除本收件人的消息（FIFO）；可选按类型过滤。"""
        return self._mailbox._take(self._recipient, types)

    def pending(self) -> int:
        """未读消息数；只读计数，不暴露内容。"""
        return self._mailbox._pending(self._recipient)


class PointToPointMailbox:
    """有界的点对点信箱集合：一个参与者一个信箱，互不串读。

    参数
    ----
    participants:
        允许参与投递的**具名**端点；``"*"`` 与重复名一律拒绝。
    max_per_recipient:
        单个信箱的容量上限；写满抛 :class:`MailboxFull`，不静默丢弃。
    max_payload_bytes:
        单条消息载荷的字节上限；超限抛 :class:`MailboxViolation`。
    """

    def __init__(self, *, participants: Iterable[str],
                 max_per_recipient: int = 64,
                 max_payload_bytes: int = 32768) -> None:
        names = tuple(participants)
        if not names:
            raise ValueError("至少需要一个参与者")
        cleaned: list[str] = []
        for name in names:
            if not isinstance(name, str) or not name.strip():
                raise ValueError("参与者名必须是非空白字符串")
            if name == "*":
                raise ValueError("不支持广播参与者")
            if len(name) > 64:
                raise ValueError("参与者名过长")
            if name in cleaned:
                raise ValueError(f"参与者重复:{name}")
            cleaned.append(name)
        if isinstance(max_per_recipient, bool) or not isinstance(max_per_recipient, int) \
                or not 1 <= max_per_recipient <= 4096:
            raise ValueError("max_per_recipient 必须在 1..4096 之间")
        if isinstance(max_payload_bytes, bool) or not isinstance(max_payload_bytes, int) \
                or not 256 <= max_payload_bytes <= 1_048_576:
            raise ValueError("max_payload_bytes 必须在 256..1048576 之间")
        self._participants = tuple(cleaned)
        self._max_per_recipient = max_per_recipient
        self._max_payload_bytes = max_payload_bytes
        self._boxes: dict[str, deque[AgentEnvelope]] = {
            name: deque() for name in self._participants
        }
        self._seen: set[str] = set()
        self._lock = threading.RLock()

    @property
    def participants(self) -> tuple[str, ...]:
        return self._participants

    @property
    def max_per_recipient(self) -> int:
        return self._max_per_recipient

    @property
    def max_payload_bytes(self) -> int:
        return self._max_payload_bytes

    def reader(self, recipient: str) -> MailboxReader:
        with self._lock:
            if recipient not in self._boxes:
                raise MailboxViolation(f"unknown_recipient:{recipient}")
        return MailboxReader(self, recipient)

    def send(self, envelope: AgentEnvelope) -> None:
        """投递一条消息；越权、超限、重复、信箱满都在这里失败。"""
        if not isinstance(envelope, AgentEnvelope):
            raise TypeError("envelope 必须是 AgentEnvelope")
        with self._lock:
            for endpoint in (envelope.from_, envelope.to):
                if endpoint not in self._boxes:
                    raise MailboxViolation(f"unknown_participant:{endpoint}")
            if envelope.envelope_id in self._seen:
                raise MailboxViolation(f"duplicate_envelope:{envelope.envelope_id}")
            if envelope.encoded_size() > self._max_payload_bytes:
                raise MailboxViolation("payload_too_large")
            box = self._boxes[envelope.to]
            if len(box) >= self._max_per_recipient:
                raise MailboxFull(f"mailbox_full:{envelope.to}")
            box.append(envelope)
            self._seen.add(envelope.envelope_id)

    def _take(self, recipient: str,
              types: tuple[MessageType, ...] | None) -> tuple[AgentEnvelope, ...]:
        with self._lock:
            box = self._boxes.get(recipient)
            if box is None:
                raise MailboxViolation(f"unknown_recipient:{recipient}")
            if types is None:
                wanted = set(MESSAGE_TYPES)
            else:
                if isinstance(types, str) or not isinstance(types, tuple):
                    raise TypeError("types 必须是元组")
                for item in types:
                    if item not in MESSAGE_TYPES:
                        raise MailboxViolation(f"unknown_message_type:{item}")
                wanted = set(types)
            taken: list[AgentEnvelope] = []
            kept: deque[AgentEnvelope] = deque()
            while box:
                envelope = box.popleft()
                (taken if envelope.type in wanted else kept).append(envelope)
            box.extend(kept)
            return tuple(taken)

    def _pending(self, recipient: str) -> int:
        with self._lock:
            box = self._boxes.get(recipient)
            if box is None:
                raise MailboxViolation(f"unknown_recipient:{recipient}")
            return len(box)

    def snapshot(self) -> dict[str, int]:
        """每个收件人的未读数；**不含消息内容**，可安全进日志。"""
        with self._lock:
            return {name: len(box) for name, box in self._boxes.items()}
