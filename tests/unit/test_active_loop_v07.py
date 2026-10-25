import json
import pytest
from min_agent.models import ModelResponse,ProviderState
from experiments.memory_v07.runtime_loop import run_active_v07
from min_agent.long_memory.evidence_budget import CallLedger
from test_task_evidence_v06 import fixture
from test_active_evidence_v06 import ScriptedSearchRead


class NativeMock(ScriptedSearchRead):
    """合成原生状态和受控usage，绝不是模型质量成绩。"""
    def __init__(self):super().__init__();self.usage=[]
    def generate(self,messages,*,tools=None):
        response=super().generate(messages,tools=tools)
        self.usage.append(dict(input_tokens=3500,output_tokens=100))
        if response.tool_calls:
            output=[{'type':'reasoning','content':[{'type':'reasoning_text','text':'SYNTHETIC_NATIVE_PADDING'*30}]}]
            output += [dict(type='function_call',call_id=c.id,name=c.name,arguments=json.dumps(c.arguments)) for c in response.tool_calls]
            response=response.model_copy(update={'provider_state':ProviderState(provider='deepseek.responses',
                model='deepseek-flash',response_model='deepseek-flash',output=output)})
        return response


def test_real_runtime_delivers_evidence_and_finishes_with_same_limits():
    memory,ctx,q=fixture();model=NativeMock()
    row=run_active_v07(memory,ctx,q,provider=model,ledger=CallLedger(max_requests=3,max_cost_usd=1))
    assert row['runtime_status']=='completed'
    assert row['provider_calls']==3 and row['tool_calls']==2
    assert row['validation']['schema_valid'] and row['validation']['citation_valid']
    assert len(row['delivered_requests'])==3
    assert not row['delivered_requests'][0]['reference'].get('evidence')
    assert '792015' in json.dumps(row['delivered_requests'][2]['reference'])
    assert row['actual_tokens']==10800
    assert all(e['single_limit']==32768 and e['cumulative_limit']==65536 for e in row['budget_events'])
    assert 'SYNTHETIC_NATIVE_PADDING' not in json.dumps(row)


def test_same_native_mock_exposes_legacy_reservation_block_before_fix():
    from min_agent.long_memory.active_evidence import run_active
    memory,ctx,q=fixture()
    old=run_active(memory,ctx,q,model=NativeMock())
    assert old['runtime_status']=='partial' and old['error_code']=='context_limit'
    memory,ctx,q=fixture()
    new=run_active_v07(memory,ctx,q,provider=NativeMock(),ledger=CallLedger(max_requests=3,max_cost_usd=1))
    assert new['runtime_status']=='completed' and new['provider_calls']==3


def test_model_round_gate_is_not_mislabeled_as_context_limit():
    memory,ctx,q=fixture()
    row=run_active_v07(memory,ctx,q,provider=NativeMock(),ledger=CallLedger(max_requests=3,max_cost_usd=1),max_model_calls=2)
    assert row['runtime_status']=='partial' and row['provider_calls']==2
    assert row['stop_reason']=='model_call_limit'


def test_unknown_usage_stops_without_sending_a_second_request():
    class Unknown(NativeMock):
        def generate(self,*args,**kwargs):
            response=super().generate(*args,**kwargs);self.usage.clear();return response
    memory,ctx,q=fixture();model=Unknown()
    row=run_active_v07(memory,ctx,q,provider=model,ledger=CallLedger(max_requests=3,max_cost_usd=1))
    assert row['runtime_status']=='partial' and len(model.requests)==1
    assert row['stop_reason']=='usage_unknown'


def test_tool_gate_and_global_call_gate_remain_enforced():
    memory,ctx,q=fixture()
    tool=run_active_v07(memory,ctx,q,provider=NativeMock(),ledger=CallLedger(max_requests=3,max_cost_usd=1),max_tool_calls=1)
    assert tool['runtime_status']=='partial' and tool['stop_reason']=='tool_call_limit'
    assert tool['tool_calls']==1
    memory,ctx,q=fixture();model=NativeMock()
    global_gate=run_active_v07(memory,ctx,q,provider=model,ledger=CallLedger(max_requests=2,max_cost_usd=1))
    assert global_gate['provider_calls']==2 and global_gate['stop_reason']=='external_call_limit'


def test_new_loop_preserves_existing_owner_and_deletion_dependencies():
    from test_long_memory_store import setup_memory,candidate,commit
    from min_agent.long_memory.models import Query
    sessions,ctx,clock,memory=setup_memory()
    item=commit(memory,ctx,candidate(memory,ctx))
    q=Query(text='Python',as_of=clock.now)
    row=run_active_v07(memory,ctx,q,provider=NativeMock(),ledger=CallLedger(max_requests=3,max_cost_usd=1))
    assert row['runtime_status']=='completed'
    assert 'SCRIPTED_CONTROL' in sessions.snapshot(ctx).model_dump_json()
    memory.delete(ctx,item.id)
    assert 'SCRIPTED_CONTROL' not in sessions.snapshot(ctx).model_dump_json()
