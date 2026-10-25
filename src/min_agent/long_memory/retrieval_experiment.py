"""检索组件对照与离线归因：Gold 仅在检索完成后参与打分。"""
from datetime import timedelta
from hashlib import sha256
import json
import re
from statistics import median
from time import perf_counter

from min_agent.long_memory.cards import build_cards, CardView, entity_hits
from min_agent.long_memory.evaluation import build_approved_fixture, normalized
from min_agent.long_memory.models import Query
from min_agent.long_memory.retrieval import search, terms


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def _facts(gold):
    return [f if isinstance(f, dict) else {'variants': f if isinstance(f, list) else [f], 'sources': []}
            for f in gold]


def _text(record):
    # 不给来源标签/业务名计事实分；邻接原文是本轮实验的表示变量。
    text = record.content
    if text.startswith('Source business:'):
        return text.split('\n', 1)[-1]
    return text.split('] ', 1)[-1]


def _matches(text, fact):
    return any(normalized(v) in normalized(text) for v in fact['variants'])


def _supports(record, fact, evidence_positions):
    if not _matches(_text(record), fact):
        return False
    required = {(s['conversation_id'], s['message_index']) for s in fact.get('sources', [])}
    return not required or bool(required & {evidence_positions[r] for r in record.evidence_refs})


def pack_context(hits, pool, budget):
    selected = []
    entries = []
    for hit in hits:
        item = pool[hit['memory_id']]
        entry = dict(memory_id=item.id, content=item.content, evidence_type=item.evidence_type,
                     evidence_refs=item.evidence_refs)
        if len(encoded(entries+[entry])) <= budget:
            selected.append(item.id)
            entries.append(entry)
    return selected, len(encoded(entries)) if entries else 0


def _diagnose(facts, original_pool, retrieval, selected, positions, source_messages):
    diagnostic = retrieval['diagnostic']
    initial = {h['memory_id']: h for h in diagnostic['initial_candidates']}
    merged = {h['memory_id']: (i+1, h) for i, h in enumerate(diagnostic['merged_before_cap'])}
    capped = {h['memory_id'] for h in diagnostic['merged_candidates']}
    final = {h['memory_id'] for h in retrieval['hits']}
    lexical = {h['memory_id'] for h in diagnostic['all_pool_lexical_hits']}
    raw = {h['memory_id'] for h in diagnostic['raw_hits']}
    rows = []
    for number, fact in enumerate(facts, 1):
        sources = []
        for item in original_pool.values():
            if not _supports(item, fact, positions):
                continue
            rank, hit = merged.get(item.id, (None, None))
            reference = source_messages[item.evidence_refs[0]]
            if item.id not in raw:
                cause = 'topic_route_exclusion' if item.id in lexical else 'lexical_no_match'
            elif item.id not in capped:
                cause = 'candidate_cap'
            elif item.id not in final:
                cause = 'top_k_cutoff'
            elif item.id not in selected:
                cause = 'context_budget'
            else:
                cause = 'visible'
            text = reference['content']
            spans = []
            for variant in fact['variants']:
                spans.extend(dict(start=m.start(), end=m.end(), quote=text[m.start():m.end()])
                    for m in re.finditer(re.escape(variant), text, re.I))
            sources.append(dict(episode_id=item.id, evidence_refs=item.evidence_refs,
                conversation_id=reference['conversation_id'], message_index=reference['message_index'],
                timestamp=reference['timestamp'], role=reference['role'], evidence_spans=spans,
                message_sha256=reference['message_sha256'], legal_at_cutoff=True,
                topic_ids=item.topic_ids, scenario_ids=item.scenario_ids,
                all_pool_lexical_match=item.id in lexical, initial_candidate=item.id in initial,
                raw_candidate=item.id in raw, merged_candidate=item.id in capped,
                merged_rank=rank, score=hit['score'] if hit else None,
                paths=hit['paths'] if hit else [], final_top_k=item.id in final,
                visible=item.id in selected, cause=cause,
                matched_query_terms=sorted(terms(retrieval['query_text']) & terms(item.content))))
        best = min((s['merged_rank'] for s in sources if s['merged_rank'] is not None), default=None)
        rows.append(dict(fact_id=fact.get('fact_id', f'f{number}'), variants=fact['variants'],
            source_exists_in_legal_history=bool(sources), best_merged_rank=best, sources=sources,
            exclusion_note='All book sources are fixture-approved and available at the final session cutoff; no scene restriction or current directive.',
            unknown='NOT_FOUND_IN_LEGAL_HISTORY' if not sources else None))
    return rows


def compare_case(case, *, gold, budget=2048):
    if type(budget) is not int or budget < 0:
        raise ValueError('预算必须为非负整数')
    # 输入适配器只接收历史，不接触题目期望、评价标准或 Gold。
    history = {k: case[k] for k in ('test_id', 'category', 'conversation_histories')}
    memory, context, info = build_approved_fixture(history, approval='approved_fixture')
    query = Query(text=case['user_question'], as_of=info['last_time']+timedelta(microseconds=1), top_k=8)
    cards = build_cards(memory, context, approval='approved_fixture')
    view = CardView(memory, context, cards)
    doc = memory.document(context)
    positions = {}
    source_messages = {}
    origin_by_hash = {}
    for h in case['conversation_histories']:
        for index, message in enumerate(h['messages']):
            digest = sha256(message.get('content', '').encode()).hexdigest()
            origin_by_hash.setdefault((digest, index), []).append((h, message))
    # session UUID 与原始 conversation_id 的映射来自原始 Episode 头，不从答案猜测。
    for versions in doc.memories.values():
        item = versions[-1]
        ref = item.evidence_refs[0]
        evidence = doc.evidence[ref]
        scenario = re.match(r'^\[.*?;session=(.*?);range=', item.content).group(1)
        history_row, message = next((h, m) for h, m in origin_by_hash[(evidence.source_hash, evidence.message_ref)]
                                    if str(h['conversation_id']) == scenario)
        positions[ref] = (scenario, evidence.message_ref)
        source_messages[ref] = dict(conversation_id=scenario, message_index=evidence.message_ref,
            timestamp=str(history_row['timestamp']), role=evidence.role, content=message['content'],
            message_sha256=evidence.source_hash)
    groups = {}
    for group, adapter in [('A', memory), ('B', view), ('C', view)]:
        timings = []
        ordinary = None
        for _ in range(3):
            start = perf_counter()
            extras = entity_hits(view, context, query) if group == 'C' else ()
            ordinary = search(adapter, context, query, extra_hits=extras)
            timings.append((perf_counter()-start)*1000)
        inspected = search(adapter, context, query,
            extra_hits=entity_hits(view, context, query) if group == 'C' else (), inspect=True)
        assert ordinary['hits'] == inspected['hits']
        inspected['query_text'] = query.text
        pool = {m.id: m for m in adapter.records(context, as_of=query.as_of, scenario_ids=[])}
        selected, context_bytes = pack_context(inspected['hits'], pool, budget)
        groups[group] = dict(retrieval=inspected, selected_ids=selected, context_bytes=context_bytes,
            storage_canonical_bytes=len(encoded(doc.model_dump(mode='json'))),
            storage_card_sidecar_bytes=0 if group == 'A' else len(encoded([c.model_dump(mode='json') for c in cards])),
            indexed_text_bytes=sum(len(m.content.encode()) for m in pool.values()),
            retrieval_ms=timings, retrieval_median_ms=median(timings), pool_count=len(pool),
            top_k=query.top_k, budget=budget, provider_tokens='NOT_RUN', model_task_quality='NOT_RUN',
            judge='NOT_RUN', live_model_calls=0, _pool=pool)
    # 所有查询、卡片、候选、排名在这里之前已完成；以下才读取 Gold。
    facts = _facts(gold)
    for group, row in groups.items():
        pool = row.pop('_pool')
        hits = row['retrieval']['hits']
        diagnostic = row['retrieval']['diagnostic']
        def coverage(ids):
            return sum(any(_supports(pool[i], f, positions) for i in ids) for f in facts)/len(facts) if facts else None
        hit_ids = [h['memory_id'] for h in hits]
        relevant = [any(_supports(pool[i], f, positions) for f in facts) for i in hit_ids]
        row.update(hit_at_k=int(any(relevant)), fact_recall_at_k=coverage(hit_ids),
            mrr=next((1/(i+1) for i, yes in enumerate(relevant) if yes), 0.0),
            candidate_fact_coverage=coverage([h['memory_id'] for h in diagnostic['merged_candidates']]),
            full_pre_cap_fact_coverage=coverage([h['memory_id'] for h in diagnostic['merged_before_cap']]),
            legal_history_fact_coverage=coverage(pool), visible_fact_recall=coverage(row['selected_ids']),
            unique_before_topk=len(diagnostic['merged_candidates']),
            unique_before_cap=len(diagnostic['merged_before_cap']),
            duplicate_hits_before_topk=row['retrieval']['trace']['duplicate_hit_count'])
        exceptions = [f for f in facts if any(w in f.get('label','').casefold()
            for w in ('conditional', 'exception', 'not a purchased', 'in-network maximum'))]
        row['exception_facts_covered'] = sum(any(_supports(pool[i], f, positions) for i in hit_ids) for f in exceptions)
        row['exception_facts_total'] = len(exceptions)
        # 短表面形式在其他情景出现只作碰撞提示，不冒充完整语义错误率。
        row['cross_scenario_surface_collisions'] = sum(
            bool({s['conversation_id'] for s in f.get('sources', [])})
            and _matches(_text(pool[i]), f)
            and not ({s['conversation_id'] for s in f.get('sources', [])} & {positions[r][0] for r in pool[i].evidence_refs})
            for f in facts for i in hit_ids)
        row['semantic_wrong_scenario_rate'] = 'NOT_RUN'
        row['fact_matches'] = [dict(fact_id=f.get('fact_id', f'f{n}'),
            hit_ids=[i for i in hit_ids if _supports(pool[i], f, positions)],
            visible_ids=[i for i in row['selected_ids'] if _supports(pool[i], f, positions)])
            for n, f in enumerate(facts, 1)]
    original = {m.id:m for m in memory.records(context, as_of=query.as_of, scenario_ids=[])}
    attribution = _diagnose(facts, original, groups['A']['retrieval'], groups['A']['selected_ids'], positions, source_messages)
    return dict(case_id=case['test_id'], category=case['category'], question=case['user_question'],
        input_history_sha256=sha256(encoded(history)).hexdigest(), as_of=query.as_of.isoformat(),
        gold_fact_count=len(facts), groups=groups, raw_fact_attribution=attribution,
        cards=[c.model_dump(mode='json') for c in cards],
        source_episodes=[m.model_dump(mode='json') for m in original.values()])
