"""长期记忆的权威实体；Core/索引只是这些实体的可重建视图。"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Entity(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, allow_inf_nan=False)
    id: str
    user_id: str

    @model_validator(mode='after')
    def aware_times(self):
        if any(isinstance(value,datetime) and value.tzinfo is None for value in self.__dict__.values()):
            raise ValueError('实体时间必须显式包含时区')
        return self


class Evidence(Entity):
    session_id: str
    message_ref: int
    role: Literal['user', 'assistant', 'tool']
    observed_at: datetime
    source_hash: str
    excerpt_or_pointer: str
    classification: Literal['DIRECT', 'INFERRED']


class Memory(Entity):
    scope: Literal['user', 'session'] = 'user'
    session_id: str
    kind: Literal['preference', 'constraint', 'fact', 'goal_note', 'episode'] = 'fact'
    content: str = Field(min_length=1, max_length=8000)
    evidence_type: Literal['DIRECT', 'INFERRED'] = 'DIRECT'
    confirmation_status: Literal['confirmed'] = 'confirmed'
    evidence_refs: list[str]
    observed_at: datetime
    effective_from: datetime
    valid_until: datetime | None = None
    lifecycle: Literal['active', 'disputed', 'retired'] = 'active'
    version: int = Field(default=1, ge=1)
    created_at: datetime
    updated_at: datetime
    confirmed_at: datetime
    confirmation_ref: str
    topic_ids: list[str] = Field(default_factory=list)
    scenario_ids: list[str] = Field(default_factory=list)
    goal_ids: list[str] = Field(default_factory=list)
    conflict_key: str | None = None
    breadth: Literal['broad', 'specific'] = 'specific'
    decision_cost: int = Field(default=1, ge=0, le=3)
    stable: bool = False


class Topic(Entity):
    name: str
    summary: str = ''
    keywords: list[str] = Field(default_factory=list)
    version: int = 1
    created_at: datetime


class Scenario(Entity):
    label: str
    conditions: list[str] = Field(default_factory=list)
    created_at: datetime


class Goal(Entity):
    content: str
    lifecycle: Literal['active', 'paused', 'completed', 'cancelled'] = 'active'
    priority: int = Field(default=1, ge=0, le=3)
    horizon: Literal['long_term', 'stage'] = 'stage'
    effective_from: datetime
    observed_at: datetime
    updated_at: datetime
    source_ref: str
    version: int = 1


class MemoryEdge(Entity):
    source_id: str
    target_id: str
    type: Literal['BELONGS_TO', 'APPLIES_IN', 'ABOUT', 'DERIVED_FROM', 'RELATED_TO',
                  'SUPERSEDES', 'GENERALIZES', 'EXCEPTION_OF']
    evidence_refs: list[str] = Field(default_factory=list)
    status: Literal['confirmed', 'proposed'] = 'confirmed'
    observed_at: datetime


class Candidate(Entity):
    session_id: str
    content: str = Field(min_length=1, max_length=8000)
    context: dict = Field(default_factory=dict)
    evidence_refs: list[str]
    status: Literal['pending', 'confirmed', 'rejected'] = 'pending'
    fingerprint: str
    expires_at: datetime
    proposed_at: datetime
    review_token: str
    memory_id: str | None = None


class MemoryDocument(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    schema_version: Literal[1] = 1
    revision: int = 0
    memories: dict[str, list[Memory]] = Field(default_factory=dict)
    evidence: dict[str, Evidence] = Field(default_factory=dict)
    topics: dict[str, Topic] = Field(default_factory=dict)
    scenarios: dict[str, Scenario] = Field(default_factory=dict)
    goals: dict[str, list[Goal]] = Field(default_factory=dict)
    edges: dict[str, MemoryEdge] = Field(default_factory=dict)
    candidates: dict[str, Candidate] = Field(default_factory=dict)
    tombstones: list[str] = Field(default_factory=list)
    suppressed_fingerprints: list[str] = Field(default_factory=list)
    blocked_source_hashes: list[str] = Field(default_factory=list)
    pending_erasure_ids: list[str] = Field(default_factory=list)
    events: list[dict] = Field(default_factory=list)
    usages: list[dict] = Field(default_factory=list)


class Query(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    text: str = Field(min_length=1, max_length=20000)
    as_of: datetime
    scenario_ids: list[str] = Field(default_factory=list)
    directives: dict[str, str] = Field(default_factory=dict)
    top_k: int = Field(default=8, ge=1, le=100)
    top_g: int = Field(default=3, ge=1, le=20)
    route_min_score: float = Field(default=0.15,ge=0,le=1)
    graph_hops: int = Field(default=1, ge=0, le=2)
    max_candidates: int = Field(default=100, ge=1, le=500)

    @model_validator(mode='after')
    def aware(self):
        if self.as_of.tzinfo is None: raise ValueError('as_of必须有时区')
        return self


class SearchHit(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    memory_id: str
    source: str
    score: float
    topic_id: str | None = None
    reasons: list[str] = Field(default_factory=list)


def encode_tokens_upper_bound(text: str) -> int:
    """UTF-8字节上界，明确不是provider精确Token；包括所给序列化文本。"""
    return len(text.encode('utf-8'))
