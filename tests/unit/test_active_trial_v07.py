from pathlib import Path
import pytest
from min_agent.models import ModelResponse
from min_agent.long_memory.evidence_budget import CallLedger
from experiments.memory_v07.trial import run_trial,validate_authorization,prepare_pack
from test_active_loop_v07 import NativeMock


def packet():return prepare_pack()


def test_preflight_settings_and_packet_do_not_require_credentials():
    pack=packet()
    assert len(pack['cases'])==3 and pack['max_requests']==12
    assert pack['max_cost_usd']==.5 and pack['max_output_tokens']==4096
    assert all('required_facts' not in str(c['b0_messages']) and 'must_not_do' not in str(c['b0_messages']) for c in pack['cases'])
    with pytest.raises(PermissionError):validate_authorization(pack,{},'not-a-hash')


def test_first_b2_budget_block_stops_every_remaining_case():
    from min_agent.models import ProviderState
    instances=[]
    class HugeState(NativeMock):
        def generate(self,messages,*,tools=None):
            response=super().generate(messages,tools=tools)
            if len(self.requests)==2 and response.provider_state:
                output=list(response.provider_state.output)
                output[0]={'type':'reasoning','content':[{'type':'reasoning_text','text':'x'*20000}]}
                response=response.model_copy(update={'provider_state':response.provider_state.model_copy(update={'output':output})})
            return response
        def close(self):pass
    class Baseline:
        def __init__(self):self.usage=[]
        def generate(self,*a,**k):
            self.usage.append(dict(input_tokens=3500,output_tokens=100))
            return ModelResponse(finish_reason='stop',content='{"answer":"MOCK_ONLY","used_evidence_ids":[],"uncertainties":[]}')
        def close(self):pass
    def factory(group):
        model=HugeState() if group=='B2' else Baseline();instances.append((group,model));return model
    row=run_trial(packet(),factory=factory,ledger=CallLedger(max_requests=12,max_cost_usd=.5),backend='MOCK')
    assert row['status']=='STOPPED_ON_B2_BUDGET'
    assert len(row['cells'])==2
    assert row['cells'][-1]['stop_reason']=='single_request_limit'
    assert [g for g,_ in instances]==['B0','B2']
    assert row['provider_calls']==3
