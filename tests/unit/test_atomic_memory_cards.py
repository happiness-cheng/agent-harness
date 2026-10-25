from datetime import timedelta

import pytest

from min_agent.long_memory import Query
from min_agent.long_memory.evaluation import build_approved_fixture
from min_agent.long_memory.atomic_cards import QuoteRef, build_atomic_card, validate_atomic_card


def fixture():
    case = dict(test_id='atomic-nonce',category='synthetic',conversation_histories=[
        dict(conversation_id='lumen',timestamp='2026-01-01T00:00:00Z',messages=[
            dict(role='assistant',content='Lumen offers a membership.'),
            dict(role='assistant',content='Which tier do you choose?'),
            dict(role='user',content='I choose Amber.')]),
        dict(conversation_id='nova',timestamp='2026-02-01T00:00:00Z',messages=[
            dict(role='assistant',content='Nova also offers Amber.')])])
    memory,ctx,info = build_approved_fixture(case,approval='approved_fixture')
    rows = memory.records(ctx,as_of=info['last_time'])
    def ref(value,contains=None):
        source = next(m for m in rows if value in m.content and (contains is None or contains in m.content))
        text=source.content.split('] ',1)[1];start=text.index(value)
        return QuoteRef(episode_id=source.id,start=start,end=start+len(value),quote=value)
    return memory,ctx,Query(text='membership tier',as_of=info['last_time']),ref


def test_cross_message_binding_keeps_evidence_and_inference_pending():
    memory,ctx,query,ref = fixture()
    card = build_atomic_card(memory,ctx,query,subject=ref('Lumen'),attribute=ref('tier'),value=ref('Amber','I choose'),approval='approved_fixture')
    assert (card.subject,card.attribute,card.value)==('Lumen','tier','Amber')
    assert card.claim_kind=='inferred' and card.evidence_type=='INFERRED'
    assert card.confirmation_status=='pending' and len(card.evidence_refs)==3
    assert validate_atomic_card(memory,ctx,query,card)
    assert len(memory.document(ctx).memories)==4


@pytest.mark.parametrize('error',['different_event','fabricated_value','future','explicit_upgrade','unapproved'])
def test_bad_associations_do_not_become_facts(error):
    memory,ctx,query,ref = fixture()
    subject,attribute,value=ref('Lumen'),ref('tier'),ref('Amber','I choose')
    extra={}
    if error=='different_event':value=ref('Amber','Nova')
    if error=='fabricated_value':value=value.model_copy(update={'quote':'MISSING-600'})
    if error=='future':query=query.model_copy(update={'as_of':query.as_of-timedelta(days=40)})
    if error=='explicit_upgrade':extra['claim_kind']='explicit'
    with pytest.raises((PermissionError,ValueError)):
        build_atomic_card(memory,ctx,query,subject=subject,attribute=attribute,value=value,
                          approval='none' if error=='unapproved' else 'approved_fixture',**extra)


def test_atomic_derivative_becomes_invalid_after_source_withdrawal():
    from min_agent.task_state import TaskController,GoalSpec
    memory,ctx,query,ref=fixture()
    card=build_atomic_card(memory,ctx,query,subject=ref('Lumen'),attribute=ref('tier'),value=ref('Amber','I choose'),approval='approved_fixture')
    controller=TaskController(memory.sessions)
    proposal=controller.propose(ctx,GoalSpec(objective='撤回测试来源',completion_criteria=('撤回指定来源',)))
    controller.confirm(ctx,proposal.proposal_id);controller.select_mode(ctx,'build')
    memory.delete(ctx,ref('tier').episode_id)
    assert not validate_atomic_card(memory,ctx,query,card)
