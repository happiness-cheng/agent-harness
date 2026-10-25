from min_agent.long_memory.evidence_context import assemble_evidence
from min_agent.long_memory.evaluation import build_approved_fixture
from min_agent.long_memory import Query


def fixture(text):
    case=dict(test_id='evidence-v04',category='synthetic',conversation_histories=[dict(conversation_id='policy',
        timestamp='2026-01-01T00:00:00Z',metadata={'business':'Lumen'},messages=[dict(role='assistant',content=text)])])
    memory,ctx,info=build_approved_fixture(case,approval='approved_fixture')
    rows=memory.records(ctx,as_of=info['last_time'])
    return memory,ctx,Query(text='Lumen room number',as_of=info['last_time']),[dict(memory_id=m.id,score=1) for m in rows]


def test_extracts_only_verbatim_sentences_and_keeps_conditions_and_time():
    text='Welcome, happy to help. Room 2841 is reserved. It is not available before March 12. Only if your payment clears. Would you like dinner?'
    memory,ctx,query,hits=fixture(text)
    result=assemble_evidence(memory,ctx,query,hits,strategy='P1',budget=2048)
    entry=result['entries'][0]
    quotes=[q['text'] for q in entry['quotes']]
    assert 'Welcome, happy to help.' not in quotes
    assert 'Room 2841 is reserved.' in quotes
    assert any('not available before March 12' in q for q in quotes)
    assert 'Only if your payment clears.' in quotes
    assert all(text[q['start']:q['end']]==q['text'] for q in entry['quotes'])
    assert entry['scene']=='policy' and entry['entity_scope']==['Lumen']
    assert result['bytes']<=2048 and result['mechanical_complete']


def test_no_fragment_is_published_when_complete_evidence_cannot_fit():
    memory,ctx,query,hits=fixture('Room 2841 is reserved. It is not available before March 12.')
    for strategy in ['P0','P1','P2']:
        result=assemble_evidence(memory,ctx,query,hits,strategy=strategy,budget=30)
        assert not result['entries'] and result['bytes']==0


def test_duplicate_hits_and_foreign_ids_never_add_context_or_mutate_source():
    memory,ctx,query,hits=fixture('Room 2841 is reserved.')
    before=memory.document(ctx).model_dump_json()
    result=assemble_evidence(memory,ctx,query,hits+hits+[dict(memory_id='foreign',score=2)],strategy='P2')
    assert len(result['selected_ids'])==1
    assert before==memory.document(ctx).model_dump_json()


def test_assembly_rechecks_owner_expiry_scenario_and_withdrawal():
    from uuid import uuid4
    from datetime import timedelta
    from test_long_memory_store import setup_memory,commit
    _,ctx,clock,memory=setup_memory()
    visible=commit(memory,ctx,memory.propose(ctx,'Room 2841 is reserved.',source_indices=[0]))
    scene=memory.register_scenario(ctx,'other')
    hidden=commit(memory,ctx,memory.propose(ctx,'Room 2942 is reserved.',source_indices=[0],scenario_ids=[scene.id]))
    future=commit(memory,ctx,memory.propose(ctx,'Room 3043 is reserved.',source_indices=[0],effective_from=clock.now+timedelta(days=1)))
    query=Query(text='room number',as_of=clock.now)
    hits=[dict(memory_id=m.id,score=1) for m in [visible,hidden,future]]
    assert assemble_evidence(memory,ctx,query,hits,strategy='P2')['selected_ids']==[visible.id]
    other=memory.sessions.create(uuid4())
    assert not assemble_evidence(memory,other,query,hits,strategy='P1')['entries']
    memory.delete(ctx,visible.id)
    assert not assemble_evidence(memory,ctx,query,hits,strategy='P1')['entries']


def test_future_topic_metadata_is_not_disclosed_and_expired_evidence_is_not_packed():
    from datetime import timedelta
    memory,ctx,query,hits=fixture('Room 2841 is reserved.')
    def change(doc,at):
        for topic_id,topic in list(doc.topics.items()):
            doc.topics[topic_id]=topic.model_copy(update={'name':'FUTURE_SECRET','created_at':query.as_of+timedelta(days=1)})
    memory._write(ctx,change)
    assert assemble_evidence(memory,ctx,query,hits,strategy='P1')['entries'][0]['entity_scope']==[]
    def expire(doc,at):
        for identity,versions in list(doc.memories.items()):
            doc.memories[identity]=[versions[-1].model_copy(update={'valid_until':query.as_of})]
    memory._write(ctx,expire)
    assert not assemble_evidence(memory,ctx,query,hits,strategy='P1')['entries']
