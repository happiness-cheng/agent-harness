"""独立、未确认的原子卡片实验；字段逐字引用原文，不写生产 Memory。"""
from hashlib import sha256
from typing import Literal
from uuid import uuid5

from pydantic import BaseModel,ConfigDict,Field

from min_agent.long_memory.cards import ValidTime
from min_agent.long_memory.retrieval import eligible_for_query
from min_agent.long_memory.retrieval_quality import body


class QuoteRef(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)
    episode_id:str
    start:int=Field(ge=0)
    end:int=Field(gt=0)
    quote:str=Field(min_length=1)


class AtomicCard(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)
    memory_id:str
    user_id:str
    subject:str
    attribute:str
    value:str
    scenario:str
    valid_time:ValidTime
    evidence_refs:list[str]
    source_quotes:dict[str,QuoteRef]
    source_hashes:dict[str,str]
    claim_kind:Literal['explicit','inferred']
    evidence_type:Literal['DIRECT','INFERRED']
    confirmation_status:Literal['pending']='pending'


def build_atomic_card(memory,context,query,*,subject,attribute,value,approval,claim_kind='inferred'):
    if approval!='approved_fixture':
        raise PermissionError('仅允许已批准公共/合成 Fixture 的原子卡实验')
    if claim_kind not in {'explicit','inferred'}:
        raise ValueError('未知事实标记')
    memory.sessions.check_access(context)
    pool={m.id:m for m in eligible_for_query(memory,context,query)[0]}
    doc=memory.document(context)
    refs={key:QuoteRef.model_validate(ref.model_dump()) for key,ref in
          dict(subject=subject,attribute=attribute,value=value).items()}
    selected=[];evidence=[];events=set();hashes={}
    for key,ref in refs.items():
        source=pool.get(ref.episode_id)
        if source is None or not source.confirmation_ref.startswith('approved_fixture:'):
            raise PermissionError('原子字段来源未批准、越权、未来或已撤回')
        text=body(source)
        if ref.start>=ref.end or ref.end>len(text) or text[ref.start:ref.end]!=ref.quote:
            raise ValueError('字段不是原文逐字片段；拒绝补造缺失事实')
        selected.append(source)
        hashes[source.id]=sha256(source.model_dump_json().encode()).hexdigest()
        for identity in source.evidence_refs:
            e=doc.evidence.get(identity)
            if e is None or e.user_id!=str(context.owner_id) or e.observed_at>query.as_of:
                raise PermissionError('字段证据无效')
            evidence.append(identity);events.add(e.session_id)
    if len(events)!=1:
        raise ValueError('不同事件不能因同名/相似取值自动关联')
    if claim_kind=='explicit' and (len(hashes)!=1 or any(m.evidence_type!='DIRECT' for m in selected)):
        raise ValueError('跨句或推断来源不能自动升级为明确用户事实')
    start=max(max(m.effective_from,m.observed_at,m.confirmed_at,m.updated_at) for m in selected)
    ends=[m.valid_until for m in selected if m.valid_until is not None]
    identity=str(uuid5(context.owner_id,'atomic:'+':'.join(
        f'{k}:{r.episode_id}:{r.start}:{r.end}' for k,r in refs.items())))
    return AtomicCard(memory_id=identity,user_id=str(context.owner_id),
        subject=subject.quote,attribute=attribute.quote,value=value.quote,scenario=next(iter(events)),
        valid_time=ValidTime(start=start,end=min(ends) if ends else None),
        evidence_refs=list(dict.fromkeys(evidence)),source_quotes=refs,source_hashes=hashes,
        claim_kind=claim_kind,evidence_type='DIRECT' if claim_kind=='explicit' else 'INFERRED')


def validate_atomic_card(memory,context,query,card):
    try:
        card=AtomicCard.model_validate(card.model_dump())
        if card.user_id!=str(context.owner_id):
            return False
        rebuilt=build_atomic_card(memory,context,query,**card.source_quotes,
            approval='approved_fixture',claim_kind=card.claim_kind)
        return rebuilt==card
    except (PermissionError,ValueError,KeyError):
        return False
