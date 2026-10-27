from datetime import timedelta

import pytest

from min_agent.long_memory import Query
from min_agent.long_memory.selection import SelectionPolicy
from test_long_memory_store import setup_memory, candidate, commit


def test_goal_priority_never_pauses_other_goal_and_history_is_time_scoped():
    sessions,context,clock,memory=setup_memory()
    job=memory.set_goal(context,'求职',source_index=0,priority=2,horizon='long_term')
    adrian=memory.set_goal(context,'Adrian',source_index=0,priority=1)
    before=clock.now
    clock.now+=timedelta(days=1)
    memory.update_goal(context,adrian.id,source_index=0,priority=3)
    assert memory.goals(context,as_of=clock.now)[job.id].lifecycle=='active'
    assert memory.goals(context,as_of=before)[adrian.id].priority==1
    memory.update_goal(context,job.id,source_index=0,lifecycle='paused')
    assert memory.goals(context,as_of=clock.now)[job.id].lifecycle=='paused'
    memory.update_goal(context,job.id,source_index=0,lifecycle='active')
    assert memory.goals(context,as_of=clock.now)[job.id].lifecycle=='active'


def test_strategies_share_pool_and_budget_but_use_different_explainable_signals():
    sessions,context,clock,memory=setup_memory()
    goal=memory.set_goal(context,'Adrian',source_index=0,priority=3)
    valuable=commit(memory,context,candidate(memory,context,goal_ids=[goal.id],decision_cost=3))
    frequent=commit(memory,context,memory.propose(context,'常用但低决策价值的记录',source_indices=[0],decision_cost=0))
    for event in ('use1','use2','use3'): memory.note_usage(context,frequent.id,event)
    clock.now+=timedelta(days=1)
    recent=commit(memory,context,memory.propose(context,'最新但低决策价值的记录',source_indices=[0],decision_cost=0))
    q=Query(text='安排工作',as_of=clock.now)
    expected={'RECENCY':recent.id,'FREQUENCY':frequent.id,'GOAL_VALUE':valuable.id}
    for strategy,identity in expected.items():
        result=memory.rank_for_core(context,q,strategy=strategy,budget=600)
        assert result['ranked_ids'][0]==identity
        assert result['bytes_upper_bound']<=600
        assert set(result['pool_ids'])=={valuable.id,frequent.id,recent.id}
        assert result['reason_codes']
    assert memory.rank_for_core(context,q,strategy='NONE',budget=600)['selected_ids']==[]


def test_visible_versions_are_deduped_but_after_removal_can_be_reinjected():
    sessions,context,clock,memory=setup_memory()
    item=commit(memory,context,candidate(memory,context))
    q=Query(text='Python',as_of=clock.now)
    one=memory.assemble_context(context,q,budget=1600)
    assert one['trace']['injected_ids']==[item.id]
    two=memory.assemble_context(context,q,budget=1600,visible_versions={(item.id,item.version)})
    assert two['trace']['injected_ids']==[]
    again=memory.assemble_context(context,q,budget=1600,visible_versions=set())
    assert again['trace']['injected_ids']==[item.id]
    assert again['trace']['bytes_upper_bound']<=1600


def test_core_changes_are_views_and_budget_zero_never_changes_scope_or_authorization():
    sessions,context,clock,memory=setup_memory()
    item=commit(memory,context,candidate(memory,context,breadth='broad',stable=True))
    before=memory.document(context).model_dump_json()
    for strategy in ('NONE','RECENCY','FREQUENCY','GOAL_VALUE'):
        result=memory.rank_for_core(context,Query(text='Python',as_of=clock.now),strategy=strategy,budget=0)
        assert result['selected_ids']==[]
    assert memory.document(context).model_dump_json()==before


def test_repeating_same_usage_or_confirmation_does_not_inflate_frequency_or_revision():
    sessions,context,clock,memory=setup_memory()
    pending=candidate(memory,context)
    item=commit(memory,context,pending)
    before=memory.document(context).revision
    commit(memory,context,pending)
    assert memory.document(context).revision==before
    memory.note_usage(context,item.id,'one-real-use')
    before=memory.document(context).revision
    memory.note_usage(context,item.id,'one-real-use')
    assert memory.document(context).revision==before


def test_policy_weights_are_explicit_and_validated():
    with pytest.raises(ValueError): SelectionPolicy(goal_weight=-1)
