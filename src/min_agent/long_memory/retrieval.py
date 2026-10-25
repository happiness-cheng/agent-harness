"""先授权过滤，再多组/FTS/图检索；按ID融合后才截取Top-K。"""
from collections import Counter
import re
import sqlite3

from min_agent.long_memory.models import Query, SearchHit


def terms(text):
    # v0.1使用透明的英文词/单汉字分词；不是语义检索，限制在报告中保留。
    return set(re.findall(r'[a-z0-9_]+|[\u3400-\u9fff]',text.casefold()))


def similarity(left,right):
    tokens=terms(left)
    return len(tokens & terms(right))/len(tokens) if tokens else 0.0


def fuse_hits(hits, *, top_k):
    combined={}
    for hit in hits:
        hit=SearchHit.model_validate(hit.model_dump())
        row=combined.setdefault(hit.memory_id,dict(memory_id=hit.memory_id,score=hit.score,paths=[],reasons=[]))
        row['score']=max(row['score'],hit.score)  # 不因属于多个组而累加高分。
        path=dict(source=hit.source,topic_id=hit.topic_id,score=hit.score)
        if path not in row['paths']: row['paths'].append(path)
        row['reasons']=sorted(set(row['reasons'])|set(hit.reasons))
    return sorted(combined.values(),key=lambda r:(-r['score'],r['memory_id']))[:top_k]


def _lookup(records,text,*,use_fts):
    if not records: return [],'fts5_bm25' if use_fts else 'lexical_fallback'
    query_terms=sorted(terms(text))[:80]
    if not query_terms: return [],'lexical_fallback'
    if use_fts:
        con=sqlite3.connect(':memory:')
        try:
            con.execute('CREATE VIRTUAL TABLE memory_fts USING fts5(identity UNINDEXED, content)')
            con.executemany('INSERT INTO memory_fts VALUES (?,?)',
                            [(m.id,' '.join(sorted(terms(m.content)))) for m in records])
            expression=' OR '.join('"'+word+'"' for word in query_terms)
            rows=con.execute('SELECT identity,bm25(memory_fts) FROM memory_fts WHERE memory_fts MATCH ? ORDER BY bm25(memory_fts),identity',
                             (expression,)).fetchall()
            max_rank=max((-r[1] for r in rows),default=1.0) or 1.0
            by_id={m.id:m for m in records}
            return [(identity,0.8*similarity(text,by_id[identity].content)+0.2*(-rank/max_rank))
                    for identity,rank in rows],'fts5_bm25'
        except sqlite3.OperationalError:
            pass
        finally: con.close()
    return [(m.id,score) for m in records if (score:=similarity(text,m.content))>0],'lexical_fallback'


def eligible_for_query(memory,context,query):
    records=memory.records(context,as_of=query.as_of,scenario_ids=query.scenario_ids)
    filtered=Counter()
    specific={m.conflict_key for m in records if m.conflict_key and m.scenario_ids
              and m.lifecycle=='active' and m.evidence_type=='DIRECT'}
    result=[]
    for m in records:
        if m.conflict_key and m.conflict_key in query.directives:
            filtered['current_directive']+=1
        elif m.conflict_key and m.conflict_key in specific and not m.scenario_ids:
            filtered['confirmed_scenario_exception']+=1
        else: result.append(m)
    return result,filtered,len(records)


def search(memory,context,query,*,use_fts=True,extra_hits=()):
    query=Query.model_validate(query.model_dump())
    doc=memory.document(context)
    pool,filtered,qualified_count=eligible_for_query(memory,context,query)
    eligible={m.id:m for m in pool}
    filtered['authority_time_lifecycle_scope']=len(doc.memories)-qualified_count
    topic_ids={t for m in pool for t in m.topic_ids}
    routed=[]
    for topic in doc.topics.values():
        if topic.id not in topic_ids or topic.created_at>query.as_of: continue
        score=similarity(query.text,topic.name+' '+topic.summary+' '+' '.join(topic.keywords))
        if score>0: routed.append((score,topic.id))
    routed.sort(key=lambda item:(-item[0],item[1]))
    routes=routed[:query.top_g]
    hits=[]; backend='fts5_bm25' if use_fts else 'lexical_fallback'
    for _,topic in routes:
        rows,mode=_lookup([m for m in pool if topic in m.topic_ids],query.text,use_fts=use_fts)
        if mode=='lexical_fallback': backend=mode
        hits.extend(SearchHit(memory_id=identity,source='group_'+mode,score=score,topic_id=topic,
                              reasons=['group_match']) for identity,score in rows)
    fallback=not routes or not hits or routes[0][0]<query.route_min_score
    if fallback:
        rows,backend=_lookup(pool,query.text,use_fts=use_fts)
        hits.extend(SearchHit(memory_id=identity,source='global_'+backend,score=score,
                              reasons=['global_fallback']) for identity,score in rows)
    for hit in extra_hits:
        if hit.memory_id in eligible: hits.append(hit)
        else: filtered['unauthorized_external_hit']+=1
    initial=fuse_hits(hits,top_k=query.max_candidates)
    frontier={h['memory_id'] for h in initial}
    seen=set(frontier)
    expanded=0
    for depth in range(query.graph_hops):
        next_frontier=set()
        for edge in doc.edges.values():
            if (edge.status!='confirmed' or edge.observed_at>query.as_of
                    or edge.type not in {'RELATED_TO','EXCEPTION_OF','GENERALIZES'}): continue
            other=(edge.target_id if edge.source_id in frontier else edge.source_id if edge.target_id in frontier else None)
            if other is None or other not in eligible or other in seen: continue
            if len(seen)>=query.max_candidates: break
            hits.append(SearchHit(memory_id=other,source='graph',score=0.05/(depth+1),reasons=['authorized_neighbor']))
            next_frontier.add(other); seen.add(other); expanded+=1
        frontier=next_frontier
    unique=fuse_hits(hits,top_k=query.max_candidates)
    return dict(hits=unique[:query.top_k],trace=dict(
        document_revision=doc.revision,backend=backend,filtered=dict(filtered),
        routed_topics=[t for _,t in routes],omitted_topic_count=max(0,len(routed)-len(routes)),
        global_fallback=fallback,raw_hit_count=len(hits),unique_candidate_count=len(unique),
        duplicate_hit_count=len(hits)-len({h.memory_id for h in hits}),graph_expanded=expanded,
        candidate_cap=query.max_candidates,candidate_cap_reached=len(unique)>=query.max_candidates,
        final_ids=[r['memory_id'] for r in unique[:query.top_k]],as_of=query.as_of.isoformat()))
