"""可信调用方持有的内存硬预算；调用前扣除，不估算token或费用。"""

from threading import RLock
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Resource = Literal["model_calls", "tool_calls"]
Purpose = Literal["main", "repair", "retry", "research", "compaction"]
_RESOURCES = ("model_calls", "tool_calls")
_PURPOSES = ("main", "repair", "retry", "research", "compaction")


class BudgetLimits(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    model_calls: int = Field(ge=0)
    tool_calls: int = Field(ge=0)


class BudgetUsage(BaseModel):
    """独立快照；修改嵌套用途字典也不影响实时账本。"""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    model_calls: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    by_purpose: dict[Purpose, BudgetLimits]

    @model_validator(mode="after")
    def check_totals(self):
        for resource in _RESOURCES:
            if sum(getattr(part, resource) for part in self.by_purpose.values()) != getattr(self, resource):
                raise ValueError("预算用途分项与总计不一致")
        return self


class BudgetNode(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    limits: BudgetLimits
    usage: BudgetUsage
    parent_id: UUID | None = None
    purpose: Purpose = "main"

    @model_validator(mode="after")
    def check_usage(self):
        if any(getattr(self.usage, key) > getattr(self.limits, key) for key in _RESOURCES):
            raise ValueError("预算消耗超过限额")
        return self


def _count(usage, resource, purpose=None):
    part = usage if purpose is None else usage.by_purpose.get(purpose)
    return getattr(part, resource) if part is not None else 0


class BudgetSnapshot(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    format_version: Literal[1] = 1
    limits: BudgetLimits
    usage: BudgetUsage
    segments: dict[UUID, BudgetNode]

    @field_validator("format_version", mode="before")
    @classmethod
    def check_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("预算快照版本无效")
        return value

    @model_validator(mode="after")
    def check_tree(self):
        if any(getattr(self.usage, key) > getattr(self.limits, key) for key in _RESOURCES):
            raise ValueError("累计预算消耗超过限额")
        for segment_id in self.segments:
            seen, current = set(), segment_id
            while current is not None:
                if current in seen or current not in self.segments:
                    raise ValueError("预算片段层级无效")
                seen.add(current)
                current = self.segments[current].parent_id
        roots = [node for node in self.segments.values() if node.parent_id is None]
        for resource in _RESOURCES:
            for purpose in (None, *_PURPOSES):
                if sum(_count(node.usage, resource, purpose) for node in roots) != _count(self.usage, resource, purpose):
                    raise ValueError("预算根片段与累计不一致")
                for segment_id, node in self.segments.items():
                    children = [part for part in self.segments.values() if part.parent_id == segment_id]
                    if sum(_count(part.usage, resource, purpose) for part in children) > _count(node.usage, resource, purpose):
                        raise ValueError("预算子片段消耗超过上级")
        return self


class BudgetBinding(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    budget_id: UUID
    owner_id: UUID
    session_id: UUID


class BudgetRecord(BudgetBinding):
    revision: int = Field(ge=0)
    state: BudgetSnapshot


def check_budget_successor(old: BudgetRecord, new: BudgetRecord) -> None:
    if (old.budget_id, old.owner_id, old.session_id) != (new.budget_id, new.owner_id, new.session_id):
        raise ValueError("预算归属不能改变")
    if new.revision != old.revision + 1 or new.state.limits != old.state.limits:
        raise ValueError("预算版本或固定总限额不一致")
    if not old.state.segments.keys() <= new.state.segments.keys():
        raise ValueError("不能移除已有预算片段")
    pairs = [(old.state.usage, new.state.usage)]
    for segment_id, before in old.state.segments.items():
        after = new.state.segments[segment_id]
        if (before.limits, before.parent_id, before.purpose) != (after.limits, after.parent_id, after.purpose):
            raise ValueError("已有片段的限额或层级不能改变")
        pairs.append((before.usage, after.usage))
    for before, after in pairs:
        if any(_count(after, key, purpose) < _count(before, key, purpose)
               for key in _RESOURCES for purpose in (None, *_PURPOSES)):
            raise ValueError("不能减少预算消耗")


class BudgetPersistenceError(RuntimeError):
    def __init__(self):
        super().__init__("预算状态未能安全提交；未放行新调用")


class BudgetExceeded(RuntimeError):
    def __init__(self, scope: Literal["task", "segment"], resource: Resource) -> None:
        super().__init__("预算不足，未启动新调用")
        self.scope = scope
        self.resource = resource


def _limits(value):
    if not isinstance(value, BudgetLimits):
        raise TypeError("预算必须使用BudgetLimits")
    return BudgetLimits.model_validate(value.model_dump(warnings="error"))


def _purpose(value):
    if value not in _PURPOSES:
        raise ValueError("未支持的预算用途")
    return value


def _increment(owner, resource, purpose):
    owner._counts[resource] += 1
    owner._by_purpose.setdefault(purpose, dict.fromkeys(_RESOURCES, 0))[resource] += 1


def _snapshot(owner):
    return BudgetUsage(**owner._counts, by_purpose=owner._by_purpose)


class BudgetLedger:
    """一个任务的累计账本；新片段及其子调用必须复用该实例。"""

    def __init__(self, total_limits: BudgetLimits, *, on_change=None, lock=None,
                 binding: BudgetBinding | None = None, check_context=None) -> None:
        self._limits = _limits(total_limits)
        self._lock = lock if lock is not None else RLock()
        self._on_change, self._check_context = on_change, check_context
        self._persistence_failed = False
        self._binding = BudgetBinding.model_validate(binding.model_dump()) if binding is not None else None
        self._segments = {}
        self._counts = dict.fromkeys(_RESOURCES, 0)
        self._by_purpose = {}

    def start_segment(self, limits: BudgetLimits, *, purpose: Purpose = "main") -> "BudgetSegment":
        return BudgetSegment(self, limits, purpose=purpose)

    @property
    def usage(self) -> BudgetUsage:
        with self._lock:
            return _snapshot(self)

    def snapshot(self) -> BudgetSnapshot:
        with self._lock:
            return BudgetSnapshot(limits=self._limits.model_copy(), usage=_snapshot(self), segments={
                key: BudgetNode(limits=node._limits.model_copy(), usage=_snapshot(node),
                                parent_id=node._parent.segment_id if node._parent else None, purpose=node._purpose)
                for key, node in self._segments.items()
            })

    def segment(self, segment_id: UUID) -> "BudgetSegment":
        with self._lock:
            return self._segments[segment_id]

    def _persist(self):
        if self._persistence_failed:
            raise BudgetPersistenceError()
        if self._on_change is not None:
            try:
                self._on_change(self.snapshot())
            except BaseException as error:
                # 提交结果可能未知；旧句柄必须停用，不能按回滚的内存值再次放行。
                self._persistence_failed = True
                if isinstance(error, Exception):
                    raise BudgetPersistenceError() from None
                raise

    @classmethod
    def restore(cls, snapshot: BudgetSnapshot, **options) -> "BudgetLedger":
        if not isinstance(snapshot, BudgetSnapshot):
            raise TypeError("必须使用BudgetSnapshot恢复")
        snapshot = BudgetSnapshot.model_validate(snapshot.model_dump())
        on_change = options.pop("on_change", None)
        ledger = cls(snapshot.limits, **options)
        pending = dict(snapshot.segments)
        while pending:
            for segment_id, state in list(pending.items()):
                if state.parent_id is not None and state.parent_id not in ledger._segments:
                    continue
                node = BudgetSegment(ledger, state.limits, parent=ledger._segments.get(state.parent_id),
                                     purpose=state.purpose, _segment_id=segment_id)
                node._counts = {key: getattr(state.usage, key) for key in _RESOURCES}
                node._by_purpose = {key: part.model_dump() for key, part in state.usage.by_purpose.items()}
                del pending[segment_id]
        ledger._counts = {key: getattr(snapshot.usage, key) for key in _RESOURCES}
        ledger._by_purpose = {key: part.model_dump() for key, part in snapshot.usage.by_purpose.items()}
        ledger._on_change = on_change
        return ledger


class BudgetSegment:
    """一个执行片段；子片段逐次消费，同时受所有上级及累计上限约束。"""

    def __init__(
        self, ledger: BudgetLedger, limits: BudgetLimits, *,
        parent: "BudgetSegment | None" = None, purpose: Purpose = "main", _segment_id: UUID | None = None,
    ) -> None:
        if not isinstance(ledger, BudgetLedger):
            raise TypeError("预算片段必须绑定任务账本")
        if parent is not None and (not isinstance(parent, BudgetSegment) or parent._ledger is not ledger):
            raise ValueError("子片段必须使用上级的同一任务账本")
        self._ledger, self._parent = ledger, parent
        self._limits, self._purpose = _limits(limits), _purpose(purpose)
        self._counts = dict.fromkeys(_RESOURCES, 0)
        self._by_purpose = {}
        self._segment_id = _segment_id if _segment_id is not None else uuid4()
        with ledger._lock:
            if self._segment_id in ledger._segments:
                raise ValueError("预算片段ID已存在")
            ledger._segments[self._segment_id] = self
            try:
                ledger._persist()
            except BaseException:
                del ledger._segments[self._segment_id]
                raise

    @property
    def segment_id(self) -> UUID:
        return self._segment_id

    @property
    def binding(self) -> BudgetBinding | None:
        return self._ledger._binding.model_copy() if self._ledger._binding is not None else None

    def check_context(self, context=None, store=None) -> None:
        if self._ledger._check_context is not None:
            self._ledger._check_context(context, store)

    def child(self, limits: BudgetLimits, *, purpose: Purpose | None = None) -> "BudgetSegment":
        return BudgetSegment(
            self._ledger, limits, parent=self,
            purpose=self._purpose if purpose is None else purpose,
        )

    @property
    def usage(self) -> BudgetUsage:
        with self._ledger._lock:
            return _snapshot(self)

    @property
    def remaining(self) -> BudgetLimits:
        """只读各级最紧剩余值；真正派发仍须经过原子的consume硬检查。"""
        with self._ledger._lock:
            owners, node = [self._ledger], self
            while node is not None:
                owners.append(node)
                node = node._parent
            return BudgetLimits(**{
                resource: min(getattr(owner._limits, resource) - owner._counts[resource]
                              for owner in owners)
                for resource in _RESOURCES
            })

    def consume(self, resource: Resource, *, purpose: Purpose | None = None) -> None:
        if resource not in _RESOURCES:
            raise ValueError("未支持的预算资源")
        purpose = self._purpose if purpose is None else _purpose(purpose)
        with self._ledger._lock:
            if self._ledger._persistence_failed:
                raise BudgetPersistenceError()
            nodes, node = [], self
            while node is not None:
                nodes.append(node)
                node = node._parent
            if self._ledger._counts[resource] >= getattr(self._ledger._limits, resource):
                raise BudgetExceeded("task", resource)
            for node in nodes:
                if node._counts[resource] >= getattr(node._limits, resource):
                    raise BudgetExceeded("segment", resource)
            # 全部检查通过才扣除；不因异常或子调用而退还/重复扣累计额度。
            owners = [self._ledger, *nodes]
            before = [(owner, owner._counts.copy(), {key: value.copy() for key, value in owner._by_purpose.items()}) for owner in owners]
            try:
                for owner in owners:
                    _increment(owner, resource, purpose)
                self._ledger._persist()
            except BaseException:
                for owner, counts, purposes in before:
                    owner._counts, owner._by_purpose = counts, purposes
                raise
