from pathlib import Path
from types import SimpleNamespace
import pytest

from min_agent.long_memory.answer_run import run_authorized,validate_authorization
from min_agent.long_memory.answer_eval import prepare_answer_case


def pack():
    case=dict(test_id='live-nonce',category='synthetic',user_question='What is the code?',conversation_histories=[dict(
        conversation_id='one',timestamp='2024-01-01T00:00:00Z',messages=[dict(role='user',content='Code is LUMEN-72.')])])
    row=prepare_answer_case(case,oracle_spec=[dict(conversation_id='one',message_index=0)])
    row.update(gold=dict(required_facts=['LUMEN-72']),evaluation_audit=[])
    return dict(cases=[row],model='deepseek-flash',reasoning_effort='high',max_output_tokens=4096,
        request_count=3,peak_usd_per_million=dict(input=.30,output=1.20),proposed_fee_cap_usd=1)


def auth():
    return dict(approved_by_user=True,approval_id='unit-only',user_confirmation_reference='UNIT_TEST_NOT_LIVE',
        pack_sha256='hash',model='deepseek-flash',reasoning_effort='high',max_requests=3,
        max_cost_usd=1,data_scope='public_book_and_synthetic',external_judge=False)


class Fake:
    def __init__(self):self.usage=[];self.request_count=0
    def generate(self,messages,*,tools):
        assert tools==[]
        self.request_count+=1;self.usage.append(dict(input_tokens=100,output_tokens=20))
        return SimpleNamespace(content='{"answer":"unknown","used_evidence_ids":[],"uncertainties":[]}',finish_reason='stop',tool_calls=[],provider_state=None)
    def close(self):pass


def test_without_authorization_factory_is_never_called(tmp_path):
    def forbidden():raise AssertionError('不能读取凭据或建立在线客户端')
    with pytest.raises(PermissionError):run_authorized(pack(),None,pack_sha='hash',factory=forbidden,journal=tmp_path/'r.jsonl',claim=tmp_path/'claim.json')


def test_exact_request_ceiling_and_one_use_claim_prevent_repeat(tmp_path):
    model=Fake();data=pack();permission=auth()
    result=run_authorized(data,permission,pack_sha='hash',factory=lambda:model,journal=tmp_path/'r.jsonl',claim=tmp_path/'claim.json')
    assert model.request_count==3 and len(result['responses'])==3
    assert result['total_peak_usage_cost_usd']>0
    with pytest.raises(FileExistsError):run_authorized(data,permission,pack_sha='hash',factory=lambda:model,journal=tmp_path/'other.jsonl',claim=tmp_path/'claim.json')
    assert model.request_count==3


@pytest.mark.parametrize('change',[{'max_requests':2},{'max_cost_usd':.000001},{'pack_sha256':'wrong'},{'external_judge':True}])
def test_quota_fee_hash_and_judge_rejected_before_factory(tmp_path,change):
    def forbidden():raise AssertionError('不应读取凭据')
    with pytest.raises((PermissionError,ValueError)):
        run_authorized(pack(),auth()|change,pack_sha='hash',factory=forbidden,journal=tmp_path/'r.jsonl',claim=tmp_path/'claim.json')


def test_missing_usage_stops_batch_instead_of_guessing_cost(tmp_path):
    class Missing(Fake):
        def generate(self,messages,*,tools):
            result=super().generate(messages,tools=tools);self.usage=[];return result
    model=Missing()
    result=run_authorized(pack(),auth(),pack_sha='hash',factory=lambda:model,journal=tmp_path/'r.jsonl',claim=tmp_path/'claim.json')
    assert model.request_count==1 and result['status']=='PARTIAL'
    assert result['responses'][0]['usage_status']=='UNKNOWN'
