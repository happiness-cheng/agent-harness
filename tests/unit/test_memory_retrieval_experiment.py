from min_agent.long_memory.retrieval_experiment import compare_case


def case():
    return dict(test_id='isolation-synthetic', category='synthetic', user_question='account number',
        expected_behavior='GOLD_SENTINEL', conversation_histories=[dict(conversation_id='only',
        timestamp='2026-01-01T00:00:00Z', messages=[
            {'role': 'assistant', 'content': 'What is your account number?'},
            {'role': 'user', 'content': 'ATLAS-42'}])])


def test_same_scope_budget_topk_and_posthoc_only_gold():
    before = compare_case(case(), gold=['ATLAS-42'], budget=2048)
    after = compare_case(case(), gold=['OTHER_GOLD_SENTINEL'], budget=2048)
    for group in ('A', 'B', 'C'):
        left, right = before['groups'][group], after['groups'][group]
        assert left['retrieval'] == right['retrieval']
        assert left['selected_ids'] == right['selected_ids']
        assert left['context_bytes'] <= 2048
        assert left['top_k'] == 8 and left['model_task_quality'] == 'NOT_RUN'
    assert before['groups']['B']['candidate_fact_coverage'] == 1
    assert before['groups']['A']['candidate_fact_coverage'] == 0
    assert before['input_history_sha256'] == after['input_history_sha256']


def test_budget_failure_is_separate_from_retrieval_failure():
    result = compare_case(case(), gold=['ATLAS-42'], budget=0)
    assert result['groups']['B']['fact_recall_at_k'] == 1
    assert result['groups']['B']['visible_fact_recall'] == 0
    assert result['groups']['B']['context_bytes'] == 0
