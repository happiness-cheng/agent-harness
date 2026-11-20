"""MA-P5-008：Hooks 注册表（5 类事件，**只放行/拒绝，不允许改参数**）。

为什么不允许改参数：可修改请求的钩子会让「实际执行了什么」无法从调用参数审计，
失败归因也会失真（到底是调用方写的，还是钩子改的？）。需要改参数应当改调用方。

失败策略：钩子抛异常或返回非法对象时 **fail-closed**（拒绝），不静默放行；
多个钩子按 ``priority`` 升序执行，任一拒绝立即短路。

边界：钩子必须同步、无网络、快速返回；本模块不做超时强杀（同进程无法安全打断），
超时由调用方在钩子内部自行控制。
"""

from __future__ import annotations

import threading
from types import MappingProxyType
from typing import Callable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "HookEvent", "HOOK_EVENTS", "HookDecision", "HookRegistry", "Hook",
]

HookEvent = Literal[
    "before_model", "after_model", "before_tool", "after_tool", "on_error",
]
HOOK_EVENTS: tuple[str, ...] = (
    "before_model", "after_model", "before_tool", "after_tool", "on_error",
)


class _Strict(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class HookDecision(_Strict):
    allow: bool
    reason: str = Field(default="", max_length=200)


Hook = Callable[[Mapping[str, object]], HookDecision | None]


class HookRegistry:
    """线程安全的同步钩子表；``context`` 以只读视图传入。"""

    def __init__(self) -> None:
        self._hooks: dict[str, list[tuple[int, str, Hook]]] = {
            event: [] for event in HOOK_EVENTS
        }
        self._lock = threading.RLock()

    def register(self, event: str, hook: Hook, *, name: str = "",
                 priority: int = 0) -> None:
        if event not in HOOK_EVENTS:
            raise KeyError(f"未知钩子事件：{event}")
        if not callable(hook):
            raise TypeError("hook 必须可调用")
        label = name or getattr(hook, "__name__", "hook")
        with self._lock:
            self._hooks[event].append((int(priority), label, hook))

    def emit(self, event: str, context: Mapping[str, object] | None = None
             ) -> HookDecision:
        if event not in HOOK_EVENTS:
            raise KeyError(f"未知钩子事件：{event}")
        view = MappingProxyType(dict(context or {}))
        with self._lock:
            hooks = sorted(self._hooks[event], key=lambda item: (item[0], item[1]))
        for _, name, hook in hooks:
            try:
                decision = hook(view)
            except Exception as error:  # fail-closed，不静默放行
                return HookDecision(
                    allow=False, reason=f"hook_error:{name}:{type(error).__name__}")
            if decision is None:
                continue
            if not isinstance(decision, HookDecision):
                return HookDecision(allow=False, reason=f"hook_invalid:{name}")
            if not decision.allow:
                detail = f"{name}:{decision.reason}" if decision.reason else name
                return HookDecision(allow=False, reason=detail[:200])
        return HookDecision(allow=True, reason="")

    def snapshot(self) -> dict[str, list[dict[str, object]]]:
        with self._lock:
            return {
                event: [{"priority": priority, "name": name}
                        for priority, name, _ in sorted(entries,
                                                        key=lambda item: (item[0], item[1]))]
                for event, entries in self._hooks.items()
            }
