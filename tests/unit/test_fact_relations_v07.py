from datetime import datetime,timezone,timedelta
import pytest
from experiments.memory_v07.relations import SourceExcerpt,extract_relations,conflicts,held_products

AT=datetime(2024,1,1,tzinfo=timezone.utc)
def source(text,role='user',**kwargs):
    return SourceExcerpt(id='fixture-source',owner='owner-a',role=role,text=text,observed_at=AT,**kwargs)


def test_different_account_purposes_are_not_a_conflict():
    relations=extract_relations(source('My payroll account number is 771039. My funding account number is 663048.'))
    assert {(r.subject,r.attribute,r.value) for r in relations}=={
        ('payroll account','number','771039'),('funding account','number','663048')}
    assert conflicts(relations,owner='owner-a',as_of=AT)==[]
    assert all(r.quote in r.source.text for r in relations)


def test_same_account_attribute_can_conflict_but_disjoint_scenarios_cannot():
    a=extract_relations(source('My payroll account number is 771039.'),scenario='work')
    b=extract_relations(source('My payroll account number is 663048.'),scenario='work')
    assert len(conflicts(a+b,owner='owner-a',as_of=AT))==1
    b=extract_relations(source('My payroll account number is 663048.'),scenario='family')
    assert conflicts(a+b,owner='owner-a',as_of=AT)==[]


def test_questions_and_discussion_never_prove_holding_or_coverage():
    assert extract_relations(source('Does Gold Card cover $800?'))==[]
    relations=extract_relations(source('I discussed Platinum Card. I applied for Silver Card. I hold Gold Card.'))
    assert held_products(relations,owner='owner-a',as_of=AT)==['Gold Card']
    assert {r.state for r in relations}=={'discussed','application_pending','held'}


def test_mixed_statement_question_preserves_the_statement_only():
    relations=extract_relations(source('Gold Card earns 3x points on flights. Does it cover $800?'))
    assert [(r.attribute,r.value) for r in relations]==[('flight_reward','3x')]


def test_historical_assistant_is_not_runtime_verification():
    relations=extract_relations(source('You hold Gold Card.','assistant'))
    assert not relations[0].world_verified
    assert held_products(relations,owner='owner-a',as_of=AT)==[]
    with pytest.raises(ValueError):source('You hold Gold Card.','assistant',runtime_verified=True)
    verified=extract_relations(source('You hold Gold Card.','tool',runtime_verified=True))
    assert verified[0].world_verified and held_products(verified,owner='owner-a',as_of=AT)==['Gold Card']


def test_cancelled_latest_status_owner_and_valid_time_remain_separate():
    old=extract_relations(source('I hold Gold Card.'))
    later=extract_relations(source('I cancelled Gold Card.').model_copy(update={'observed_at':AT+timedelta(days=1)}))
    assert held_products(old+later,owner='owner-a',as_of=AT)==['Gold Card']
    assert held_products(old+later,owner='owner-a',as_of=AT+timedelta(days=2))==[]
    assert held_products(old,owner='another-owner',as_of=AT)==[]
    expired=[old[0].model_copy(update={'valid_until':AT+timedelta(hours=1)})]
    assert held_products(expired,owner='owner-a',as_of=AT+timedelta(days=1))==[]


def test_holding_is_not_borrowed_from_another_scenario_and_approval_is_not_held():
    a=extract_relations(source('I hold Gold Card.'),scenario='work')
    b=extract_relations(source('I cancelled Gold Card.'),scenario='personal')
    assert held_products(a+b,owner='owner-a',as_of=AT,scenario='work')==['Gold Card']
    assert held_products(a+b,owner='owner-a',as_of=AT,scenario='personal')==[]
    approved=extract_relations(source('I was approved for Platinum Card.'))
    assert approved[0].state=='application_approved'
    assert held_products(approved,owner='owner-a',as_of=AT)==[]


def test_routing_number_is_not_the_account_number_and_keeps_leading_zero():
    facts=extract_relations(source('My payroll account number is 771039. My payroll account routing number is 036000187. My payroll account purpose is payroll.'))
    assert {(r.attribute,r.value) for r in facts}=={('number','771039'),('routing_number','036000187'),('purpose','payroll')}
