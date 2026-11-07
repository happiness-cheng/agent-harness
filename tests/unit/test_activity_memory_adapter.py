"""只测试合成报告与临时沙盒，不访问任何真实平台历史。"""
import hashlib
import json

import pytest
import yaml

from min_agent.activity_memory_adapter import create_sandbox, demo, convert_report, candidate_id, write_bundle


def convert(value=None):
    return convert_report(report() if value is None else value, observed_at='2026-11-06T01:00:00Z')


def report(profile="codex/rollout"):
    return dict(format="adrian-readonly-activity/1",files=[dict(profile=profile,source_ref="source_"+"a"*20,
        object_id="id1_"+"b"*64,stable=True,complete=True,timeline=[dict(record_type="event_msg",
        timestamp="2026-01-01T01:00:00Z",line_index=1,logical_byte_offset=42,physical_byte_offset=42,
        seq_namespace="codex.ordinal" if profile.startswith("codex") else "dsh.seq",seq_value=1,
        native_record_id=None,provisional_record_id="id1_"+"c"*64,identity_status="provisional")])])


def test_conversion_is_observation_not_real_event():
    result=convert_report(report(),observed_at="2026-11-06T01:00:00Z")
    item=result['candidates'][0]
    assert item['review_state']=='draft' and item['status']=='unknown'
    assert item['activity_kind']=='conversation'
    assert item['activity_subtype']=='agent_activity_observation'
    assert item['event_at'] is None and item['conversation_at'] is None
    assert item['source_recorded_at']=='2026-01-01T01:00:00Z'
    assert not item['eligible_for_import']
    assert item['evidence_refs'][0]['original_reference_validity']=='unverified'
    assert item['adrian_ids']['provisional_record_id']=='id1_'+'c'*64


def test_idempotent_demo_and_valid_links():
    root=create_sandbox()
    first=demo(root);second=demo(root)
    assert first['candidate_ids']==second['candidate_ids']
    assert second['written']==0
    for p in (root/'vault'/'Candidates').glob('*.md'):
        metadata=yaml.safe_load(p.read_text(encoding='utf-8').split('---',2)[1])
        assert metadata['review_state']=='draft'
        assert metadata['evidence_refs']
    assert len(list((root/'vault'/'Candidates').glob('*.md')))==2


def test_platform_ids_do_not_collide():
    ids={candidate_id(p,'src_'+'d'*64,'/files/0/timeline/0') for p in ('Codex','DSH','ChatGPT','DeepSeek')}
    assert len(ids)==4


def test_source_hash_mapping_and_block_links():
    bundle=convert(); root=create_sandbox(); write_bundle(root,bundle)
    source=root/'vault'/'Sources'/f"{bundle['source_id']}.json"
    assert bundle['source_id']=='src_'+hashlib.sha256(source.read_bytes()).hexdigest()
    candidate=bundle['candidates'][0]; mapping=bundle['mappings'][0]
    assert mapping['adrian_ids']==candidate['adrian_ids']
    assert mapping['activity_id']==candidate['activity_id']
    assert mapping['relation']=='observed_in_report_not_identity_equivalence'
    link=candidate['evidence_refs'][0]['card_link']; path,block=link.split('#^')
    assert '^'+block in (root/'vault'/f'{path}.md').read_text(encoding='utf-8')


@pytest.mark.parametrize('missing', ['source_ref','line_index','seq_value','seq_namespace','logical_byte_offset'])
def test_missing_locator_never_fabricates_message_evidence(missing):
    value=report(); file=value['files'][0]; row=file['timeline'][0]
    (file if missing=='source_ref' else row).pop(missing)
    ref=convert(value)['candidates'][0]['evidence_refs'][0]
    assert ref['original_reference_validity']=='missing_locator'
    assert all(ref[k] is None for k in ('message_id','message_index','quote','role','line_anchor'))


@pytest.mark.parametrize('level', ['report','file','record'])
@pytest.mark.parametrize('flag', [{'sensitivity':'private'}, {'health_isolated':True}, {'sensitivity':'quarantined'}])
def test_isolated_metadata_not_exported(level,flag):
    value=report(); target={'report':value,'file':value['files'][0],'record':value['files'][0]['timeline'][0]}[level]
    target.update(flag)
    bundle=convert(value)
    assert bundle['candidates']==[] and bundle['mappings']==[] and bundle['skipped']
    assert json.loads(bundle['source_bytes'])['records']==[]


def test_untrusted_claims_body_paths_and_review_state_are_not_copied():
    value=report(); row=value['files'][0]['timeline'][0]
    row.update(body='SECRET_SYNTHETIC',token='SECRET_SYNTHETIC',title='SECRET_SYNTHETIC',
        quote='SECRET_SYNTHETIC',source_path='health/SECRET_SYNTHETIC',status='completed',
        activity_kind='user_reported_event',review_state='confirmed',event_at='2026-01-01T01:00:00Z')
    bundle=convert(value); root=create_sandbox(); write_bundle(root,bundle)
    assert all(b'SECRET_SYNTHETIC' not in p.read_bytes() for p in (root/'vault').rglob('*') if p.is_file())
    item=bundle['candidates'][0]
    assert item['status']=='unknown' and item['event_at'] is None and item['review_state']=='draft'


@pytest.mark.parametrize('state', ['confirmed','corrected','draft'])
def test_existing_notes_never_overwritten(state):
    root=create_sandbox(); bundle=convert(); write_bundle(root,bundle)
    path=root/'vault'/'Candidates'/f"{bundle['candidates'][0]['activity_id']}.md"
    data=f'---\nreview_state: {state}\n---\nUser edited synthetic note'.encode()
    path.write_bytes(data)
    result=write_bundle(root,bundle)
    assert path.read_bytes()==data and result['written']==0
    assert 'protected_existing_note' in result['file_statuses'].values()


def test_evidence_conflict_preflight_does_not_create_candidates():
    root=create_sandbox(); bundle=convert(); sources=root/'vault'/'Sources'; sources.mkdir(parents=True)
    (sources/f"{bundle['source_id']}.md").write_bytes(b'changed synthetic evidence')
    with pytest.raises(ValueError,match='existing_evidence_conflict'):
        write_bundle(root,bundle)
    assert not (root/'vault'/'Candidates').exists()
    assert not (sources/f"{bundle['source_id']}.json").exists()


@pytest.mark.parametrize('profile', ['chatgpt/export','deepseek/export',[]])
def test_unsupported_platform_does_not_guess(profile):
    value=report(); value['files'][0]['profile']=profile
    bundle=convert(value)
    assert bundle['candidates']==[] and bundle['skipped']==['unsupported_platform_adapter']


def test_unstable_incomplete_and_seq0_remain_explicit():
    value=report('dsh/old'); file=value['files'][0]; file.update(stable=False,complete=False)
    file['timeline'][0].update(seq_namespace='dsh.seq0',physical_byte_offset=None,record_type=[])
    item=convert(value)['candidates'][0]
    assert not item['upstream_stable'] and not item['upstream_complete']
    assert item['resolution_state']=='unresolved' and not item['eligible_for_import']
    assert item['evidence_refs'][0]['locator']['seq_namespace']=='dsh.seq0'
    assert item['evidence_refs'][0]['locator']['physical_byte_offset'] is None


def test_outside_sandbox_rejected_without_write(tmp_path):
    with pytest.raises(ValueError,match='output_not_adapter_sandbox'):
        write_bundle(tmp_path,convert())
    assert list(tmp_path.iterdir())==[]


def test_unexpected_input_is_rejected_before_scan(monkeypatch):
    root=create_sandbox(); inputs=root/'synthetic-inputs'; inputs.mkdir()
    (inputs/'private.jsonl').write_bytes(b'synthetic sentinel')
    monkeypatch.setattr('min_agent.activity_memory_adapter.scan',lambda _: pytest.fail('must not scan unexpected input'))
    with pytest.raises(ValueError,match='unexpected_synthetic_input'):
        demo(root)


def test_four_platform_original_sentinels_unchanged(tmp_path):
    paths=[tmp_path/f'{platform}.json' for platform in ('Codex','DSH','ChatGPT','DeepSeek')]
    for path in paths:
        path.write_bytes(b'synthetic sentinel, never input to adapter')
    before={p:p.read_bytes() for p in paths}
    root=create_sandbox(); demo(root)
    inputs={p:p.read_bytes() for p in (root/'synthetic-inputs').iterdir()}
    demo(root)
    assert all(p.read_bytes()==data for p,data in {**before,**inputs}.items())


def test_cli_replay():
    import subprocess
    import sys
    command=[sys.executable,'-X','utf8','-m','min_agent.activity_memory_adapter']
    first=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',check=True)
    root=json.loads(first.stdout)['sandbox']
    second=subprocess.run(command+['--sandbox',root],capture_output=True,text=True,encoding='utf-8',check=True)
    assert json.loads(second.stdout)['written']==0


def test_linked_output_refused_before_write(monkeypatch):
    from min_agent import activity_memory_adapter as adapter
    root=create_sandbox(); target=root/'vault'; target.mkdir()
    original=adapter.is_link
    monkeypatch.setattr(adapter,'is_link',lambda path: path==target or original(path))
    with pytest.raises(ValueError,match='linked_output_refused'):
        write_bundle(root,convert())
    assert list(target.iterdir())==[]


@pytest.mark.parametrize('timestamp', [None,'not-a-date','2026-01-01T00:00:00'])
def test_missing_or_unzoned_time_not_promoted(timestamp):
    value=report(); value['files'][0]['timeline'][0]['timestamp']=timestamp
    item=convert(value)['candidates'][0]
    assert item['source_recorded_at'] is None and item['event_at'] is None


def test_missing_observation_time_rejected():
    with pytest.raises(ValueError,match='unsupported_report_or_observation_time'):
        convert_report(report(),observed_at=None)


def test_known_limits_preserved_without_free_text_leak():
    from min_agent.activity_connector import LIMITS
    value=report(); value['known_limits']=[*LIMITS, 'SECRET_SYNTHETIC']
    item=convert(value)['candidates'][0]
    assert item['upstream_known_limits']==LIMITS
    assert 'SECRET_SYNTHETIC' not in json.dumps(item)
