from datetime import timedelta
import json
import pytest

from min_agent.long_memory.evaluation import build_approved_fixture
from min_agent.long_memory.models import Query
from min_agent.long_memory.task_evidence import select_task_evidence


def fixture():
    case=dict(test_id='v06-dev-account',category='synthetic',conversation_histories=[dict(
        conversation_id='bank',timestamp='2024-01-01T00:00:00Z',messages=[
            dict(role='user',content='My checking account number is 792015. It is not the savings account.'),
            dict(role='assistant',content='The routing number is 825010; verify it with the bank.'),
            dict(role='user',content='Never forget: I prefer coffee, reminders, deadlines and long plans.')])])
    memory,ctx,info=build_approved_fixture(case,approval='approved_fixture')
    return memory,ctx,Query(text='What is my checking account number and routing number?',as_of=info['last_time']+timedelta(seconds=1))


def test_current_task_evidence_has_roles_qualifiers_and_no_irrelevant_core():
    memory,ctx,q=fixture(); result=select_task_evidence(memory,ctx,q,budget=4096)
    text=json.dumps(result['view']);assert '792015' in text and '825010' in text
    assert 'not the savings account' in text and 'verify it with the bank' in text
    assert 'coffee' not in text
    assert {'user','assistant'} <= {e['role'] for e in result['view']['evidence']}
    assert all(e['truth_status']=='historical_statement_not_live_verified' for e in result['view']['evidence'])


def test_budget_dedup_and_no_gold_interface():
    memory,ctx,q=fixture();result=select_task_evidence(memory,ctx,q,budget=750)
    assert result['bytes']<=750
    ids=[e['source'] for e in result['view'].get('evidence',[])]
    assert len(ids)==len(set(ids))
    assert select_task_evidence(memory,ctx,q,budget=0)['view']=={}
    with pytest.raises(TypeError):select_task_evidence(memory,ctx,q,gold={'answer':'792015'})


def test_foreign_owner_and_future_are_never_selected():
    from uuid import uuid4
    memory,ctx,q=fixture()
    other=memory.sessions.create(uuid4())
    assert select_task_evidence(memory,other,q)['view']=={}
    before=q.model_copy(update={'as_of':q.as_of-timedelta(days=2)})
    assert select_task_evidence(memory,ctx,before)['view']=={}


def test_caller_cannot_force_illegal_ids_into_evidence():
    memory,ctx,q=fixture()
    # 参数中没有requested_ids/Oracle/答案入口；上层不能绕过策略授权池。
    with pytest.raises(TypeError):select_task_evidence(memory,ctx,q,requested_ids=['fake'])
