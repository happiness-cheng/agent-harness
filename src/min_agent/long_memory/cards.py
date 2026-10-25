"""批准 Fixture 的只读上下文化实验；不提供生产记忆写入/确认入口。"""
from datetime import datetime
from hashlib import sha256
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from min_agent.long_memory.models import SearchHit
from min_agent.long_memory.retrieval import eligible_for_query


class EntityName(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    name: str
    aliases: list[str] = Field(default_factory=list)


class ValidTime(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    start: datetime
    end: datetime | None = None

    @model_validator(mode='after')
    def validate_interval(self):
        if self.start.tzinfo is None or self.end is not None and self.end.tzinfo is None:
            raise ValueError('卡片时间必须有时区')
        if self.end is not None and self.end <= self.start:
            raise ValueError('卡片有效区间为空')
        return self


class MemoryCard(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    memory_id: str
    user_id: str
    content: str = Field(min_length=1, max_length=8000)
    entity: list[EntityName]
    scenario: str
    valid_time: ValidTime
    evidence_refs: list[str] = Field(min_length=1)
    evidence_type: Literal['DIRECT', 'INFERRED']
    confirmation_status: Literal['pending', 'confirmed_fixture']
    topic_ids: list[str]
    source_episode_id: str
    source_version: int
    source_sha256: str


def _fingerprint(memory):
    return sha256(memory.model_dump_json().encode()).hexdigest()


def _body(memory):
    match = re.match(r'^\[(user|assistant|tool);session=(.*?);range=(\d+):(\d+)\] (.*)$', memory.content, re.S)
    if match is None:
        raise ValueError('仅接受带角色和范围的原始 Episode Fixture')
    return match.group(1), match.group(2), int(match.group(3)), match.group(5)


def _entities(text, business):
    # 只识别原文可见的名字/编号；别名必须采用原文的 Name (ALIAS) 声明。
    names = {business} if business else set()
    names.update(re.findall(r'\b[A-Z][a-z]+(?: [A-Z][a-z]+){1,3}\b|\b[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+\b', text))
    aliases = {}
    for name, alias in re.findall(r'\b([A-Z][A-Za-z]+(?: [A-Z][A-Za-z]+){0,3})\s*\(([A-Z][A-Z0-9]{1,9})\)', text+' '+business):
        names.add(name)
        aliases.setdefault(name, []).append(alias)
    return [EntityName(name=n, aliases=sorted(set(aliases.get(n, [])))) for n in sorted(names)]


def build_cards(memory, context, *, approval):
    if approval != 'approved_fixture':
        raise PermissionError('语义卡片实验仅允许显式批准的合成/公共 Fixture')
    doc = memory.document(context)
    latest = [versions[-1] for versions in doc.memories.values()]
    if any(not m.confirmation_ref.startswith('approved_fixture:') for m in latest):
        raise PermissionError('未审核的生产/私人记录不能套用 Fixture 批准')
    by_position = {}
    for m in latest:
        evidence = doc.evidence[m.evidence_refs[0]]
        _, _, start, _ = _body(m)
        if start == 0:
            by_position[(evidence.session_id, evidence.message_ref)] = m
    cards = []
    for m in latest:
        role, scenario, start, body = _body(m)
        evidence = doc.evidence[m.evidence_refs[0]]
        previous = by_position.get((evidence.session_id, evidence.message_ref-1)) if start == 0 else None
        refs = list(m.evidence_refs)
        context_text = ''
        if previous is not None:
            prev_role, _, _, prev_body = _body(previous)
            context_text = f'Context excerpt ({prev_role}): {prev_body[:600]}\n'
            refs += previous.evidence_refs
        business = ' / '.join(doc.topics[t].name for t in m.topic_ids if t in doc.topics)
        content = f'Source business: {business}\n{context_text}Statement ({role}): {body}'
        cards.append(MemoryCard(memory_id=m.id, user_id=m.user_id, content=content,
            entity=_entities(content, business), scenario=scenario,
            valid_time=ValidTime(start=m.effective_from, end=m.valid_until),
            evidence_refs=list(dict.fromkeys(refs)), evidence_type=m.evidence_type,
            confirmation_status='confirmed_fixture', topic_ids=m.topic_ids,
            source_episode_id=m.id, source_version=m.version, source_sha256=_fingerprint(m)))
    return cards


class CardView:
    """每次读取先过原权威层过滤，再投影；卡片不能复活已撤回/过期的来源。"""
    def __init__(self, memory, context, cards):
        self.memory, self.context = memory, memory.sessions.check_access(context)
        self.cards = {}
        for card in cards:
            card = MemoryCard.model_validate(card.model_dump())
            if card.memory_id in self.cards:
                raise ValueError('卡片 ID 重复，禁止静默覆盖')
            self.cards[card.memory_id] = card

    def _check(self, context):
        self.memory.sessions.check_access(context)
        if context != self.context:
            raise PermissionError('卡片视图绑定固定会话及 owner')

    def document(self, context):
        self._check(context)
        return self.memory.document(context)

    def records(self, context, *, as_of, scenario_ids=None):
        self._check(context)
        doc = self.memory.document(context)
        result = []
        for source in self.memory.records(context, as_of=as_of, scenario_ids=scenario_ids):
            card = self.cards.get(source.id)
            if (card is None or card.user_id != str(context.owner_id)
                    or card.confirmation_status != 'confirmed_fixture'
                    or card.source_episode_id != source.id or card.source_version != source.version
                    or card.source_sha256 != _fingerprint(source)
                    or card.valid_time.start != source.effective_from or card.valid_time.end != source.valid_until):
                continue
            # 邻接上下文也必须仍有合法来源，不能在来源删除后残留。
            if any(ref not in doc.evidence or doc.evidence[ref].user_id != str(context.owner_id)
                   or doc.evidence[ref].observed_at > as_of for ref in card.evidence_refs):
                continue
            result.append(source.model_copy(update={'content': card.content, 'evidence_refs': card.evidence_refs}))
        return result


def entity_hits(view, context, query):
    records, _, _ = eligible_for_query(view, context, query)
    hits = []
    for m in records:
        card = view.cards[m.id]
        matches = [e.name for e in card.entity if any(
            re.search(r'(?<!\w)'+re.escape(name)+r'(?!\w)', query.text, re.I)
            for name in [e.name, *e.aliases])]
        if matches:
            # 冻结的组件实验：精确实体证据优先，名称数量不无限加分。
            hits.append(SearchHit(memory_id=m.id, source='structured_entity_alias',
                score=1.0, reasons=['explicit_entity:'+n for n in matches]))
    return hits
