"""上下文组件评分与精确Gold Oracle；全部实际决策完成后才接触Gold。"""
from datetime import timedelta
import re
from time import perf_counter

from min_agent.long_memory.evaluation import build_approved_fixture,normalized,timestamp
from min_agent.long_memory.models import Query
from min_agent.long_memory.retrieval import eligible_for_query
from min_agent.long_memory.retrieval_quality import experiment_search,body
from min_agent.long_memory.retrieval_experiment import _facts,encoded,pack_context
from min_agent.long_memory.fact_binding import binding_rerank,local_bindings
from min_agent.long_memory.evidence_context import assemble_evidence,evidence_packages


def oracle_bound(nodes,*,budget,max_items=8):
    """每个覆盖mask/条数只保留最低成本；最多8条，不制造新证据。"""
    if not 0<=max_items<=8 or budget<0:raise ValueError('Oracle仅用于有界离线诊断')
    if len({n['memory_id'] for n in nodes})!=len(nodes):raise ValueError('Oracle输入须先按ID去重')
    union=0;states={(0,0):(0,[])}
    for node in nodes:
        mask=node['mask'];union|=mask
        if not mask:continue
        if mask.bit_length()>12:raise ValueError('离线Oracle最多12个必要事实组')
        cost=len(encoded(node['entry']))+1
        for (old,k),(used,ids) in list(states.items()):
            if k>=max_items:continue
            key=(old|mask,k+1);candidate=(used+cost,ids+[node['memory_id']])
            if key not in states or candidate[0]<states[key][0]:states[key]=candidate
    best=(0,0,[]);rank=0
    for (mask,k),(used,ids) in states.items():
        count=mask.bit_count();rank=max(rank,count);size=used+1 if k else 0
        if size<=budget and (count,-size)>(best[0],-best[1]):best=(count,size,ids)
    return dict(unbounded_facts=union.bit_count(),rank_ceiling=rank,budget_ceiling=best[0],
                budget_bytes=best[1],budget_ids=best[2],states=len(states))


def _parts(entry,pool):
    if 'memory_id' in entry:
        identity=entry['memory_id'];text=body(pool[identity]);return identity,[dict(start=0,end=len(text),text=text)]
    return entry['source'],entry['quotes']


def _position(identity,pool,positions):
    return positions[pool[identity].evidence_refs[0]]


def _entry_supports(entry,fact,pool,positions):
    identity,parts=_parts(entry,pool)
    required={(s['conversation_id'],s['message_index']) for s in fact.get('sources',[])}
    return (not required or _position(identity,pool,positions) in required) and any(
        normalized(v) in normalized(q['text']) for v in fact['variants'] for q in parts)


def _span_visible(span,entries,pool,positions):
    for entry in entries:
        identity,parts=_parts(entry,pool)
        if _position(identity,pool,positions)!=(span['conversation_id'],span['message_index']):continue
        match=re.search(r';range=(\d+):',pool[identity].content);offset=int(match[1]) if match else 0
        if any(q['start']+offset<=span['start'] and q['end']+offset>=span['end'] for q in parts):return True
    return False


def _review_scores(reviews,entries,pool,positions):
    values=complete_text=complete=0;details=[]
    for reviewed in reviews:
        value=False;full_text=False;full=False
        for alternative in reviewed['alternatives']:
            anchor=alternative['anchor']
            has_value=_span_visible(anchor,entries,pool,positions)
            value|=has_value
            fields=has_value and all(_span_visible(s,entries,pool,positions) for s in alternative['required'])
            full_text|=fields
            provenance=all(any(
                _position(_parts(e,pool)[0],pool,positions)==(s['conversation_id'],s['message_index'])
                and e.get('scene')==s['conversation_id'] and e.get('time')==timestamp(s['timestamp']).isoformat()
                for e in entries) for s in alternative['required'])
            full|=fields and provenance
        values+=value;complete_text+=full_text;complete+=full
        details.append(dict(fact_id=reviewed['fact_id'],reviewed_value_visible=value,
            required_text_visible=full_text,complete_evidence_visible=full))
    return dict(audited_facts=len(reviews),reviewed_value_facts=values,complete_text_facts=complete_text,
        complete_evidence_facts=complete,full_evidence_retention=complete/values if values else None,
        text_evidence_retention=complete_text/values if values else None,details=details)


def evaluate_context_case(case,*,gold,reviews=(),budget=2048):
    history={k:case[k] for k in ('test_id','category','conversation_histories')}
    memory,ctx,info=build_approved_fixture(history,approval='approved_fixture')
    query=Query(text=case['user_question'],as_of=info['last_time']+timedelta(microseconds=1),top_k=8)
    pool={m.id:m for m in eligible_for_query(memory,ctx,query)[0]};doc=memory.document(ctx)
    positions={}
    for item in pool.values():
        scene=re.match(r'^\[.*?;session=(.*?);range=',item.content)[1]
        for ref in item.evidence_refs:positions[ref]=(scene,doc.evidence[ref].message_ref)
    start=perf_counter();base=experiment_search(memory,ctx,query,improved_ranking=True)
    retrieval_ms=(perf_counter()-start)*1000
    start=perf_counter();bound=binding_rerank(memory,ctx,query,base['ranked']);binding_ms=(perf_counter()-start)*1000
    ranks={'R0':base['ranked'],'R1':bound['ranked']}
    configurations={}
    for ranking,ordered in ranks.items():
        hits=ordered[:8]
        for strategy in ('P0','P1','P2'):
            start=perf_counter();assembly=assemble_evidence(memory,ctx,query,hits,strategy=strategy,budget=budget)
            elapsed=(perf_counter()-start)*1000
            configurations[ranking+'_'+strategy]=dict(top_ids=[h['memory_id'] for h in hits],assembly=assembly,
                assembly_ms=elapsed,ranking=ranking,strategy=strategy)
    legacy_ids,legacy_bytes=pack_context(base['hits'],pool,budget)
    assert legacy_ids==configurations['R0_P0']['assembly']['selected_ids']
    assert legacy_bytes==configurations['R0_P0']['assembly']['bytes']
    assert {h['memory_id'] for h in ranks['R0']}=={h['memory_id'] for h in ranks['R1']}
    # 实际检索、分层和六份上下文到此均已固定。以下仅用于离线分析。
    facts=_facts(gold)
    def legacy_entry(identity):
        m=pool[identity]
        return dict(memory_id=identity,content=m.content,evidence_type=m.evidence_type,evidence_refs=m.evidence_refs)
    def mask(entry):return sum(1<<i for i,f in enumerate(facts) if _entry_supports(entry,f,pool,positions))
    candidate_nodes=[dict(memory_id=h['memory_id'],entry=legacy_entry(h['memory_id']),mask=mask(legacy_entry(h['memory_id']))) for h in base['candidates']]
    candidate_oracle=oracle_bound(candidate_nodes,budget=budget)
    oracle={}
    for ranking,ordered in ranks.items():
        ids=[h['memory_id'] for h in ordered[:8]]
        legacy_nodes=[n for n in candidate_nodes if n['memory_id'] in ids]
        packets=configurations[ranking+'_P1']['assembly']['packages']
        extracted_nodes=[dict(memory_id=p['memory_id'],entry=p['entry'],mask=mask(p['entry'])) for p in packets]
        oracle[ranking]=dict(full_topk=oracle_bound(legacy_nodes,budget=budget),
                             extractive_topk=oracle_bound(extracted_nodes,budget=budget))
    for name,row in configurations.items():
        ids=row['top_ids'];assembly=row['assembly'];entries=assembly['entries']
        matches=[]
        for number,fact in enumerate(facts,1):
            candidates=[n['memory_id'] for n in candidate_nodes if n['mask']>>(number-1)&1]
            top=[i for i in ids if i in candidates]
            visible=[_parts(e,pool)[0] for e in entries if _entry_supports(e,fact,pool,positions)]
            matches.append(dict(fact_id=fact.get('fact_id',f'f{number}'),variants=fact['variants'],
                candidate_ids=candidates,top_ids=top,visible_ids=visible))
        row.update(fact_count=len(facts),candidate_facts=sum(bool(m['candidate_ids']) for m in matches),
            topk_facts=sum(bool(m['top_ids']) for m in matches),visible_facts=sum(bool(m['visible_ids']) for m in matches),
            matches=matches,review=_review_scores(reviews,entries,pool,positions))
        # 绑定错误率只评价审查过的锚点/属性，不把未标注输出默认算对。
        predictions=reviewed_predictions=wrong=0
        for identity in ids:
            pos=_position(identity,pool,positions)
            for prediction in local_bindings(body(pool[identity])):
                predictions+=1
                annotated=[r for r in reviews for a in r['alternatives']
                    if (a['anchor']['conversation_id'],a['anchor']['message_index'])==pos
                    and normalized(prediction['value']) in normalized(a['anchor']['quote'])]
                if annotated:
                    reviewed_predictions+=1
                    wrong+=not any(prediction['attribute']==r['attribute'] for r in annotated)
        row['binding_audit']=dict(predictions=predictions,reviewed=reviewed_predictions,wrong=wrong,
            error_rate=wrong/reviewed_predictions if reviewed_predictions else None,
            coverage=reviewed_predictions/predictions if predictions else None)
    return dict(case_id=case['test_id'],category=case['category'],question=case['user_question'],
        as_of=query.as_of.isoformat(),fact_count=len(facts),retrieval_ms=retrieval_ms,binding_ms=binding_ms,
        candidate_count=len(base['candidates']),candidate_ids=[h['memory_id'] for h in base['candidates']],
        rankings={k:[h['memory_id'] for h in v] for k,v in ranks.items()},binding_features=bound['features'],
        configurations=configurations,oracle=dict(candidate=candidate_oracle,**oracle),
        source_episodes=[m.model_dump(mode='json') for m in pool.values()],
        source_positions={r:dict(conversation_id=p[0],message_index=p[1]) for r,p in positions.items()},
        model_quality='NOT_RUN',provider_tokens='NOT_RUN',judge='NOT_RUN',live_model_calls=0)
