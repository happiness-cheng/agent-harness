import pytest
from min_agent.models import Message,ModelResponse,ProviderState,ToolCall
from experiments.memory_v07.budget import BudgetAccount,BudgetBlocked,request_breakdown


def test_breakdown_uses_real_protocol_without_duplicate_native_call():
    call=ToolCall(id='q1',name='search_long_memory',arguments={'question':'account'})
    state=ProviderState(provider='deepseek.responses',model='deepseek-flash',response_model='deepseek-flash',output=[
        {'type':'reasoning','content':[{'type':'reasoning_text','text':'SYNTHETIC_PADDING'*20}]},
        {'type':'function_call','call_id':call.id,'name':call.name,'arguments':'{"question":"account"}'}])
    messages=[Message(role='system',content='trusted rules'),Message(role='user',content='question'),
        Message(role='assistant',content='',tool_calls=[call],provider_state=state),
        Message(role='tool',content='{"ok":true,"result":"id"}',tool_call_id='q1'),
        Message(role='user',content='{"application_state":{"mode":"plan"},"untrusted_user_memory":{"evidence":[]}}')]
    report=request_breakdown(messages,[])
    assert report['system_count']==1 and report['dynamic_projection_count']==1
    assert report['history_tool_message_bytes']>0 and report['native_state_bytes']>0
    assert report['wire_function_calls']==1
    assert report['wire_body_bytes']>0 and report['output_reserve']==4096
    assert report['current_memory_projection_bytes']>0
    assert 'SYNTHETIC_PADDING' not in str(report)


def test_completed_requests_settle_actual_tokens_without_raising_limits():
    account=BudgetAccount()
    d=request_breakdown([Message(role='user',content='x'*15000)],[])
    assert d['request_upper_units']*3>65536
    for _ in range(3):
        account.begin(d)
        account.settle(dict(input_tokens=4000,output_tokens=100))
    assert account.actual_tokens==12300
    assert account.single_limit==32768 and account.total_limit==65536
    assert account.pending_units==0


def test_single_and_cumulative_limits_are_distinct_and_not_disabled():
    account=BudgetAccount()
    d=request_breakdown([Message(role='user',content='x'*33000)],[])
    with pytest.raises(BudgetBlocked,match='single_request_limit'):account.begin(d)
    account=BudgetAccount()
    d=request_breakdown([Message(role='user',content='x'*20000)],[])
    for _ in range(2):account.begin(d);account.settle(dict(input_tokens=20000,output_tokens=1000))
    with pytest.raises(BudgetBlocked,match='cumulative_token_limit'):account.begin(d)


@pytest.mark.parametrize('usage',[None,{},dict(input_tokens=-1,output_tokens=5),dict(input_tokens=True,output_tokens=5)])
def test_unknown_usage_cannot_release_reservation_or_continue(usage):
    account=BudgetAccount();d=request_breakdown([Message(role='user',content='hello')],[])
    account.begin(d)
    with pytest.raises(BudgetBlocked,match='usage_unknown'):account.settle(usage)
    assert account.pending_units>0
    with pytest.raises(BudgetBlocked):account.begin(d)


def test_reported_overflow_stops_without_faking_a_small_usage():
    account=BudgetAccount();d=request_breakdown([Message(role='user',content='hello')],[])
    account.begin(d)
    with pytest.raises(BudgetBlocked,match='usage_exceeds_reservation'):
        account.settle(dict(input_tokens=100000,output_tokens=5))
