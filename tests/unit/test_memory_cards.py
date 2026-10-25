from datetime import timedelta

import pytest

from min_agent.long_memory import Query
from min_agent.long_memory.cards import build_cards, CardView, entity_hits
from min_agent.long_memory.evaluation import build_approved_fixture
from min_agent.long_memory.retrieval import search


def fixture():
    case = dict(test_id='card-synthetic', category='test', user_question='UNSEEN_QUESTION',
        expected_behavior='GOLD_CANARY', conversation_histories=[dict(conversation_id='atlas',
        timestamp='2026-01-01T00:00:00Z', metadata={'business': 'Atlas Payments (AP)'}, messages=[
            {'role': 'assistant', 'content': 'What is your account number?'},
            {'role': 'user', 'content': 'ATLAS-42'},
            {'role': 'assistant', 'content': 'The result is uncertain; do not treat it as verified.'}]),
        dict(conversation_id='other', timestamp='2026-02-01T00:00:00Z', messages=[
            {'role': 'user', 'content': 'ATLAS-42'}])])
    return build_approved_fixture(case, approval='approved_fixture')


def test_cards_keep_context_role_evidence_and_independent_scenario_ids():
    memory, ctx, info = fixture()
    with pytest.raises(PermissionError): build_cards(memory, ctx, approval='automatic')
    cards = build_cards(memory, ctx, approval='approved_fixture')
    assert len(cards) == 4 and len({c.memory_id for c in cards}) == 4
    replies = [c for c in cards if c.content.endswith('ATLAS-42')]
    assert len(replies) == 2 and replies[0].scenario != replies[1].scenario
    assert 'What is your account number?' in replies[0].content
    assert len(replies[0].evidence_refs) == 2
    assert all('GOLD_CANARY' not in c.model_dump_json() for c in cards)
    assert cards[0].evidence_type == 'INFERRED' and cards[0].confirmation_status == 'confirmed_fixture'
    assert len(memory.document(ctx).memories) == 4  # 卡片不反写权威层。


def test_card_view_filters_future_foreign_expired_and_withdrawn_sources():
    memory, ctx, info = fixture()
    cards = build_cards(memory, ctx, approval='approved_fixture')
    at = info['last_time'] - timedelta(days=1)
    view = CardView(memory, ctx, cards)
    query = Query(text='ATLAS', as_of=at)
    result = search(view, ctx, query, extra_hits=entity_hits(view, ctx, query))
    assert all(h['memory_id'] != cards[-1].memory_id for h in result['hits'])
    bad = cards[1].model_copy(update={'user_id': 'foreign'})
    assert bad.memory_id not in {m.id for m in CardView(memory, ctx, [bad]).records(ctx, as_of=at)}
    from min_agent.task_state import TaskController, GoalSpec
    controller = TaskController(memory.sessions)
    proposal = controller.propose(ctx, GoalSpec(objective='删除测试记忆', completion_criteria=('撤回指定记录',)))
    controller.confirm(ctx, proposal.proposal_id)
    controller.select_mode(ctx, 'build')
    memory.delete(ctx, cards[1].memory_id)
    assert cards[1].memory_id not in {m.id for m in view.records(ctx, as_of=at)}


def test_explicit_alias_is_structured_path_without_gold_and_dedup_before_topk():
    memory, ctx, info = fixture()
    view = CardView(memory, ctx, build_cards(memory, ctx, approval='approved_fixture'))
    query = Query(text='AP', as_of=info['last_time'], top_k=8)
    hits = entity_hits(view, ctx, query)
    assert hits and all(h.source == 'structured_entity_alias' for h in hits)
    result = search(view, ctx, query, extra_hits=hits)
    assert len({h['memory_id'] for h in result['hits']}) == len(result['hits'])


def test_pending_card_and_changed_source_fingerprint_are_not_searchable():
    memory, ctx, info = fixture()
    cards = build_cards(memory, ctx, approval='approved_fixture')
    pending = cards[1].model_copy(update={'confirmation_status': 'pending'})
    stale = cards[0].model_copy(update={'source_sha256': '0'*64})
    view = CardView(memory, ctx, [pending, stale])
    assert view.records(ctx, as_of=info['last_time']) == []
    with pytest.raises(ValueError):
        CardView(memory, ctx, [cards[0], cards[0]])


def test_sidecar_respects_parent_expiry_scene_and_owner_even_with_alias_matches():
    memory, ctx, info = fixture()
    from uuid import uuid4
    from min_agent.sessions import SessionStore
    other = memory.sessions.create(uuid4())
    cards = build_cards(memory, ctx, approval='approved_fixture')
    view = CardView(memory, ctx, cards)
    with pytest.raises(PermissionError):
        view.records(other, as_of=info['last_time'])
    parent_id = cards[1].memory_id
    def scope_source(doc, now):
        old = doc.memories[parent_id][-1]
        doc.memories[parent_id][-1] = old.model_copy(update={
            'valid_until': old.effective_from + timedelta(days=2), 'scenario_ids': ['scene-A']})
    memory._write(ctx, scope_source)  # 受控 Fixture 元数据，不是生产写入口。
    view = CardView(memory, ctx, build_cards(memory, ctx, approval='approved_fixture'))
    assert parent_id not in {m.id for m in view.records(ctx, as_of=info['last_time'], scenario_ids=['scene-A'])}
    at = cards[1].valid_time.start + timedelta(days=1)
    assert parent_id not in {m.id for m in view.records(ctx, as_of=at, scenario_ids=['scene-B'])}
    assert parent_id in {m.id for m in view.records(ctx, as_of=at, scenario_ids=['scene-A'])}


def test_source_withdrawal_also_removes_neighbor_context_projection():
    memory, ctx, info = fixture()
    cards = build_cards(memory, ctx, approval='approved_fixture')
    view = CardView(memory, ctx, cards)
    from min_agent.task_state import TaskController, GoalSpec
    controller = TaskController(memory.sessions)
    proposal = controller.propose(ctx, GoalSpec(objective='撤回上下文来源', completion_criteria=('撤回指定记录',)))
    controller.confirm(ctx, proposal.proposal_id)
    controller.select_mode(ctx, 'build')
    memory.delete(ctx, cards[0].memory_id)
    available = {m.id for m in view.records(ctx, as_of=info['last_time'])}
    assert cards[0].memory_id not in available and cards[1].memory_id not in available
