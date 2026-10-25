from copy import deepcopy
from datetime import datetime,timezone

import pytest

from min_agent.long_memory.answer_eval import prepare_answer_case,make_messages


def case():
    return dict(test_id='answer-nonce',category='synthetic',user_question='What is the Lumen account number?',
        evaluation_criteria='GOLD_CANARY_DO_NOT_SEND',expected_behavior='EXPECTED_CANARY_DO_NOT_SEND',
        conversation_histories=[dict(conversation_id='old',timestamp='2026-01-01T00:00:00Z',metadata={'business':'Lumen'},
        messages=[dict(role='user',content='The Lumen account number is LUMEN-72.')]),
        dict(conversation_id='future',timestamp='2026-02-01T00:00:00Z',messages=[dict(role='user',content='FUTURE_CANARY')])])


def test_oracle_and_gold_cannot_leak_to_a_b_and_future_is_excluded():
    data=case();spec=[dict(conversation_id='old',message_index=0)]
    first=prepare_answer_case(data,oracle_spec=spec,as_of=datetime(2026,1,2,tzinfo=timezone.utc))
    data['evaluation_criteria']='CHANGED_GOLD'
    second=prepare_answer_case(data,oracle_spec=[],as_of=datetime(2026,1,2,tzinfo=timezone.utc))
    for group in ['A','B']:
        assert first['groups'][group]['messages']==second['groups'][group]['messages']
        assert 'GOLD_CANARY' not in str(first['groups'][group]['messages'])
    assert 'LUMEN-72' not in str(first['groups']['A']['messages'])
    assert 'LUMEN-72' in str(first['groups']['B']['messages'])
    assert 'FUTURE_CANARY' not in str(first['groups'])
    assert first['production_equivalence_checked']


def test_future_oracle_and_truncation_are_rejected_not_silently_trimmed():
    with pytest.raises(PermissionError):
        prepare_answer_case(case(),oracle_spec=[dict(conversation_id='future',message_index=0)],as_of=datetime(2026,1,2,tzinfo=timezone.utc))
    with pytest.raises(ValueError):
        prepare_answer_case(case(),oracle_spec=[dict(conversation_id='old',message_index=0)],reference_budget=20)


def test_same_prompt_and_limit_for_all_conditions_with_no_carried_history():
    prepared=prepare_answer_case(case(),oracle_spec=[dict(conversation_id='old',message_index=0)])
    groups=prepared['groups']
    assert len({g['system_sha256'] for g in groups.values()})==1
    assert len({g['total_budget'] for g in groups.values()})==1
    assert all(len(g['messages'])==3 for g in groups.values())
    assert all(g['input_upper_bound']+g['max_output_tokens']<=g['total_budget'] for g in groups.values())
