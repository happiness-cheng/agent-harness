import gzip
from hashlib import sha256
import json
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
import eval_memory_value_v06 as trial
from min_agent.long_memory.active_evidence import validate_answer


def packet():
    path=ROOT/'reports/memory_v06/prepared.json.gz'
    return trial.load(path),sha256(path.read_bytes()).hexdigest()
def permission(digest):
    return dict(approved_by_user=True,pack_sha256=digest,model='deepseek-flash',reasoning_effort='high',
        data_scope='public_book_and_synthetic',external_judge=False,max_requests=105,max_cost_usd=2,
        tools=['search_long_memory','read_long_memory_topic','read_long_memory_evidence'],
        user_confirmation_reference='TEST_ONLY_NOT_AUTHORIZATION')


def test_unapproved_trial_cannot_read_credentials_or_create_claim(tmp_path):
    pack,digest=packet()
    with pytest.raises(PermissionError):trial.live(pack,{},digest,tmp_path/'missing-credentials','http://127.0.0.1:7890',tmp_path/'out.json')
    assert not (tmp_path/'out.json').exists()


@pytest.mark.parametrize('key,value',[('max_requests',106),('max_cost_usd',3),('external_judge',True),('model','other')])
def test_authorization_cannot_expand_scope(key,value):
    pack,digest=packet();auth=permission(digest);auth[key]=value
    with pytest.raises(PermissionError):trial.validate_permission(pack,auth,digest)


def test_policy_freeze_and_equal_prompts_and_reference_budget():
    pack,digest=packet();trial.validate_permission(pack,permission(digest),digest)
    assert len(pack['cases'])==15
    for case in pack['cases']:
        prompts=[messages[0]['content'] for messages in case['requests'].values()]
        assert len(set(prompts))==1
        for view in case['views'].values():
            assert len(trial.encoded(view))<=4096
        for ep in case['source_episodes']:
            assert trial.timestamp(ep['observed_at'])<=trial.timestamp(case['as_of'])


def test_one_authorization_cannot_be_replayed_by_changing_output(tmp_path,monkeypatch):
    pack,digest=packet();auth=permission(digest)
    monkeypatch.setattr(trial,'ROOT',tmp_path)
    monkeypatch.setattr(trial,'sources',lambda:pack['source_hashes'])
    claims=tmp_path/'reports/memory_v06/claims';claims.mkdir(parents=True)
    path=claims/(sha256((auth['user_confirmation_reference']+digest).encode()).hexdigest()+'.json')
    path.write_text('{}',encoding='utf-8')
    with pytest.raises(FileExistsError):trial.live(pack,auth,digest,tmp_path/'missing-credentials','proxy',tmp_path/'renamed-out.json')


def test_existing_real_responses_replay_structure_and_citation_error_without_new_calls():
    pack=trial.load(ROOT/'reports/memory_v05/prepared-pilot.json.gz')
    result=json.loads((ROOT/'reports/memory_v05/live-results.json').read_text(encoding='utf-8'))
    cases={c['case_id']:c for c in pack['cases']};checks=[]
    for response in result['responses']:
        ids=cases[response['case_id']]['groups'][response['group']]['visible_ids']
        checks.append(validate_answer(response['raw_answer'],set(ids)))
    assert sum(c['json_valid'] for c in checks)==26
    assert sum(c['schema_valid'] and not c['citation_valid'] for c in checks)==1
