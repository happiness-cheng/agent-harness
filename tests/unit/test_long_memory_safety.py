"""独立于排序好坏的安全/性质回归；不通过质量均分抵消。"""
from datetime import timedelta
from itertools import permutations
import json
from uuid import uuid4, uuid5

import pytest

from min_agent.long_memory import LongMemory, Query, SearchHit
from min_agent.long_memory.retrieval import fuse_hits
from min_agent.models import Message
from min_agent.sessions import ExecutionContext
from test_long_memory_store import setup_memory, candidate, commit


def test_internal_owner_document_cannot_be_opened_as_runtime_session():
    sessions,context,clock,memory=setup_memory()
    commit(memory,context,candidate(memory,context))
    internal=ExecutionContext(owner_id=context.owner_id,session_id=uuid5(context.owner_id,'adrian-confirmed-memory-v01'))
    with pytest.raises(PermissionError): sessions.snapshot(internal)
    with pytest.raises(PermissionError): sessions.begin(internal)


def test_fts_and_graph_filter_foreign_owner_before_search_not_after_top_k():
    sessions,context,clock,memory=setup_memory()
    item=commit(memory,context,candidate(memory,context))
    foreign=sessions.create(uuid4())
    result=memory.search(foreign,Query(text='Python',as_of=clock.now),
                         extra_hits=[SearchHit(memory_id=item.id,source='vector',score=100)])
    assert result['hits']==[]
    assert result['trace']['filtered']['unauthorized_external_hit']==1
    with pytest.raises(PermissionError): memory.link(foreign,item.id,item.id,'RELATED_TO')


def test_same_phrase_in_two_scenarios_keeps_two_ids_and_specific_exception_wins():
    sessions,context,clock,memory=setup_memory()
    a=memory.register_scenario(context,'Codex');b=memory.register_scenario(context,'OpenHands')
    one=commit(memory,context,candidate(memory,context,scenario_ids=[a.id]))
    two=commit(memory,context,candidate(memory,context,scenario_ids=[b.id]))
    assert one.id!=two.id
    assert len(memory.search(context,Query(text='Python',as_of=clock.now,scenario_ids=[a.id,b.id]))['hits'])==2


def test_assistant_text_is_only_inferred_and_always_needs_confirmation():
    sessions,context,clock,memory=setup_memory()
    task=sessions.begin(context)
    sessions.append(context,task.task_id,[Message(role='assistant',content='我猜用户总是喜欢Java。')])
    sessions.finish(context,task.task_id,status='completed')
    with pytest.raises(PermissionError): memory.propose(context,'用户喜欢Java',source_indices=[1])
    pending=memory.propose(context,'用户可能喜欢Java，待确认',source_indices=[1],evidence_type='INFERRED')
    assert memory.records(context,as_of=clock.now)==[]
    confirmed=commit(memory,context,pending)
    assert confirmed.evidence_type=='INFERRED'
    assert memory.document(context).evidence[confirmed.evidence_refs[0]].role=='assistant'


def test_one_broad_explicit_statement_is_sufficient_no_two_quote_rule():
    sessions,context,clock,memory=setup_memory()
    item=commit(memory,context,candidate(memory,context,breadth='broad',stable=True))
    assert item.breadth=='broad' and len(item.evidence_refs)==1
    assert memory.rank_for_core(context,Query(text='Python',as_of=clock.now),budget=2048)['view']['stable_core']


def test_atomic_write_failure_never_publishes_confirmation(monkeypatch):
    sessions,context,clock,memory=setup_memory()
    pending=candidate(memory,context)
    before=memory.document(context).model_dump_json()
    original=sessions._commit_session
    def fail(snapshot,**kwargs): raise OSError('synthetic-storage-failure')
    monkeypatch.setattr(sessions,'_commit_session',fail)
    with pytest.raises(OSError): commit(memory,context,pending)
    monkeypatch.setattr(sessions,'_commit_session',original)
    assert memory.document(context).model_dump_json()==before


def test_id_fusion_is_permutation_invariant_and_distinct_ids_survive():
    hits=[SearchHit(memory_id='m1',source='group',score=0.4,topic_id='t1'),
          SearchHit(memory_id='m1',source='bm25',score=0.9),
          SearchHit(memory_id='m2',source='bm25',score=0.8)]
    for order in permutations(hits):
        fused=fuse_hits(order,top_k=2)
        assert [r['memory_id'] for r in fused]==['m1','m2']
        assert len(fused[0]['paths'])==2 and fused[0]['score']==0.9


def test_wrong_candidate_token_cannot_approve_another_fact():
    sessions,context,clock,memory=setup_memory()
    a=candidate(memory,context);b=memory.propose(context,'另一事实',source_indices=[0])
    with pytest.raises(PermissionError): memory.confirm(context,b.id,review_token=memory.review(context,a.id)['review_token'])
    assert memory.records(context,as_of=clock.now)==[]


def test_past_core_never_contains_future_goal_or_future_preference():
    sessions,context,clock,memory=setup_memory()
    original=commit(memory,context,candidate(memory,context))
    before=clock.now;clock.now+=timedelta(days=1)
    goal=memory.set_goal(context,'未来目标',source_index=0,priority=3)
    future=commit(memory,context,memory.propose(context,'未来明确要求',source_indices=[0],goal_ids=[goal.id]))
    result=memory.rank_for_core(context,Query(text='安排工作',as_of=before),budget=2048)
    assert result['pool_ids']==[original.id] and future.id not in json.dumps(result)


def test_unconfirmed_rejection_or_expiration_is_not_an_authorization():
    sessions,context,clock,memory=setup_memory()
    pending=candidate(memory,context)
    token=memory.review(context,pending.id)['review_token']
    memory.reject(context,pending.id)
    with pytest.raises(PermissionError): memory.confirm(context,pending.id,review_token=token)
    assert memory.records(context,as_of=clock.now)==[]


def test_pending_old_source_cannot_be_confirmed_after_source_memory_is_deleted():
    sessions,context,clock,memory=setup_memory()
    first=commit(memory,context,candidate(memory,context))
    pending=memory.propose(context,'旧来源的另一种总结',source_indices=[0])
    token=memory.review(context,pending.id)['review_token']
    memory.delete(context,first.id)
    with pytest.raises(PermissionError): memory.confirm(context,pending.id,review_token=token)


def test_identical_session_scoped_text_in_distinct_sessions_is_not_collapsed():
    sessions,context,clock,memory=setup_memory()
    first=commit(memory,context,candidate(memory,context,scope='session'))
    other=sessions.create(context.owner_id)
    from min_agent.task_state import TaskController,GoalSpec
    controller=TaskController(sessions)
    p=controller.propose(other,GoalSpec(objective='确认记忆',completion_criteria=('审核候选',)))
    controller.confirm(other,p.proposal_id);controller.select_mode(other,'build')
    task=sessions.begin(other)
    sessions.append(other,task.task_id,[Message(role='user',content='我希望解释代码时先给Python短例子。')])
    sessions.finish(other,task.task_id,status='completed')
    second=commit(memory,other,candidate(memory,other,scope='session'))
    assert second.id!=first.id
    assert [m.id for m in memory.records(other,as_of=clock.now)]==[second.id]


def test_new_inferred_fact_is_explicitly_disputed_until_reviewed_as_such():
    sessions,context,clock,memory=setup_memory()
    pending=memory.propose(context,'可能喜欢更简短的讲解',source_indices=[0],evidence_type='INFERRED')
    confirmed=commit(memory,context,pending)
    assert confirmed.lifecycle=='disputed'


def test_confirmed_specific_exception_suppresses_general_only_in_its_scenario():
    sessions,context,clock,memory=setup_memory()
    scene=memory.register_scenario(context,'英语练习')
    general=commit(memory,context,candidate(memory,context,conflict_key='language',breadth='broad'))
    specific=commit(memory,context,memory.propose(context,'英语练习只用英语',source_indices=[0],
        conflict_key='language',scenario_ids=[scene.id]))
    q=Query(text='解释代码',as_of=clock.now,scenario_ids=[scene.id])
    result=memory.rank_for_core(context,q,budget=2000)
    assert result['pool_ids']==[specific.id]
    assert memory.rank_for_core(context,Query(text='解释代码',as_of=clock.now),budget=2000)['pool_ids']==[general.id]


def test_low_confidence_group_routing_uses_global_fallback():
    sessions,context,clock,memory=setup_memory()
    wrong=memory.register_topic(context,'report',keywords=['report'])
    right=memory.register_topic(context,'storage',keywords=['storage'])
    commit(memory,context,memory.propose(context,'report othernotes',source_indices=[0],topic_ids=[wrong.id]))
    item=commit(memory,context,memory.propose(context,'unique-needle',source_indices=[0],topic_ids=[right.id]))
    result=memory.search(context,Query(text='report alpha beta gamma delta epsilon zeta theta unique-needle',as_of=clock.now))
    assert result['trace']['global_fallback'] is True
    assert item.id in {h['memory_id'] for h in result['hits']}
