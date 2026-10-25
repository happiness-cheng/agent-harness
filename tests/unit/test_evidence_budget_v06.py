import pytest
from min_agent.context import ContextLimit
from min_agent.models import Message,ModelResponse
from min_agent.long_memory.evidence_budget import CallLedger,MeteredModel


class FakeProvider:
    def __init__(self,usage=True):self.usage=[];self.calls=0;self.provide_usage=usage
    def generate(self,messages,*,tools=None):
        self.calls+=1
        if self.provide_usage:self.usage.append(dict(input_tokens=10,output_tokens=5))
        return ModelResponse(finish_reason='stop',content='{"answer":"ok","used_evidence_ids":[],"uncertainties":[]}')


def test_reserve_happens_before_provider_and_never_retries():
    provider=FakeProvider();ledger=CallLedger(max_requests=1,max_cost_usd=1)
    model=MeteredModel(provider,ledger)
    model.generate([Message(role='user',content='hi')],tools=[])
    with pytest.raises(ContextLimit):model.generate([Message(role='user',content='again')],tools=[])
    assert provider.calls==1 and ledger.calls==1 and len(ledger.rows)==1


@pytest.mark.parametrize('cap,token_cap',[(.000001,65536),(1,1)])
def test_fee_or_total_token_limit_blocks_before_provider(cap,token_cap):
    provider=FakeProvider();model=MeteredModel(provider,CallLedger(max_requests=2,max_cost_usd=cap),total_units=token_cap)
    with pytest.raises(ContextLimit):model.generate([Message(role='user',content='hi')],tools=[])
    assert provider.calls==0


def test_missing_usage_stops_subsequent_calls():
    provider=FakeProvider(usage=False);ledger=CallLedger(max_requests=3,max_cost_usd=1)
    model=MeteredModel(provider,ledger)
    with pytest.raises(ContextLimit):model.generate([Message(role='user',content='hi')],tools=[])
    with pytest.raises(ContextLimit):model.generate([Message(role='user',content='again')],tools=[])
    assert provider.calls==1 and ledger.stop_reason=='usage_unknown'
