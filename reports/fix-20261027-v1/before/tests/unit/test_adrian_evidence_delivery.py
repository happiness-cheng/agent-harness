"""生产装配的有界预算、顺序无关及Provider送达边界；仅合成批准Fixture。"""
import json
from datetime import timedelta

import pytest

from min_agent.long_memory.delivery import bind_active_memory, MemoryDeliveryModel, citation_check
from min_agent.long_memory.delivery_support import encoded
from min_agent.long_memory.evaluation import build_approved_fixture
from min_agent.long_memory.models import Query
from min_agent.models import Message
from min_agent.tools.registry import ToolRegistry


def prepared(*, reverse=False, budget=1200):
    messages = [dict(role='user', content=f'I had tea and a project meeting note {n}.') for n in range(12)]
    messages.append(dict(role='user', content='My membership number is ZX-48201. It is not an order number; valid only for personal bookings.'))
    case = dict(test_id='adrian-integration-controlled', category='synthetic', conversation_histories=[dict(
        conversation_id='membership', timestamp='2024-01-01T00:00:00Z', messages=messages)])
    memory, context, info = build_approved_fixture(case, approval='approved_fixture')
    query = Query(text='What is my membership number for personal bookings?', as_of=info['last_time'])
    registry = ToolRegistry()
    state = bind_active_memory(registry, memory, context, query, budget=budget)
    topic = next(iter(memory.document(context).topics))
    ids = json.loads(registry.get('read_long_memory_topic').handler(topic_id=topic, limit=30))['memory_ids']
    if reverse:
        ids.reverse()
    for start in range(0, len(ids), 8):
        registry.get('read_long_memory_evidence').handler(memory_ids=ids[start:start+8])
    return memory, context, registry, state


def outgoing(view):
    return [Message(role='user', content=json.dumps({'untrusted_user_memory': view}, ensure_ascii=False))]


def test_priority_and_budget_feedback_are_preserved_in_production_package():
    _, _, registry, state = prepared()
    view = registry.memory_view([])
    assert len(encoded(view)) <= 1200
    assert 'ZX-48201' in encoded(view).decode()
    assert 'valid only for personal bookings' in encoded(view).decode()
    assert state.requested_ids == state.loaded_ids and not state.receipts
    assert view['memory_delivery']['omitted'] > 0
    assert all(o['reason'] == 'budget' for o in state.omitted)
    assert view['memory_delivery']['history_absence_not_established'] is True
    receipt = state.before_send(outgoing(view))
    assert receipt['status'] == 'attempted' and not receipt['delivered']
    state.after_send(receipt)
    assert receipt['delivered'] == state.selected_ids


def test_read_order_does_not_change_selected_evidence():
    left = prepared()[2].memory_view([])
    right = prepared(reverse=True)[2].memory_view([])
    assert {e['source'] for e in left['evidence']} == {e['source'] for e in right['evidence']}


@pytest.mark.parametrize('budget', [0, 100, 400, 800])
def test_budget_does_not_truncate_evidence_or_silently_mark_it_delivered(budget):
    _, _, registry, state = prepared(budget=budget)
    view = registry.memory_view([])
    assert len(encoded(view or {})) <= max(2, budget)
    assert not state.receipts
    if not state.feedback_fits:
        with pytest.raises(PermissionError):
            state.before_send(outgoing(view or {}))
    else:
        assert view['memory_delivery']['omitted'] > 0
        for entry in view['evidence']:
            assert entry['quotes'][0]['start'] == 0
            assert entry['quotes'][0]['end'] == len(entry['quotes'][0]['text'])


def test_revocation_after_final_build_blocks_provider():
    memory, context, registry, state = prepared()
    view = registry.memory_view([])
    from min_agent.task_state import TaskController, GoalSpec
    control = TaskController(memory.sessions)
    proposal = control.propose(context, GoalSpec(objective='合成测试定向撤回', completion_criteria=('指定证据已撤回',)))
    control.confirm(context, proposal.proposal_id)
    control.select_mode(context, 'build')
    memory.delete(context, state.selected_ids[0])
    with pytest.raises(PermissionError, match='撤回'):
        state.before_send(outgoing(view))
    assert not state.receipts


def test_future_ids_are_not_exposed_by_search_or_index():
    memory, context, _, state = prepared()
    query = state.query.model_copy(update={'as_of': state.query.as_of - timedelta(days=2)})
    registry = ToolRegistry()
    bind_active_memory(registry, memory, context, query)
    result = json.loads(registry.get('search_long_memory').handler(question=query.text))
    assert not result['memory_ids']
    assert not registry.memory_view([]).get('root_index')


def test_provider_error_does_not_create_a_delivered_receipt():
    memory, context, registry, state = prepared()
    view = registry.memory_view([])
    class Unavailable:
        def generate(self, messages, *, tools=None):
            raise RuntimeError('synthetic provider unavailable')
    with pytest.raises(RuntimeError, match='synthetic provider'):
        MemoryDeliveryModel(Unavailable(), state).generate(outgoing(view))
    receipt = memory.sessions.extension(context, 'long_memory_delivery', {})
    assert receipt['status'] == 'delivery_unknown' and not receipt['delivered']
    assert citation_check('[memory:omitted-id]', view)['status'] == 'invalid'
