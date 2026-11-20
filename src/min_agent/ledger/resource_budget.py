"""MA-P5-008：资源硬门（token / 费用 / 总时长，**调用前预占**）。

与既有 ``budget.BudgetSegment`` 的分工（不替换、不合并）：

* ``BudgetSegment``：生产在用的**次数**硬门（model_calls / tool_calls），逐级收紧、
  原子 consume、持久绑定；
* ``ResourceLedger``：本模块，管 **prompt / completion / total tokens、费用（micros）、
  总时长（单调时钟）**，只在 worker 层接入。

为什么要预占而不是事后记账：事后记账能"查出超支"，但拦不住已经发生的调用
（对应 CAL-004 与 CAL-005 的区别）。这里在调用前按「prompt 估算 + completion 取
max_tokens 上限」预占，任一项不足即拒绝，调用不发生；调用后按实际 usage 结算，
多余预占释放。

边界（不得美化）：

* 预占成功 **不等于** 一定不超支：估算偏差会导致 ``actual > reserved``，此时允许
  补扣但记 ``overrun``，因为调用已经发生；
* 有费用上限而模型未登记价格时抛 :class:`UnknownPrice`，**不允许"先花再说"**；
* 时长用注入的单调时钟，不用 wall clock，避免系统时间跳变。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Callable, Mapping

from pydantic import BaseModel, ConfigDict

__all__ = [
    "ResourceName", "RESOURCES", "ResourceLimits", "ResourceUsage", "Reservation",
    "ResourceLedger", "ResourceExhausted", "DeadlineExceeded", "UnknownPrice",
    "ModelPrice", "PriceTable", "estimate_tokens",
]

ResourceName = str
RESOURCES = (
    "model_calls", "tool_calls", "prompt_tokens", "completion_tokens",
    "total_tokens", "cost_micros",
)


class _Strict(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class ResourceLimits(_Strict):
    """``None`` 表示该资源不限制；有限值表示硬上限。"""

    model_calls: int | None = None
    tool_calls: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cost_micros: int | None = None
    wall_seconds: float | None = None


class ResourceUsage(_Strict):
    model_calls: int = 0
    tool_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_micros: int = 0
    wall_seconds: float = 0.0


class ResourceExhausted(RuntimeError):
    def __init__(self, resource: str, requested: int, remaining: int) -> None:
        super().__init__(f"resource_exhausted:{resource}:requested={requested},remaining={remaining}")
        self.resource = resource
        self.requested = requested
        self.remaining = remaining


class DeadlineExceeded(RuntimeError):
    """已超过总时长上限；调用方必须停止，不得继续派发。"""


class UnknownPrice(RuntimeError):
    """有费用上限但模型未登记价格；拒绝调用，不允许"先花再说"。"""


class Reservation:
    """一次预占；必须由 ``commit`` 或 ``release`` 收尾，不能悬空。"""

    __slots__ = ("amounts", "parent")

    def __init__(self, amounts: dict[str, int], parent: "Reservation | None") -> None:
        self.amounts = dict(amounts)
        self.parent = parent

    def __repr__(self) -> str:
        return f"Reservation({self.amounts})"


class ResourceLedger:
    """父子资源账本：子额度不超过父剩余，预占逐级向上传递。"""

    def __init__(self, limits: ResourceLimits, *, parent: "ResourceLedger | None" = None,
                 clock: Callable[[], float] = time.monotonic,
                 deadline_seconds: float | None = None) -> None:
        if not isinstance(limits, ResourceLimits):
            raise TypeError("limits 必须是 ResourceLimits")
        for name in ("model_calls", "tool_calls", "prompt_tokens", "completion_tokens",
                     "total_tokens", "cost_micros"):
            value = getattr(limits, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} 不能为负")
        if limits.wall_seconds is not None and limits.wall_seconds <= 0:
            raise ValueError("wall_seconds 必须为正")
        self._limits = limits
        self._parent = parent
        self._clock = parent._clock if parent is not None else clock
        self._used = {name: 0 for name in RESOURCES}
        self._reserved = {name: 0 for name in RESOURCES}
        self._overrun = False
        limit_seconds = deadline_seconds if deadline_seconds is not None else limits.wall_seconds
        if limit_seconds is not None:
            parent_left = parent.remaining_seconds() if parent is not None else None
            if parent_left is not None:
                limit_seconds = min(limit_seconds, parent_left)
            self._deadline: float | None = self._clock() + limit_seconds
        elif parent is not None:
            self._deadline = parent._deadline
        else:
            self._deadline = None
        self._lock = threading.RLock()

    @property
    def limits(self) -> ResourceLimits:
        return self._limits

    @property
    def overrun(self) -> bool:
        return self._overrun

    def remaining(self, resource: str) -> int | None:
        if resource not in RESOURCES:
            raise KeyError(f"未知资源：{resource}")
        with self._lock:
            own: int | None = None
            limit = getattr(self._limits, resource)
            if limit is not None:
                own = limit - self._used[resource] - self._reserved[resource]
            parent_left = (self._parent.remaining(resource)
                           if self._parent is not None else None)
            if own is None:
                return parent_left
            if parent_left is None:
                return own
            return min(own, parent_left)

    def remaining_seconds(self) -> float | None:
        if self._deadline is None:
            return None
        return max(0.0, self._deadline - self._clock())

    def exhausted(self) -> tuple[str, ...]:
        return tuple(
            name for name in RESOURCES
            if (left := self.remaining(name)) is not None and left <= 0
        )

    def check_deadline(self) -> None:
        if self._deadline is not None and self._clock() >= self._deadline:
            raise DeadlineExceeded("deadline_exceeded")

    def child(self, limits: ResourceLimits, *,
              wall_seconds: float | None = None) -> "ResourceLedger":
        return ResourceLedger(limits, parent=self,
                              deadline_seconds=wall_seconds)

    def reserve(self, **amounts: int) -> Reservation:
        """原子预占；任一项不足即抛，调用不应发生。

        检查与增加保留必须在**同一把锁**内，否则两个线程可能同时通过检查、
        合计超额（并发超卖）。初版把检查放在锁外，是实现缺陷。
        """
        clean = {name: int(value) for name, value in amounts.items()
                 if name in RESOURCES and value}
        with self._lock:
            self.check_deadline()
            for name, amount in clean.items():
                if amount < 0:
                    raise ValueError(f"{name} 预占量不能为负")
                left = self.remaining(name)
                if left is not None and amount > left:
                    raise ResourceExhausted(name, amount, left)
            parent_reservation = (self._parent.reserve(**clean)
                                  if self._parent is not None else None)
            for name, amount in clean.items():
                self._reserved[name] += amount
        return Reservation(clean, parent_reservation)

    def commit(self, reservation: Reservation, **actuals: int) -> None:
        """按实际结算；``actual > reserved`` 记 overrun，不否认已发生。"""
        if not isinstance(reservation, Reservation):
            raise TypeError("reservation 必须是 Reservation")
        clean = {name: int(value) for name, value in actuals.items()
                 if name in RESOURCES and value is not None}
        with self._lock:
            for name, amount in reservation.amounts.items():
                self._reserved[name] = max(0, self._reserved[name] - amount)
            for name, amount in clean.items():
                if amount < 0:
                    raise ValueError(f"{name} 实际用量不能为负")
                if amount > reservation.amounts.get(name, 0):
                    self._overrun = True
                self._used[name] += amount
        if self._parent is not None:
            self._parent.commit(reservation.parent, **clean)

    def release(self, reservation: Reservation) -> None:
        if not isinstance(reservation, Reservation):
            raise TypeError("reservation 必须是 Reservation")
        with self._lock:
            for name, amount in reservation.amounts.items():
                self._reserved[name] = max(0, self._reserved[name] - amount)
        if self._parent is not None:
            self._parent.release(reservation.parent)

    def snapshot(self) -> dict[str, object]:
        return {
            "limits": {name: getattr(self._limits, name) for name in RESOURCES}
            | {"wall_seconds": self._limits.wall_seconds},
            "used": dict(self._used),
            "reserved": dict(self._reserved),
            "remaining": {name: self.remaining(name) for name in RESOURCES},
            "remaining_seconds": self.remaining_seconds(),
            "exhausted": list(self.exhausted()),
            "overrun": self._overrun,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }


class ModelPrice(_Strict):
    """单价以 micros / 1M tokens 表示，整数避免浮点误差。"""

    model_id: str
    input_micros_per_million: int
    output_micros_per_million: int


class PriceTable:
    def __init__(self, prices: Mapping[str, ModelPrice]) -> None:
        self._prices = dict(prices)

    def price(self, model_id: str) -> ModelPrice | None:
        return self._prices.get(model_id)

    def cost_micros(self, model_id: str, prompt_tokens: int,
                    completion_tokens: int) -> int | None:
        """未登记价格返回 ``None``；调用方必须自行决定拒绝还是允许。"""
        price = self.price(model_id)
        if price is None:
            return None
        total = (prompt_tokens * price.input_micros_per_million
                 + completion_tokens * price.output_micros_per_million)
        return total // 1_000_000


def estimate_tokens(text: str) -> int:
    """粗略估算 prompt tokens；只用于预占，实际以 usage 结算。

    英文约 4 字符 / token，中文约 1 字 / token；按 ``len // 3`` 统一处理会
    把中文低估约 3 倍（首版即因此产生不必要的 overrun）。
    """
    if not isinstance(text, str):
        raise TypeError("text 必须是字符串")
    ascii_chars = sum(1 for char in text if ord(char) < 128)
    wide_chars = len(text) - ascii_chars
    return max(1, ascii_chars // 4 + wide_chars)
