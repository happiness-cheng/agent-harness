"""逐字证据包及预算选择；不接收Gold、不写Runtime或长期记忆。"""
import re

from min_agent.long_memory.fact_binding import sentences,local_bindings,requested_attributes,source_scope,QUALIFIER
from min_agent.long_memory.retrieval import eligible_for_query
from min_agent.long_memory.retrieval_quality import body,keywords
from min_agent.long_memory.retrieval_experiment import encoded


def evidence_packages(memory,context,query,hits):
    pool={m.id:m for m in eligible_for_query(memory,context,query)[0]};doc=memory.document(context)
    packages=[];seen=set();wanted=keywords(query.text);attributes=requested_attributes(query.text)
    for rank,hit in enumerate(hits):
        identity=hit['memory_id']
        if identity not in pool or identity in seen:continue
        seen.add(identity);item=pool[identity];text=body(item);units=sentences(text)
        selected={i for i,u in enumerate(units) if keywords(u['text']) & wanted
                  or any(b['attribute'] in attributes for b in local_bindings(u['text']))}
        if not selected:selected=set(range(len(units)))
        required={i for i,u in enumerate(units) if QUALIFIER.search(u['text']) or
                  re.search(r"\b(?:Mr|Mrs|Ms|Dr)\.?\s+[A-Z]|\b(?:my name is|this is|I'm)\b",u['text'],re.I)}
        selected|=required
        # 指代依赖只补同一原消息前句，仍逐字引用；不把邻消息号码归属过来。
        for i in sorted(selected,reverse=True):
            if i and re.match(r'^(?:it|its|that|this|they|he|she|these|those)\b',units[i]['text'],re.I):selected.add(i-1)
        quotes=[units[i] for i in sorted(selected)]
        assert all(text[q['start']:q['end']]==q['text'] for q in quotes)
        scope=source_scope(doc,item,query.as_of)
        entry=dict(source=identity,**scope,time=item.observed_at.isoformat(),evidence_type=item.evidence_type,
                   evidence=item.evidence_refs,quotes=quotes)
        if item.effective_from!=item.observed_at:entry['valid_from']=item.effective_from.isoformat()
        if item.valid_until is not None:entry['valid_until']=item.valid_until.isoformat()
        bindings=[b for q in quotes for b in local_bindings(q['text'])]
        keys={(scope['scene'],tuple(scope['entity_scope']),b['attribute'],b['value'],b['status'])
              for b in bindings if b['attribute'] in attributes and b['status']=='asserted'}
        packages.append(dict(memory_id=identity,rank=rank,entry=entry,
            legacy_entry=dict(memory_id=identity,content=item.content,evidence_type=item.evidence_type,evidence_refs=item.evidence_refs),
            required_spans=[units[i] for i in sorted(required)],
            complete=required<=selected,utility=1/(rank+1),binding_keys=sorted(keys),
            query_terms=sorted(set().union(*(keywords(q['text']) for q in quotes)) & wanted),
            redundancy_key=(scope['scene'],item.observed_at.isoformat(),tuple(q['text'] for q in quotes))))
    return packages


def assemble_evidence(memory,context,query,hits,*,strategy='P0',budget=2048):
    if strategy not in {'P0','P1','P2'} or type(budget) is not int or budget<0:
        raise ValueError('未知装配策略或预算')
    packages=evidence_packages(memory,context,query,hits)
    if strategy=='P2' and len(packages)>8:
        raise ValueError('子集装配实验最多处理8个去重来源，不做无界穷举')
    def entries(ids):return [p['legacy_entry'] if strategy=='P0' else p['entry'] for p in ids]
    def size(items):return len(encoded(items)) if items else 0
    selected=[]
    if strategy in {'P0','P1'}:
        for p in packages:
            if p['complete'] and size(entries(selected+[p]))<=budget:selected.append(p)
    else:
        best=None;wanted=keywords(query.text)
        for mask in range(1<<len(packages)):
            choice=[p for i,p in enumerate(packages) if mask>>i&1]
            if any(not p['complete'] for p in choice):continue
            cost=size(entries(choice))
            if cost>budget:continue
            unique={p['redundancy_key']:p for p in choice}
            keys={tuple((k[0],tuple(k[1]),*k[2:])) for p in choice for k in p['binding_keys']}
            terms={t for p in choice for t in p['query_terms']}
            score=sum(p['utility'] for p in unique.values())+0.5*len(keys)+0.1*(len(terms)/len(wanted) if wanted else 0)
            comparison=(score,-cost,tuple(-p['rank'] for p in choice))
            if best is None or comparison>best:
                best=comparison;selected=choice
    result=entries(selected)
    return dict(entries=result,bytes=size(result),selected_ids=[p['memory_id'] for p in selected],
        mechanical_complete=all(p['complete'] for p in selected),packages=packages,
        strategy=strategy,budget=budget)
