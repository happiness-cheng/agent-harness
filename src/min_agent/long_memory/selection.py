"""可解释的importance/relevance/placement分离；所有策略共用资格与预算。"""
from datetime import datetime
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from min_agent.long_memory.models import encode_tokens_upper_bound
from min_agent.long_memory.retrieval import similarity, eligible_for_query


class SelectionPolicy(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    goal_weight: float = Field(default=30,ge=0)
    decision_cost_weight: float = Field(default=20,ge=0)
    broad_weight: float = Field(default=8,ge=0)
    direct_weight: float = Field(default=8,ge=0)
    stable_weight: float = Field(default=5,ge=0)
    relevance_weight: float = Field(default=10,ge=0)
    disputed_penalty: float = Field(default=30,ge=0)
    stable_fraction: float = Field(default=0.35,ge=0,le=1)


def serialized(view): return json.dumps(view,ensure_ascii=False,separators=(',',':'),sort_keys=True)


def entry(memory):
    return dict(id=memory.id,version=memory.version,content=memory.content,
        evidence_type=memory.evidence_type,evidence_refs=memory.evidence_refs,
        scope=memory.scope,scenario_ids=memory.scenario_ids,lifecycle=memory.lifecycle,
        observed_at=memory.observed_at.isoformat(),effective_from=memory.effective_from.isoformat(),
        valid_until=memory.valid_until.isoformat() if memory.valid_until else None)


def importance(memory,goals,policy):
    active=[goals[g].priority for g in memory.goal_ids if g in goals and goals[g].lifecycle=='active']
    signals=dict(explicit_active_goal=max(active,default=0)*policy.goal_weight,
        decision_cost=memory.decision_cost*policy.decision_cost_weight,
        broad_scope=policy.broad_weight if memory.breadth=='broad' else 0,
        direct_evidence=policy.direct_weight if memory.evidence_type=='DIRECT' else 0,
        stable=policy.stable_weight if memory.stable else 0,
        disputed=-policy.disputed_penalty if memory.lifecycle=='disputed' else 0)
    return sum(signals.values()),signals


def rank_for_core(service,context,query,*,strategy='GOAL_VALUE',budget=2048,policy=None,visible_versions=()):
    if strategy not in {'NONE','RECENCY','FREQUENCY','GOAL_VALUE'}: raise ValueError('未知选择策略')
    if type(budget) is not int or budget<0: raise ValueError('预算必须为非负整数')
    policy=SelectionPolicy.model_validate((policy or SelectionPolicy()).model_dump())
    doc=service.document(context); goals=service.goals(context,as_of=query.as_of)
    pool,_,_=eligible_for_query(service,context,query)
    scores={}; reasons={}; visible=set(visible_versions)
    for m in pool:
        weight,signals=importance(m,goals,policy)
        relevance=similarity(query.text,m.content)
        evidence={r for r in m.evidence_refs if r in doc.evidence and doc.evidence[r].observed_at<=query.as_of}
        usage={u['event_id'] for u in doc.usages if u['memory_id']==m.id and datetime.fromisoformat(u['at'])<=query.as_of}
        frequency=len(evidence)+len(usage)
        placement=(m.observed_at.timestamp() if strategy=='RECENCY' else frequency if strategy=='FREQUENCY'
                   else weight+policy.relevance_weight*relevance)
        scores[m.id]=dict(importance=weight,importance_signals=signals,relevance=relevance,
                          frequency=frequency,placement=placement,estimated_units=encode_tokens_upper_bound(serialized(entry(m))))
        reasons[m.id]=[strategy.lower(),'confirmed','time_valid']+(['active_goal'] if signals['explicit_active_goal'] else [])
    ranked=sorted(pool,key=lambda m:(-scores[m.id]['placement'],m.content,m.id))
    view=dict(stable_core=[],active_core=[]); selected=[]; omitted={}
    if strategy!='NONE' and budget>0:
        for lane in ('stable_core','active_core'):
            for m in ranked:
                stable=(m.stable and m.breadth=='broad' and m.evidence_type=='DIRECT' and m.lifecycle=='active')
                if stable!=(lane=='stable_core'): continue
                if (m.id,m.version) in visible:
                    omitted[m.id]='already_visible';continue
                value=entry(m)
                view[lane].append(value)
                size=encode_tokens_upper_bound(serialized(view))
                lane_size=encode_tokens_upper_bound(serialized(view[lane]))
                if size>budget or (lane=='stable_core' and lane_size>budget*policy.stable_fraction):
                    view[lane].pop();omitted[m.id]='budget';continue
                selected.append(m.id)
    size=encode_tokens_upper_bound(serialized(view)) if selected else 0
    return dict(view=view if selected else {},selected_ids=selected,pool_ids=sorted(m.id for m in pool),
        ranked_ids=[m.id for m in ranked],scores=scores,reason_codes=reasons,omitted=omitted,
        bytes_upper_bound=size,budget=budget,budget_unit='utf8_bytes_token_upper_bound_not_exact',
        policy=policy.model_dump(),strategy=strategy)


def assemble_context(service,context,query,*,strategy='GOAL_VALUE',budget=4096,visible_versions=(),policy=None,requested_ids=()):
    if type(budget) is not int or budget<0: raise ValueError('预算必须为非负整数')
    core=rank_for_core(service,context,query,strategy=strategy,budget=budget//2,
                       visible_versions=visible_versions,policy=policy)
    search=service.search(context,query)
    eligible={m.id:m for m in eligible_for_query(service,context,query)[0]}
    visible=set(visible_versions)
    view=dict(reference_only=True,current_request_takes_precedence=True,
        precedence=['current_directive','confirmed_scenario_exception','historical_preference'],
        stable_core=core['view'].get('stable_core',[]),active_core=core['view'].get('active_core',[]),
        retrieved=[],root_index=[])
    included=set(core['selected_ids']);metadata_support=set()
    if encode_tokens_upper_bound(serialized(view))>budget:
        view={};included=set()
    if view and strategy!='NONE':
        order=list(dict.fromkeys(list(requested_ids)+[h['memory_id'] for h in search['hits']]))
        for identity in order:
            m=eligible.get(identity)
            if m is None or identity in included or (m.id,m.version) in visible: continue
            view['retrieved'].append(entry(m))
            if encode_tokens_upper_bound(serialized(view))>budget:
                view['retrieved'].pop();continue
            included.add(identity)
        # 索引只披露已确认且时点有效的组/场景元数据，不提前注入尚未匹配的场景正文。
        index_records=service.records(context,as_of=query.as_of)
        topic_ids={t for m in index_records for t in m.topic_ids}
        document=service.document(context)
        for topic in document.topics.values():
            if topic.id not in topic_ids or topic.created_at>query.as_of: continue
            scenes={s for m in index_records if topic.id in m.topic_ids for s in m.scenario_ids}
            view['root_index'].append(dict(id=topic.id,name=topic.name,
                scenarios=[dict(id=s,label=document.scenarios[s].label,conditions=document.scenarios[s].conditions) for s in sorted(scenes)
                           if s in document.scenarios and document.scenarios[s].created_at<=query.as_of]))
            if encode_tokens_upper_bound(serialized(view))>budget: view['root_index'].pop()
            else: metadata_support.update(m.id for m in index_records if topic.id in m.topic_ids)
    if not included and not view.get('root_index'): view={}
    return dict(view=view,trace=dict(injected_ids=sorted(included),
        metadata_support_ids=sorted(metadata_support),
        injected_versions={identity:eligible[identity].version for identity in included},
        bytes_upper_bound=encode_tokens_upper_bound(serialized(view)) if view else 0,
        budget=budget,budget_unit='utf8_bytes_token_upper_bound_not_exact',
        strategy=strategy,core_scores=core['scores'],core_reason_codes=core['reason_codes'],
        search=search['trace']))
