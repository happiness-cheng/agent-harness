from min_agent.long_memory import Query
from min_agent.long_memory.retrieval import search
from test_long_memory_store import setup_memory, candidate, commit


def test_offline_full_pool_preserves_production_hits_and_exposes_pre_topk_ranks():
    _, context, clock, memory = setup_memory()
    ids = [commit(memory, context, memory.propose(context, f'Python example {i}', source_indices=[0])).id for i in range(12)]
    query = Query(text='Python', as_of=clock.now, top_k=2, max_candidates=5)
    ordinary = search(memory, context, query)
    inspected = search(memory, context, query, inspect=True)
    assert inspected['hits'] == ordinary['hits']
    detail = inspected['diagnostic']
    assert set(detail['eligible_ids']) == set(ids)
    assert len(detail['merged_before_cap']) == 12
    assert len(detail['merged_candidates']) == 5
    assert len(detail['raw_hits']) == 12
    assert 'diagnostic' not in ordinary


def test_inspection_never_expands_authority_or_scene_filters():
    _, context, clock, memory = setup_memory()
    scene = memory.register_scenario(context, 'private-context')
    item = commit(memory, context, candidate(memory, context, scenario_ids=[scene.id]))
    result = search(memory, context, Query(text='Python', as_of=clock.now), inspect=True)
    assert item.id not in result['diagnostic']['eligible_ids']
    assert not result['hits']
