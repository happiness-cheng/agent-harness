from min_agent.long_memory.fact_binding import local_bindings,binding_rerank
from min_agent.long_memory.evaluation import build_approved_fixture
from min_agent.long_memory import Query


def test_mixed_statement_and_question_bind_only_the_asserted_clause():
    rows=local_bindings('Room 2841 is reserved for you. Would you like dinner reservations?')
    assert any(r['attribute']=='room_number' and r['value']=='2841' and r['status']=='asserted' for r in rows)
    assert not local_bindings('Could you tell me the room number?')


def test_distinct_identifier_types_and_negation_are_preserved():
    rows=local_bindings('Your membership number is LP-9911; your confirmation number is REF-7732. Your order number is not ORD-22.')
    assert {(r['attribute'],r['value']) for r in rows}>={('membership_id','LP-9911'),('reservation_id','REF-7732')}
    assert any(r['attribute']=='order_id' and r['status']=='negated' for r in rows)
    assert local_bindings('Her number is HH9911911.')==[]  # 不猜邻句/代词的具体属性。


def test_stable_binding_partition_changes_no_numeric_weights_or_pool():
    case=dict(test_id='binding-v04',category='synthetic',conversation_histories=[dict(conversation_id='hotel',
        timestamp='2026-01-01T00:00:00Z',metadata={'business':'Lumen Hotel'},messages=[
            dict(role='assistant',content='Your membership number is LP-9911.'),
            dict(role='assistant',content='Your confirmation number is REF-7732.')])])
    memory,ctx,info=build_approved_fixture(case,approval='approved_fixture')
    records=memory.records(ctx,as_of=info['last_time'])
    original=[dict(memory_id=m.id,score=9-i) for i,m in enumerate(records)]
    result=binding_rerank(memory,ctx,Query(text='Lumen confirmation number',as_of=info['last_time']),original)
    assert {h['memory_id'] for h in result['ranked']}=={h['memory_id'] for h in original}
    assert result['ranked'][0]['memory_id']==records[1].id
    assert result['ranked'][0]['score']==8


def test_neighbor_value_never_inherits_an_entity_or_attribute():
    assert local_bindings('NOVA-3312')==[]
    assert local_bindings('Your number is NOVA-3312')==[]
