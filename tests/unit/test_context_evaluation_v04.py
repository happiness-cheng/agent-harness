from min_agent.long_memory.context_evaluation import oracle_bound,evaluate_context_case
from min_agent.long_memory.retrieval_experiment import encoded


def test_exact_oracle_separates_cardinality_and_budget_without_entering_runtime():
    nodes=[dict(memory_id='large',entry={'text':'x'*100},mask=1),
           dict(memory_id='small',entry={'text':'AB'},mask=3)]
    budget=len(encoded([nodes[1]['entry']]))
    result=oracle_bound(nodes,budget=budget,max_items=1)
    assert result['unbounded_facts']==2 and result['rank_ceiling']==2
    assert result['budget_ceiling']==2 and result['budget_ids']==['small']
    assert oracle_bound(nodes,budget=0,max_items=1)['budget_ceiling']==0


def test_gold_cannot_change_ranking_or_packing_and_p0_matches_old_format():
    case=dict(test_id='v04-eval',category='synthetic',user_question='Lumen room number',
        conversation_histories=[dict(conversation_id='booking',timestamp='2026-01-01T00:00:00Z',
        metadata={'business':'Lumen'},messages=[dict(role='assistant',content='Welcome. Room 2841 is reserved. It is not available before March 12.')])])
    first=evaluate_context_case(case,gold=['2841']);second=evaluate_context_case(case,gold=['UNSEEN_GOLD'])
    for name,row in first['configurations'].items():
        other=second['configurations'][name]
        assert row['top_ids']==other['top_ids'] and row['assembly']==other['assembly']
        assert row['assembly']['bytes']<=2048
    assert first['configurations']['R0_P0']['topk_facts']==1
