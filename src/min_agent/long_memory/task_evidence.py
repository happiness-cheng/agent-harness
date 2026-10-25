"""实验首轮取证：复用候选/事实绑定/原句片段，不读取Gold或调整Core权重。"""
from time import perf_counter
import re

from min_agent.long_memory.answer_eval import encoded
from min_agent.long_memory.evidence_context import evidence_packages
from min_agent.long_memory.fact_binding import binding_rerank,local_bindings,requested_attributes,sentences
from min_agent.long_memory.retrieval import eligible_for_query
from min_agent.long_memory.retrieval_quality import body,experiment_search,keywords

POLICY_VERSION='task_evidence_v1_frozen'


def decorate_entry(memory,context,query,item,entry):
    """确认是写入资格，不等于历史助手的话已经被现实系统核实。"""
    doc=memory.document(context)
    sources=[doc.evidence[r] for r in item.evidence_refs if r in doc.evidence
             and doc.evidence[r].user_id==str(context.owner_id) and doc.evidence[r].observed_at<=query.as_of]
    return dict(entry,role=sources[0].role if sources else 'unknown',
        confirmation_status=item.confirmation_status,
        truth_status='historical_statement_not_live_verified')


def evidence_view(entries):
    return dict(reference_only=True,current_request_takes_precedence=True,
                coverage='partial_view_not_exhaustive_history',evidence=entries) if entries else {}


def select_task_evidence(memory,context,query,*,budget=4096):
    if type(budget) is not int or budget<0:raise ValueError('预算必须为非负整数')
    started=perf_counter();memory.reconcile_deletions(context)
    pool={m.id:m for m in eligible_for_query(memory,context,query)[0]}
    result=experiment_search(memory,context,query,improved_candidates=True,improved_ranking=True)
    ranked=binding_rerank(memory,context,query,result['ranked'])['ranked']
    wanted=keywords(query.text);attributes=set(requested_attributes(query.text));hits=[]
    for hit in ranked:
        item=pool.get(hit['memory_id'])
        if item is None:continue
        text=body(item);features=result['ranking_features'].get(item.id,{})
        local=bool(keywords(text)&wanted)
        bound=any(b['attribute'] in attributes for b in local_bindings(text))
        contextual_value=bool(re.search(r'\d',text)) and features.get('neighbor_overlap',0)>0
        if local or bound or contextual_value:hits.append(hit)
    hits=hits[:query.top_k]
    packages=evidence_packages(memory,context,query,hits)
    # 保持v04的小规模原句子集搜索；新增角色等元数据也一起计入同一4096字节预算。
    if len(packages)>8:raise ValueError('实验装配只允许Top8，避免无界子集枚举')
    for p in packages:
        item=pool[p['memory_id']]
        # 原装配器不识别verify一类核实限定；在当前消息内补原句，不推断其真假。
        quotes=p['entry']['quotes']
        for unit in sentences(body(item)):
            if re.search(r'\b(?:verify|confirm|unverified|unknown|estimate\w*|caution)\b|待核实|未确认|预计|核实',unit['text'],re.I):
                if unit not in quotes:quotes.append(unit)
        quotes.sort(key=lambda q:q['start'])
        p['entry']=decorate_entry(memory,context,query,item,p['entry'])
    best=None;view={};selected=[]
    for mask in range(1<<len(packages)):
        choice=[p for i,p in enumerate(packages) if mask>>i&1]
        if any(not p['complete'] for p in choice):continue
        proposal=evidence_view([p['entry'] for p in choice]);size=len(encoded(proposal)) if proposal else 0
        if size>budget:continue
        unique={p['redundancy_key']:p for p in choice}
        keys={str(k) for p in choice for k in p['binding_keys']}
        matched={t for p in choice for t in p['query_terms']}
        utility=sum(p['utility'] for p in unique.values())+.5*len(keys)+.1*(len(matched)/len(wanted) if wanted else 0)
        comparison=(utility,-size,tuple(-p['rank'] for p in choice))
        if best is None or comparison>best:best=comparison;view=proposal;selected=choice
    return dict(view=view,bytes=len(encoded(view)) if view else 0,
        trace=dict(policy=POLICY_VERSION,profile={k:result['trace'].get(k) for k in ['keywords','attributes','entities','expanded_terms']},
            candidate_count=len(result['candidates']),candidate_ids=[h['memory_id'] for h in result['candidates']],
            ranked_ids=[h['memory_id'] for h in hits],selected_ids=[p['memory_id'] for p in selected],
            budget=budget,core_reserved_bytes=0,latency_ms=(perf_counter()-started)*1000,
            mechanical_complete=all(p['complete'] for p in selected)))
