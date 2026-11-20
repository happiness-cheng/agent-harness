"""MA-P5-011：并行调度的**纯机械部分**。

用户定下的分工（DECISION_LOG S1–S5）：**是否并行交给模型判断，规则只判断
执行的时候依赖项执行了没有**。具体说：

* **模型画规划**：两个任务不能同时跑，就在任务图里加一条 ``blockedBy`` 边；
  能同时跑，就不加边。写不写同一个文件、有没有隐式冲突，由模型在画边时
  一次性考虑，规则不看文件、不看资源、不做集合相交。
* **规则负责管**：``TaskStore.can_start`` 查「依赖项是否全部 completed」，
  加上 ``claim`` / ``complete`` / ``fail`` 状态机。本模块只再提供一个
  :func:`ready_batches`——把就绪任务按 ``max_workers`` 切成批，纯粹是并发
  上限的机械切分，**不是并行判定**。

**已废弃但保留**：``ResourceScope`` / ``conflict_reasons`` /
``conflict_batches`` / ``layer_mode`` / ``plan_layers`` 属于 MA-P5-009/010
的两轮尝试（规则替模型判断并行）。它们仅为历史自验脚本可 import 而保留，
``Manager`` 主路径零调用。新代码不要使用；「未声明就串行」「有写就串行」
这两条判据已被用户明确否决。

边界：把判断权交给模型，也就把「漏加一条边」的风险交给模型；规则不再兜底。
跨文件语义冲突、模型漏加边属已知边界，靠后续合并后验证 / 独立审查处理。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, Sequence, TypeVar

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ToolAccess", "LayerMode", "ConcurrencyPolicy", "plan_layers", "layer_mode",
    "ResourceScope", "conflict_reasons", "conflict_batches", "ready_batches",
]

ToolAccess = Literal["read_only", "write"]
LayerMode = Literal["parallel", "serial"]

T = TypeVar("T")


class _Strict(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class ConcurrencyPolicy(_Strict):
    """并发上限配置。**只有 max_workers 有意义**：它不是判定，只是线程池大小。"""

    max_workers: int = Field(default=1, ge=1, le=16)
    # 以下三项仅为兼容 MA-P5-009/010 历史自验 import 保留，主路径零读取。
    allow_read_parallel: bool = True
    serialize_writes: bool = True
    serialize_undeclared: bool = True


class _HasDeps(Protocol):
    subtask_id: str
    depends_on: tuple[str, ...]


def plan_layers(tasks: Sequence[_HasDeps]) -> tuple[tuple[_HasDeps, ...], ...]:
    """按依赖分层；依赖只能指向已出现任务（上层已校验），因此不会成环。"""
    remaining = list(tasks)
    done: set[str] = set()
    layers: list[tuple[_HasDeps, ...]] = []
    while remaining:
        layer = [task for task in remaining
                 if all(dep in done for dep in task.depends_on)]
        if not layer:
            raise ValueError("dependency_cycle_or_forward_reference")
        layers.append(tuple(layer))
        done.update(task.subtask_id for task in layer)
        remaining = [task for task in remaining if task.subtask_id not in done]
    return tuple(layers)


def layer_mode(accesses: Sequence[ToolAccess],
               policy: ConcurrencyPolicy) -> LayerMode:
    """**已废弃**（MA-P5-009/010）：静态标签推断并行。用户已否决「一有写就串行」。
    仅为历史脚本 import 保留，主路径零调用。
    """
    if policy.max_workers <= 1 or len(accesses) <= 1:
        return "serial"
    if not policy.allow_read_parallel:
        return "serial"
    if policy.serialize_writes and any(item == "write" for item in accesses):
        return "serial"
    return "parallel"


@dataclass(frozen=True)
class ResourceScope:
    """模型在规划里声明的资源范围。``declared=False`` 表示未声明。"""

    writes: frozenset[str] = frozenset()
    reads: frozenset[str] = frozenset()
    declared: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.declared, bool):
            raise TypeError("declared 必须是 bool")
        # frozenset 归一化，避免调用方传 list 后比较失真
        object.__setattr__(self, "writes", frozenset(self.writes))
        object.__setattr__(self, "reads", frozenset(self.reads))


def conflict_reasons(left: ResourceScope,
                     right: ResourceScope,
                     policy: ConcurrencyPolicy | None = None) -> tuple[str, ...]:
    """**已废弃**（MA-P5-011）：规则不再判断资源冲突。仅为历史脚本保留。

    原设计让规则替模型判断「能不能并行」，其中「未声明就串行」被用户否决。
    新设计中，模型用 ``blockedBy`` 边表达串行意图，规则只查 ``can_start``。
    """
    if not isinstance(left, ResourceScope) or not isinstance(right, ResourceScope):
        raise TypeError("需要 ResourceScope")
    active = policy or ConcurrencyPolicy()
    if not left.declared or not right.declared:
        return ("undeclared_scope",) if active.serialize_undeclared else ()
    reasons: list[str] = []
    if left.writes & right.writes:
        reasons.append("write_write")
    if left.writes & right.reads or right.writes & left.reads:
        reasons.append("write_read")
    return tuple(reasons)


def conflict_batches(items: Sequence[tuple[T, ResourceScope]],
                     policy: ConcurrencyPolicy) -> tuple[tuple[T, ...], ...]:
    """**已废弃**（MA-P5-011）：按资源冲突分批。仅为历史脚本 import 保留。

    新路径请用 :func:`ready_batches`——只按并发上限切批，不做任何冲突判定。
    """
    if not isinstance(policy, ConcurrencyPolicy):
        raise TypeError("需要 ConcurrencyPolicy")
    batches: list[list[T]] = []
    scopes: list[list[ResourceScope]] = []
    for item, scope in items:
        for index, members in enumerate(scopes):
            if len(batches[index]) >= policy.max_workers:
                continue
            if all(not conflict_reasons(scope, existing, policy)
                   for existing in members):
                batches[index].append(item)
                members.append(scope)
                break
        else:
            batches.append([item])
            scopes.append([scope])
    return tuple(tuple(batch) for batch in batches)


def ready_batches(items: Sequence[T], max_workers: int) -> tuple[tuple[T, ...], ...]:
    """把「已通过 ``can_start`` 的就绪任务」按并发上限切成批。

    这是**唯一的调度动作**，且纯机械：不读资源、不读标签、不比较任务内容。
    ``max_workers`` 是线程池大小，不是「能不能并行」的判据——能不能并行
    已经由模型在画 ``blockedBy`` 边时决定了。

    >>> ready_batches(("a", "b", "c"), 2)
    (('a', 'b'), ('c',))
    """
    if not isinstance(max_workers, int) or isinstance(max_workers, bool) \
            or not 1 <= max_workers <= 16:
        raise ValueError("max_workers 必须在 1..16 之间")
    ordered = tuple(items)
    return tuple(ordered[index:index + max_workers]
                 for index in range(0, len(ordered), max_workers))
