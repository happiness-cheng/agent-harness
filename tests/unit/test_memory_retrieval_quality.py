from datetime import timedelta

import pytest

from min_agent.long_memory import Query
from min_agent.long_memory.evaluation import build_approved_fixture
from min_agent.long_memory.retrieval import search
from min_agent.long_memory.retrieval_quality import experiment_search


def setup(messages):
    case = dict(test_id='quality-nonce', category='synthetic', conversation_histories=[dict(
        conversation_id='one', timestamp='2026-01-01T00:00:00Z',
        messages=[dict(role=role, content=text) for role,text in messages])])
    return build_approved_fixture(case, approval='approved_fixture')


def test_reranking_preserves_exact_candidate_ids_and_promotes_value_over_question():
    memory, ctx, info = setup([
        ('user','What is my reference number? I need my reference number.'),
        ('assistant','Your reference number is LUMEN-72.')])
    query = Query(text='What is my reference number?', as_of=info['last_time'])
    a = experiment_search(memory, ctx, query)
    b = experiment_search(memory, ctx, query, improved_ranking=True)
    assert a['hits'] == search(memory,ctx,query)['hits']
    assert {h['memory_id'] for h in a['candidates']} == {h['memory_id'] for h in b['candidates']}
    pool = {m.id:m for m in memory.records(ctx,as_of=query.as_of)}
    assert 'LUMEN-72' in pool[b['hits'][0]['memory_id']].content
    assert b['ranking_features'][b['hits'][0]['memory_id']]['specific_value'] > 0


def test_neighbor_recall_keeps_raw_episode_and_original_sort_scores():
    memory, ctx, info = setup([('assistant','What is your reference number?'),('user','LUMEN-72')])
    query = Query(text='reference number', as_of=info['last_time'], top_k=1)
    a = experiment_search(memory,ctx,query)
    c = experiment_search(memory,ctx,query,improved_candidates=True)
    d = experiment_search(memory,ctx,query,improved_candidates=True,improved_ranking=True)
    pool = {m.id:m for m in memory.records(ctx,as_of=query.as_of)}
    value = next(i for i,m in pool.items() if m.content.endswith('LUMEN-72'))
    assert value not in {h['memory_id'] for h in a['candidates']}
    assert value in {h['memory_id'] for h in c['candidates']}
    assert {h['memory_id'] for h in c['candidates']} == {h['memory_id'] for h in d['candidates']}
    assert next(h for h in c['candidates'] if h['memory_id']==value)['score'] == 0
    assert d['hits'][0]['memory_id'] == value
    assert memory.document(ctx).revision == 1  # 无写入、副作用或 usage 自增。


def test_explicit_alias_path_is_active_and_deduplicated_without_cap_increase():
    memory, ctx, info = setup([
        ('assistant','Lumen Archive (LA) stores the document reference.'),
        ('user','The reference is NOVA-31.')])
    query = Query(text='LA reference', as_of=info['last_time'], max_candidates=2)
    result = experiment_search(memory,ctx,query,improved_candidates=True,improved_ranking=True)
    assert result['trace']['alias_bindings']
    assert 'lumen' in result['trace']['expanded_terms']
    assert len(result['candidates']) <= 2
    assert len({h['memory_id'] for h in result['candidates']}) == len(result['candidates'])


def test_expansion_and_reranking_respect_scene_future_deletion_and_foreign_owner():
    from test_long_memory_store import setup_memory, commit
    from uuid import uuid4
    _, ctx, clock, memory = setup_memory()
    visible = commit(memory,ctx,memory.propose(ctx,'Lumen Archive (LA) reference NOVA-31',source_indices=[0]))
    scene = memory.register_scenario(ctx,'other')
    hidden = commit(memory,ctx,memory.propose(ctx,'Lumen Archive (LA) reference SECRET-42',source_indices=[0],scenario_ids=[scene.id]))
    future = commit(memory,ctx,memory.propose(ctx,'Lumen Archive (LA) reference FUTURE-99',source_indices=[0],effective_from=clock.now+timedelta(days=1)))
    query = Query(text='LA reference', as_of=clock.now)
    result = experiment_search(memory,ctx,query,improved_candidates=True,improved_ranking=True)
    assert {h['memory_id'] for h in result['candidates']} == {visible.id}
    assert not {hidden.id,future.id} & {h['memory_id'] for h in result['candidates']}
    other = memory.sessions.create(uuid4())
    assert not experiment_search(memory,other,query,improved_candidates=True,improved_ranking=True)['hits']
    memory.delete(ctx,visible.id)
    assert not experiment_search(memory,ctx,query,improved_candidates=True,improved_ranking=True)['hits']
