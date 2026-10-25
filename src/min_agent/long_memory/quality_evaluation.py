"""原始 Episode 的四组实验；查询与排序完成以后才读取 Gold。"""
from datetime import timedelta
from hashlib import sha256
import re
from statistics import median
from time import perf_counter

from min_agent.long_memory.evaluation import build_approved_fixture
from min_agent.long_memory.models import Query
from min_agent.long_memory.retrieval import eligible_for_query
from min_agent.long_memory.retrieval_experiment import _facts,_supports,encoded,pack_context
from min_agent.long_memory.retrieval_quality import experiment_search,body


def evaluate_quality_case(case,*,gold,budget=2048):
    if type(budget) is not int or budget<0:
        raise ValueError('上下文预算需非负整数')
    history={k:case[k] for k in ('test_id','category','conversation_histories')}
    memory,ctx,info=build_approved_fixture(history,approval='approved_fixture')
    query=Query(text=case['user_question'],as_of=info['last_time']+timedelta(microseconds=1),top_k=8)
    pool={m.id:m for m in eligible_for_query(memory,ctx,query)[0]}
    doc=memory.document(ctx)
    positions={}
    for item in pool.values():
        scenario=re.match(r'^\[.*?;session=(.*?);range=',item.content).group(1)
        for reference in item.evidence_refs:
            positions[reference]=(scenario,doc.evidence[reference].message_ref)
    groups={}
    for name,recall,ranking in [('A',False,False),('B',False,True),('C',True,False),('D',True,True)]:
        times=[];retrieval=None
        for _ in range(3):
            start=perf_counter()
            result=experiment_search(memory,ctx,query,improved_candidates=recall,improved_ranking=ranking)
            times.append((perf_counter()-start)*1000)
            if retrieval is not None and result!=retrieval:
                raise ValueError('同一输入的候选/排名不确定')
            retrieval=result
        selected,context_bytes=pack_context(retrieval['hits'],pool,budget)
        groups[name]=dict(retrieval=retrieval,visible_ids=selected,context_bytes=context_bytes,
            search_ms=times,median_search_ms=median(times),candidate_count=len(retrieval['candidates']),
            pre_cap_count=retrieval['trace']['pre_cap_count'],top_k=8,context_budget=budget,
            representation='unchanged_raw_episode',model_quality='NOT_RUN',provider_tokens='NOT_RUN',judge='NOT_RUN')
    assert {h['memory_id'] for h in groups['A']['retrieval']['candidates']}=={h['memory_id'] for h in groups['B']['retrieval']['candidates']}
    assert groups['C']['retrieval']['candidates']==groups['D']['retrieval']['candidates']
    # Gold 不影响以上任何代码路径。
    facts=_facts(gold)
    matched={}
    for group,row in groups.items():
        candidates=[h['memory_id'] for h in row['retrieval']['candidates']]
        hits=[h['memory_id'] for h in row['retrieval']['hits']]
        matches=[]
        for n,fact in enumerate(facts,1):
            matches.append(dict(fact_id=fact.get('fact_id',f'f{n}'),
                candidate_ids=[i for i in candidates if _supports(pool[i],fact,positions)],
                topk_ids=[i for i in hits if _supports(pool[i],fact,positions)],
                visible_ids=[i for i in row['visible_ids'] if _supports(pool[i],fact,positions)]))
        candidate_facts=sum(bool(m['candidate_ids']) for m in matches)
        topk_facts=sum(bool(m['topk_ids']) for m in matches)
        visible_facts=sum(bool(m['visible_ids']) for m in matches)
        row.update(fact_count=len(facts),candidate_facts=candidate_facts,topk_facts=topk_facts,
            visible_facts=visible_facts,candidate_recall=candidate_facts/len(facts) if facts else None,
            recall_at_8=topk_facts/len(facts) if facts else None,
            visible_fact_recall=visible_facts/len(facts) if facts else None,
            unique_before_topk=len(set(candidates)),fact_matches=matches)
        matched[group]=matches
    analyses=[]
    for number,fact in enumerate(facts):
        sources=[]
        for identity,item in pool.items():
            if not _supports(item,fact,positions):
                continue
            reference=item.evidence_refs[0]
            conv,index=positions[reference]
            text=body(item)
            sources.append(dict(episode_id=identity,conversation_id=conv,message_index=index,
                observed_at=item.observed_at.isoformat(),topic_ids=item.topic_ids,scenario_ids=item.scenario_ids,
                evidence_refs=item.evidence_refs,evidence_type=item.evidence_type,
                quote=text,source_sha256=sha256(text.encode()).hexdigest()))
        paths={}
        for group,row in groups.items():
            match=matched[group][number]
            if match['visible_ids']:reason='visible'
            elif match['topk_ids']:reason='context_budget'
            elif match['candidate_ids']:reason='ranking_cutoff'
            elif not sources:reason='gold_surface_missing_or_external'
            else:reason='candidate_gap'
            candidates={h['memory_id']:h for h in row['retrieval']['candidates']}
            ranks={h['memory_id']:n+1 for n,h in enumerate(row['retrieval']['ranked'])}
            paths[group]=dict(reason=reason,**match,sources=[dict(
                episode_id=s['episode_id'],rank=ranks.get(s['episode_id']),
                old_score=candidates.get(s['episode_id'],{}).get('score'),
                features=row['retrieval']['ranking_features'].get(s['episode_id']),
                recall_paths=row['retrieval']['trace'].get('fusion_paths',{}).get(s['episode_id'],[])) for s in sources])
        analyses.append(dict(fact_id=fact.get('fact_id',f'f{number+1}'),variants=fact['variants'],
            label=fact.get('label',''),literal_history_support=sources,groups=paths))
    return dict(case_id=case['test_id'],category=case['category'],question=case['user_question'],
        input_history_sha256=sha256(encoded(history)).hexdigest(),as_of=query.as_of.isoformat(),fact_count=len(facts),
        groups=groups,fact_analysis=analyses,source_episodes=[m.model_dump(mode='json') for m in pool.values()],
        source_positions={r:dict(conversation_id=p[0],message_index=p[1]) for r,p in positions.items()},
        live_model_calls=0)
