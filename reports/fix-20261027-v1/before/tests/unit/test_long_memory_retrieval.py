from datetime import timedelta

import pytest

from min_agent.long_memory import Query, SearchHit
from min_agent.long_memory.retrieval import fuse_hits
from test_long_memory_store import setup_memory, candidate, commit


def test_five_paths_are_fused_by_id_before_top_k_without_score_inflation():
    hits=[SearchHit(memory_id='m1',source=s,score=1.0,topic_id=t) for s,t in
          [('group','t1'),('group','t2'),('group','t3'),('bm25',None),('vector',None)]]
    hits.append(SearchHit(memory_id='m2',source='bm25',score=1.1))
    result=fuse_hits(hits, top_k=2)
    assert [x['memory_id'] for x in result]==['m2','m1']
    assert len(result[1]['paths'])==5 and result[1]['score']==1


def test_group_search_fallback_and_scenario_identity_are_preserved():
    sessions, context, clock, memory=setup_memory()
    topic=memory.register_topic(context,'编程',keywords=['code','Python'])
    codex=memory.register_scenario(context,'Codex')
    hands=memory.register_scenario(context,'OpenHands')
    one=commit(memory,context,candidate(memory,context,topic_ids=[topic.id],scenario_ids=[codex.id]))
    two=commit(memory,context,memory.propose(context,'OpenHands先给Python例子',source_indices=[0],topic_ids=[topic.id],scenario_ids=[hands.id]))
    result=memory.search(context,Query(text='Python',as_of=clock.now,scenario_ids=[codex.id,hands.id]))
    assert {h['memory_id'] for h in result['hits']}=={one.id,two.id}
    assert result['trace']['routed_topics']==[topic.id]
    result=memory.search(context,Query(text='短例子',as_of=clock.now,scenario_ids=[codex.id]))
    assert result['trace']['global_fallback'] is True
    assert [h['memory_id'] for h in result['hits']]==[one.id]


def test_pending_future_foreign_and_deleted_nodes_never_expand():
    sessions, context, clock, memory=setup_memory()
    one=commit(memory,context,candidate(memory,context))
    two=commit(memory,context,memory.propose(context,'另一个要求',source_indices=[0]))
    memory.link(context,one.id,two.id,'RELATED_TO')
    assert {h['memory_id'] for h in memory.search(context,Query(text='Python',as_of=clock.now))['hits']}=={one.id,two.id}
    memory.delete(context,two.id)
    assert [h['memory_id'] for h in memory.search(context,Query(text='Python',as_of=clock.now))['hits']]==[one.id]


def test_explicit_directive_suppresses_matching_historical_preference():
    sessions, context, clock, memory=setup_memory()
    commit(memory,context,candidate(memory,context,conflict_key='example_language'))
    result=memory.search(context,Query(text='Python',as_of=clock.now,directives={'example_language':'C++'}))
    assert result['hits']==[]
    assert result['trace']['filtered']['current_directive']==1


def test_deleted_source_cannot_be_reproposed_with_paraphrase():
    sessions, context, clock, memory=setup_memory()
    item=commit(memory,context,candidate(memory,context))
    memory.delete(context,item.id)
    with pytest.raises(PermissionError): memory.propose(context,'换个说法再次保存',source_indices=[0])


def test_fts_unavailable_has_explicit_deterministic_fallback():
    sessions, context, clock, memory=setup_memory()
    item=commit(memory,context,candidate(memory,context))
    result=memory.search(context,Query(text='Python',as_of=clock.now),use_fts=False)
    assert result['trace']['backend']=='lexical_fallback'
    assert result['hits'][0]['memory_id']==item.id


def test_model_visible_search_response_stays_below_generic_tool_summary_threshold():
    sessions,context,clock,memory=setup_memory()
    for i in range(30):
        commit(memory,context,memory.propose(context,f'Python合成条目{i}',source_indices=[0]))
    from min_agent.long_memory.integration import bind_runtime_memory
    from min_agent.tools.registry import ToolRegistry
    from min_agent.models import ToolCall
    registry=ToolRegistry(sessions=sessions,context=context)
    bind_runtime_memory(registry,memory,context)
    result=registry.execute(ToolCall(id='bounded-search',name='search_long_memory',arguments={'question':'Python','top_k':30}))
    assert len(result['result'].encode('utf-8'))<2000
