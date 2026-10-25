"""v0.3 离线排序/召回消融；不注册到 Runtime，不修改生产 search。"""
from collections import Counter, defaultdict
from copy import deepcopy
import math
import re

from min_agent.long_memory.models import Query
from min_agent.long_memory.retrieval import _lookup, eligible_for_query, search, terms


STOP = set('''a an the i me my mine we our ours you your yours he she it its they their
this that these those is are am was were be been being do does did have has had will
would can could may might must should please help need want know tell remind sure again
also just all any anything what which when where how why for to of in on at by with
from as about and or but if so up get make while looking now go before after new ready
getting based take care supposed hi hello thanks thank yes okay good great perfect'''.split())

# 通用属性词族，不包含案例 ID、人名、品牌、数值或 Gold 表面形式。
FAMILIES = {
    'identifier': 'number identifier reference confirmation code routing account id',
    'timing': 'time date day schedule scheduled scheduling appointment calendar arrive arrival departure deadline expires expiry until',
    'price': 'cost price bill monthly subscription fee rate charge amount balance asset debt worth payment',
    'coverage': 'cover coverage insurance warranty repair claim damage protection deductible exclude exception',
    'benefit': 'reward point cashback earn benefit',
    'risk': 'prepare concern before ready track finalizing requirement required pending delay',
}
WEIGHTS = dict(query_overlap=2.0, neighbor_overlap=1.0, attribute_match=1.0,
               specific_value=0.75, entity_match=2.0, old_score=0.2, is_question=-0.6)
RRF_K = 60
ANCHORS_PER_ROUTE = 12


def _normal(word):
    if len(word) > 4 and word.endswith('ies'):
        return word[:-3]+'y'
    if len(word) > 4 and word.endswith('s') and not word.endswith('ss'):
        return word[:-1]
    return word


def keywords(text):
    return {_normal(w) for w in terms(text) if w not in STOP}


def body(item):
    match = re.match(r'^\[(?:user|assistant|tool);session=.*?;range=\d+:\d+\] (.*)$', item.content, re.S)
    return match.group(1) if match else item.content


def _phrase(name, text):
    return re.search(r'(?<!\w)'+re.escape(name)+r'(?!\w)', text, re.I) is not None


def _index(memory, context, query):
    pool, _, _ = eligible_for_query(memory, context, query)
    records = {m.id:m for m in pool}
    doc = memory.document(context)
    positions = defaultdict(list)
    location = {}
    for m in pool:
        if not m.evidence_refs:
            continue
        e = doc.evidence.get(m.evidence_refs[0])
        if e is not None and e.user_id == str(context.owner_id) and e.observed_at <= query.as_of:
            location[m.id] = (e.session_id, e.message_ref)
            positions[location[m.id]].append(m.id)
    neighbors = {}
    for identity, (session, number) in location.items():
        neighbors[identity] = sorted({i for offset in (-1,1)
            for i in positions.get((session,number+offset),[])})
    texts = {i:body(m) for i,m in records.items()}
    token_sets = {i:keywords(text) for i,text in texts.items()}
    frequencies = Counter(word for tokens in token_sets.values() for word in tokens)
    idf = {word:math.log((len(pool)+1)/(count+1))+1 for word,count in frequencies.items()}
    return records, texts, token_sets, neighbors, idf


def _profile(text, texts):
    tokens = keywords(text)
    attributes = [kind for kind,words in FAMILIES.items() if tokens & keywords(words)]
    entities = sorted({w for w in re.findall(r'\b[A-Z][A-Za-z0-9-]+\b',text) if w.casefold() not in STOP})
    bindings = []
    expansions = set(tokens)
    for kind in attributes:
        expansions |= keywords(FAMILIES[kind])
    for identity, source in texts.items():
        for name, alias in re.findall(r'\b([A-Z][A-Za-z]+(?: [A-Z][A-Za-z]+){0,3})\s*\(([A-Z][A-Z0-9]{1,9})\)',source):
            if _phrase(name,text) or _phrase(alias,text):
                bindings.append(dict(source_id=identity,name=name,alias=alias))
                expansions |= keywords(name+' '+alias)
                entities += [name,alias]
    return dict(keywords=sorted(tokens),attributes=attributes,entities=sorted(set(entities)),
                expanded_terms=sorted(expansions),alias_bindings=bindings)


def _overlap(wanted, present, idf):
    return sum(idf.get(w,1) for w in sorted(wanted & present))/sum(idf.get(w,1) for w in sorted(wanted)) if wanted else 0.0


def _features(identity, score, profile, texts, tokens, neighbors, idf):
    wanted = set(profile['keywords'])
    text = texts[identity]
    neighbor_ids = neighbors.get(identity,[])
    local_overlap = _overlap(wanted,tokens[identity],idf)
    adjacent_overlap = max((_overlap(wanted,tokens[i],idf) for i in neighbor_ids),default=0)
    context_tokens = tokens[identity] | set().union(*(tokens[i] for i in neighbor_ids))
    supported_attributes = [k for k in profile['attributes'] if context_tokens & keywords(FAMILIES[k])]
    is_question = text.rstrip().endswith('?') or text.rstrip().endswith('？')
    # 数值/否定只是“可能含具体约束”的信号，绝不作为事实验证或写入授权。
    has_value = bool(re.search(r'\d|\b(?:no|not|never|without|except|until|requires)\b|不要|禁止',text,re.I))
    entity = any(_phrase(name,text) for name in profile['entities'])
    entity |= any(_phrase(name,texts[i]) for name in profile['entities'] for i in neighbor_ids)
    relevance = max(local_overlap,adjacent_overlap,float(entity))
    features = dict(query_overlap=local_overlap,neighbor_overlap=adjacent_overlap,
        attribute_match=float(bool(supported_attributes) and has_value),
        specific_value=float(has_value)*relevance,entity_match=float(entity),
        old_score=score,is_question=float(is_question))
    features['total'] = sum(WEIGHTS[k]*v for k,v in features.items())
    features['neighbor_ids'] = neighbor_ids
    features['matched_attributes'] = supported_attributes
    return features


def _improved_pool(original, records, texts, tokens, neighbors, idf, profile, query):
    expanded, backend = _lookup(list(records.values()),' '.join(profile['expanded_terms']),use_fts=True)
    expanded = sorted(expanded,key=lambda x:(-x[1],x[0]))
    routes = [list(original['diagnostic']['merged_candidates']),
              [dict(memory_id=i,score=s) for i,s in expanded[:query.max_candidates]]]
    anchors = []
    for route in routes:
        ordered = sorted(route,key=lambda h:(-_overlap(set(profile['keywords']),tokens[h['memory_id']],idf),h['memory_id']))
        anchors += [h['memory_id'] for h in ordered[:ANCHORS_PER_ROUTE]
                    if _overlap(set(profile['keywords']),tokens[h['memory_id']],idf)>0]
    neighbor_ids = sorted({i for anchor in anchors for i in neighbors.get(anchor,[])})
    # 每个召回路径的同一个 ID 只计一次；RRF 只决定候选 cap 的成员。
    route_ids = [[h['memory_id'] for h in route] for route in routes] + [neighbor_ids]
    rrf = defaultdict(float)
    paths = defaultdict(list)
    for source, ids in zip(('original','expanded_fts','event_neighbor'),route_ids):
        for rank, identity in enumerate(dict.fromkeys(ids),1):
            rrf[identity] += 1/(RRF_K+rank)
            paths[identity].append(dict(source=source,rank=rank,rrf=1/(RRF_K+rank)))
    selected = sorted(rrf,key=lambda i:(-rrf[i],i))[:query.max_candidates]
    original_scores = {h['memory_id']:h for h in original['diagnostic']['merged_before_cap']}
    old_global = dict(_lookup(list(records.values()),query.text,use_fts=True)[0])
    pool = []
    for identity in selected:
        old = original_scores.get(identity)
        pool.append(dict(memory_id=identity,score=old['score'] if old else old_global.get(identity,0.0),
            paths=deepcopy(old['paths']) if old else [],reasons=deepcopy(old['reasons']) if old else ['new_recall_only']))
    return pool, dict(expanded_backend=backend,expanded_fts_matches=len(expanded),
        expanded_fts_used=min(len(expanded),query.max_candidates),anchor_ids=sorted(set(anchors)),
        neighbor_ids=neighbor_ids,fusion_paths=dict(paths),rrf_membership_scores=dict(rrf),
        pre_cap_count=len(rrf),cap=query.max_candidates)


def experiment_search(memory, context, query, *, improved_candidates=False, improved_ranking=False):
    query = Query.model_validate(query.model_dump())
    original = search(memory,context,query,inspect=True)
    if not improved_candidates and not improved_ranking:
        candidates=deepcopy(original['diagnostic']['merged_candidates'])
        return dict(hits=deepcopy(original['hits']),candidates=candidates,ranked=deepcopy(candidates),
            ranking_features={},trace=dict(original_candidate_count=len(candidates),
                eligible_count=len(original['diagnostic']['eligible_ids']),
                pre_cap_count=len(original['diagnostic']['merged_before_cap']),cap=query.max_candidates,
                improved_candidates=False,improved_ranking=False),original_trace=original['trace'])
    records,texts,tokens,neighbors,idf = _index(memory,context,query)
    profile = _profile(query.text,texts)
    candidates = deepcopy(original['diagnostic']['merged_candidates'])
    trace = dict(profile,original_candidate_count=len(candidates),eligible_count=len(records),
        pre_cap_count=len(original['diagnostic']['merged_before_cap']),cap=query.max_candidates,
        improved_candidates=improved_candidates,improved_ranking=improved_ranking)
    if improved_candidates:
        candidates, extra = _improved_pool(original,records,texts,tokens,neighbors,idf,profile,query)
        trace.update(extra)
    features = {}
    if improved_ranking:
        features = {h['memory_id']:_features(h['memory_id'],h['score'],profile,texts,tokens,neighbors,idf) for h in candidates}
        ranked = sorted(candidates,key=lambda h:(-features[h['memory_id']]['total'],h['memory_id']))
    else:
        ranked = sorted(candidates,key=lambda h:(-h['score'],h['memory_id']))
    hits = deepcopy(ranked[:query.top_k])
    # 原 score 始终保留；实验排序的总分单独存放，避免混淆多路径融合分数。
    return dict(hits=hits,candidates=candidates,ranked=ranked,ranking_features=features,trace=trace,
                original_trace=original['trace'])
