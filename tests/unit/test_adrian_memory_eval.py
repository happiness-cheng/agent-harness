from datetime import datetime, timezone

import pytest

from min_agent.long_memory.evaluation import build_approved_fixture, evaluate_case, inspect_private_corpus
from min_agent.long_memory import LongMemory, Query
from min_agent.sessions import SessionStore
from uuid import uuid4


def book_case():
    return dict(test_id='synthetic_eval',category='layer2',title='synthetic',
        conversation_histories=[
            dict(conversation_id='a',timestamp='2026-01-01 10:00:00',messages=[dict(role='user',content='旧编号PINE-1')]),
            dict(conversation_id='b',timestamp='2026-01-03 10:00:00',messages=[dict(role='user',content='新编号PINE-2')])],
        user_question='当前编号是什么？',expected_behavior='HIDDEN_GOLD_NEVER_INGEST',evaluation_criteria={})


def test_fixture_approval_is_explicit_and_future_session_is_not_visible():
    case=book_case()
    with pytest.raises(PermissionError): build_approved_fixture(case,approval='none')
    memory,context,info=build_approved_fixture(case,approval='approved_fixture')
    assert info['simulated_approval_ratio']==1
    assert 'HIDDEN_GOLD_NEVER_INGEST' not in memory.document(context).model_dump_json()
    past=memory.records(context,as_of=datetime(2026,1,2,tzinfo=timezone.utc))
    assert len(past)==1 and 'PINE-2' not in past[0].content


def test_case_comparison_uses_same_pool_budget_and_does_not_fake_model_quality():
    result=evaluate_case(book_case(),gold={'required_facts':['PINE-2']},budget=1800,approval='approved_fixture')
    assert len(result['strategies'])==4
    pools={r['pool_hash'] for r in result['strategies'].values()}
    assert len(pools)==1
    for row in result['strategies'].values():
        assert row['quality']=='NOT_RUN'
        assert row['provider_tokens']=='NOT_RUN'
        assert row['selected_bytes_upper_bound']<=1800


def test_private_adapter_is_local_only_no_auto_import_and_rejects_path_escape(tmp_path):
    import json
    folder=tmp_path/'source';folder.mkdir()
    path=folder/'conversation.md'
    path.write_text('# 私有标题\n## 第 1 轮：我\nPRIVATE_CANARY_A\n## 第 1 轮：ChatGPT\nPRIVATE_CANARY_B\n',encoding='utf-8')
    (folder/'_index.json').write_text(json.dumps([dict(file='conversation.md',id='private-id',updated='2026-01-01T00:00:00Z',messages=2,turns=1)]),encoding='utf-8')
    result=inspect_private_corpus(tmp_path)
    rendered=json.dumps(result,ensure_ascii=False)
    assert 'PRIVATE_CANARY' not in rendered and '私有标题' not in rendered and 'private-id' not in rendered
    assert result['formal_memory_imports']==0 and result['conversations']==1
    (folder/'_index.json').write_text(json.dumps([dict(file='../../escape.md',id='x',updated='2026-01-01')]),encoding='utf-8')
    with pytest.raises(PermissionError): inspect_private_corpus(tmp_path)
