from min_agent.long_memory.quality_evaluation import evaluate_quality_case


def case():
    return dict(test_id='quality-eval',category='synthetic',user_question='reference number',
        expected_behavior='GOLD_SENTINEL',conversation_histories=[dict(conversation_id='one',
        timestamp='2026-01-01T00:00:00Z',messages=[
            dict(role='assistant',content='What is your reference number?'),
            dict(role='user',content='LUMEN-72')])])


def test_gold_only_changes_offline_scores_and_never_candidates_or_context():
    first=evaluate_quality_case(case(),gold=['LUMEN-72'])
    second=evaluate_quality_case(case(),gold=['OTHER_GOLD'])
    for group in ('A','B','C','D'):
        a,b=first['groups'][group],second['groups'][group]
        assert a['retrieval']==b['retrieval']
        assert a['visible_ids']==b['visible_ids']
        assert a['context_bytes']<=2048
    assert first['groups']['D']['candidate_facts']==1
    assert first['groups']['D']['visible_facts']==1


def test_budget_is_separate_from_topk_and_no_fact_is_fabricated():
    result=evaluate_quality_case(case(),gold=['LUMEN-72','ABSENT'],budget=0)
    d=result['groups']['D']
    assert d['topk_facts']==1 and d['visible_facts']==0
    assert d['context_bytes']==0
    assert result['fact_analysis'][1]['literal_history_support']==[]
