"""只读 Connector 固定合成数据；绝不复制真实历史正文。"""
import json
import hashlib
import subprocess
import sys

import pytest
import zstandard

from min_agent.activity_connector import scan
from min_agent.activity_connector import main


def codex():
    return [dict(type="session_meta", ordinal=0, timestamp="2026-01-01T00:00:00Z", payload={"id": "synthetic-thread"}),
            dict(type="event_msg", ordinal=1, timestamp="2026-01-01T00:01:00Z", payload={"type": "user_message", "message": "PRIVATE_SECRET"})]


def dsh(version=4):
    return [dict(type="session", version=version, id="synthetic-session", createdAt="2026-01-01T00:00:00Z"),
            dict(type="user/message", seq=1, time="2026-01-01T00:01:00Z", data={"text": "PRIVATE_SECRET"})]


def raw(rows):
    return b"".join(json.dumps(r).encode() + b"\n" for r in rows)


def write(path, rows, compressed=False):
    data=raw(rows)
    path.write_bytes(zstandard.ZstdCompressor().compress(data) if compressed else data)
    return path


@pytest.mark.parametrize("rows,profile", [(codex(),"codex/rollout"),(dsh(),"dsh/v4"),(dsh(0),"dsh/old")])
@pytest.mark.parametrize("compressed", [False,True])
def test_formats_readonly_and_redacted(tmp_path, rows, profile, compressed):
    p=write(tmp_path/('input.zst' if compressed else 'input.jsonl'),rows,compressed)
    before=hashlib.sha256(p.read_bytes()).hexdigest()
    result=scan(p)
    assert result['files_scanned']==1
    f=result['files'][0]
    assert f['profile']==profile
    assert f['parsed_records']==2 and f['failed_records']==0
    assert f['stable'] and f['complete']
    assert 'PRIVATE_SECRET' not in json.dumps(result)
    assert 'synthetic-thread' not in json.dumps(result)
    assert str(tmp_path) not in json.dumps(result)
    assert hashlib.sha256(p.read_bytes()).hexdigest()==before
    assert result==scan(p)


def test_dsh_old_namespaces_remain_separate(tmp_path):
    rows=dsh(0)+[dict(type="assistant/message",seq0=1,time0="2026-01-01T00:02:00Z",data={"text":"private"})]
    f=scan(write(tmp_path/'old.zstd',rows,True))['files'][0]
    assert {r['seq_namespace'] for r in f['timeline']}=={'dsh.seq','dsh.seq0'}
    assert len({r['provisional_record_id'] for r in f['timeline']})==2


def test_plain_and_zstd_logical_consistency(tmp_path):
    a=scan(write(tmp_path/'a.jsonl',codex()))['files'][0]
    b=scan(write(tmp_path/'b.zst',codex(),True))['files'][0]
    assert a['logical_sha256']==b['logical_sha256']
    assert a['object_id']==b['object_id']
    assert a['by_type']==b['by_type']
    assert b['timeline'][0]['physical_byte_offset'] is None
    assert a['timeline'][0]['logical_byte_offset']==b['timeline'][0]['logical_byte_offset']


@pytest.mark.parametrize('tail,code',[(b'{bad}\n','invalid_json_or_record_shape'),
    (b'{"type":"event_msg","payload":{}}','incomplete_line'),
    (b'{"type":"event_msg","type":"response_item","payload":{}}\n','invalid_json_or_record_shape'),
    (b'\xff\n','invalid_json_or_record_shape')])
def test_corrupt_lines_are_explicit_and_never_echoed(tmp_path,tail,code):
    p=tmp_path/'bad.jsonl';p.write_bytes(raw(codex())+tail)
    r=scan(p)['files'][0]
    assert r['parsed_records']==2 and r['failed_records']==1
    assert r['status']=='partial' and not r['complete']
    assert code in {e['code'] for e in r['errors']}
    assert r['errors'][0]['line_index']==2
    assert r['errors'][0]['logical_byte_offset']==len(raw(codex()))


@pytest.mark.parametrize('damage',['truncate','trailing_junk'])
def test_zstd_requires_complete_frames(tmp_path,damage):
    p=write(tmp_path/'bad.zstd',codex(),True)
    data=p.read_bytes();p.write_bytes(data[:-2] if damage=='truncate' else data+b'PRIVATE_SECRET')
    r=scan(p)['files'][0]
    assert not r['complete'] and r['status']=='partial'
    assert {'unfinished_zstd_stream','invalid_zstd_stream'} & {e['code'] for e in r['errors']}
    assert 'PRIVATE_SECRET' not in json.dumps(r)


def test_concatenated_zstd_frames_are_fully_consumed(tmp_path):
    p=tmp_path/'multi.zst';c=zstandard.ZstdCompressor()
    rows=codex();p.write_bytes(c.compress(raw(rows[:1]))+c.compress(raw(rows[1:])))
    r=scan(p)['files'][0]
    assert r['complete'] and r['stable'] and r['parsed_records']==2


def test_missing_fields_provisional_without_invented_ordinal(tmp_path):
    rows=codex();del rows[1]['ordinal']
    r=scan(write(tmp_path/'missing.jsonl',rows))['files'][0]
    assert r['timeline'][0]['native_record_id'] is None
    assert r['timeline'][0]['seq_value'] is None
    assert r['timeline'][0]['locator_confidence']=='UNRELIABLE'
    assert 'missing_required_locator' in {e['code'] for e in r['errors']}


@pytest.mark.parametrize('fields,code', [({'seq':1,'seq0':1},'ambiguous_sequence_namespace'),({},'missing_sequence_namespace')])
def test_old_namespace_not_guessed(tmp_path,fields,code):
    rows=dsh(0);del rows[1]['seq'];rows[1].update(fields)
    r=scan(write(tmp_path/'old.jsonl',rows))['files'][0]
    assert r['failed_records']==1 and not r['timeline']
    assert code in {e['code'] for e in r['errors']}


@pytest.mark.parametrize('row',[dict(type='session',version=99,id='private'),dict(type='event_msg',payload={}),dict(type='unrecognized',payload={})])
def test_unknown_header_never_infers_source_from_path(tmp_path,row):
    r=scan(write(tmp_path/'codex-rollout.jsonl',[row]))['files'][0]
    assert r['profile'] is None and r['object_id'] is None
    assert 'unsupported_format' in {e['code'] for e in r['errors']}


def test_multiple_headers_stop_identity_attribution(tmp_path):
    rows=codex()+codex()
    r=scan(write(tmp_path/'fork.jsonl',rows))['files'][0]
    assert r['object_id'] is None and not r['complete']
    assert 'unresolved_multiple_session_headers' in {e['code'] for e in r['errors']}


def test_duplicates_and_moved_files_not_merged(tmp_path):
    a=write(tmp_path/'a.jsonl',codex())
    before=scan(a)['files'][0]
    b=write(tmp_path/'b.jsonl',codex())
    after=scan(b)['files'][0]
    assert before['object_id']==after['object_id']
    assert before['source_ref']!=after['source_ref']
    r=scan(tmp_path)
    assert r['files_scanned']==2 and r['parsed_records']==4
    assert all(f['relationship']=='unresolved_same_object_multiple_files' for f in r['files'])
    assert all(row['native_record_id'] is None for f in r['files'] for row in f['timeline'])


def test_unknown_types_and_errors_do_not_leak_content(tmp_path):
    rows=dsh();rows[1]['type']='sk-PRIVATE_SECRET';rows[1]['time']='PRIVATE_SECRET';rows[1]['data']['token']='PRIVATE_SECRET'
    r=scan(write(tmp_path/'PRIVATE_SECRET.jsonl',rows))
    assert 'PRIVATE_SECRET' not in json.dumps(r)
    assert r['files'][0]['timeline'][0]['record_type']=='other'


def test_fractional_timestamps_range_is_chronological(tmp_path):
    rows=codex();rows[1]['timestamp']='2026-01-01T00:00:00.1Z'
    r=scan(write(tmp_path/'t.jsonl',rows))
    assert r['time_range']==dict(start='2026-01-01T00:00:00.000000Z',end='2026-01-01T00:00:00.100000Z')


def test_limits_are_reported_not_normal_eof(tmp_path):
    rows=codex();rows[1]['payload']['message']='x'*4096
    p=write(tmp_path/'big.jsonl',rows)
    r=scan(p,max_line=512)['files'][0]
    assert r['failed_records']==1 and r['errors'][0]['code']=='line_size_limit'
    for options,code in [({'max_bytes':10},'physical_size_limit'),({'max_records':1},'record_count_limit')]:
        f=scan(p,**options)['files'][0]
        assert not f['complete'] and code in {e['code'] for e in f['errors']}


def test_timeline_limit_preserves_counts(tmp_path):
    rows=codex()+[dict(type='event_msg',ordinal=2,payload={})]
    f=scan(write(tmp_path/'x.jsonl',rows),timeline_limit=1)['files'][0]
    assert f['parsed_records']==3 and f['timeline_omitted']==1 and len(f['timeline'])==1


def test_mutation_between_passes_marks_unstable(tmp_path,monkeypatch):
    from min_agent import activity_reader
    p=write(tmp_path/'race.jsonl',codex());original=activity_reader.hash_readonly
    def altered(path,limit):
        with p.open('ab') as f:f.write(b'\n')
        return original(path,limit)
    monkeypatch.setattr(activity_reader,'hash_readonly',altered)
    r=scan(p)['files'][0]
    assert not r['stable'] and r['status']=='partial'
    assert 'unstable_or_unverified_read' in {e['code'] for e in r['errors']}


def test_cli_does_not_overwrite_or_write_into_inputs(tmp_path,capsys):
    inputs=tmp_path/'inputs';inputs.mkdir();p=write(inputs/'x.jsonl',codex())
    before=p.read_bytes()
    assert main([str(p),'--output',str(p)])==2
    assert main([str(inputs),'--output',str(inputs/'report.json')])==2
    out=tmp_path/'report.json'
    assert main([str(p),'--output',str(out)])==0
    original=out.read_bytes()
    assert main([str(p),'--output',str(out)])==2
    assert out.read_bytes()==original and p.read_bytes()==before
    assert 'PRIVATE_SECRET' not in out.read_text()


def test_cli_module_runs_in_subprocess(tmp_path):
    p=write(tmp_path/'x.zst',codex(),True)
    result=subprocess.run([sys.executable,'-m','min_agent.activity_connector',str(p)],capture_output=True,timeout=20)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['parsed_records']==2
    assert b'PRIVATE_SECRET' not in result.stdout+result.stderr


def test_symlink_or_reparse_not_followed(tmp_path,monkeypatch):
    from min_agent import activity_connector
    p=write(tmp_path/'link.jsonl',codex())
    monkeypatch.setattr(activity_connector,'is_link',lambda path:True)
    r=scan(p)
    assert r['files_scanned']==0 and r['scan_errors']==['symlink_or_reparse_point_skipped']


def test_streaming_zstd_unknown_content_size_window(tmp_path):
    p=tmp_path/'stream.zstd'
    params=zstandard.ZstdCompressionParameters.from_level(3,window_log=21,write_content_size=0)
    with p.open('wb') as f, zstandard.ZstdCompressor(compression_params=params).stream_writer(f) as writer:
        writer.write(raw(dsh()))
    r=scan(p)['files'][0]
    assert r['stable'] and r['complete'] and r['parsed_records']==2


@pytest.mark.parametrize('record_type',['world_state','token_usage_record'])
def test_observed_codex_metadata_types_without_inferred_semantics(tmp_path,record_type):
    rows=codex()+[dict(type=record_type,ordinal=2,timestamp='2026-01-01T00:02:00Z',payload={'secret':'PRIVATE_SECRET'})]
    r=scan(write(tmp_path/'extra.jsonl',rows))
    assert r['failed_records']==0 and r['parsed_records']==3
    assert r['by_type'][record_type]==1
    assert 'PRIVATE_SECRET' not in json.dumps(r)


def test_decompression_bomb_limit_is_explicit(tmp_path):
    rows=codex();rows[1]['payload']['message']='x'*100000
    p=write(tmp_path/'ratio.zst',rows,True)
    f=scan(p,max_bytes=4096)['files'][0]
    assert 'logical_size_limit' in {e['code'] for e in f['errors']}
    assert not f['complete'] and f['status']=='partial'


def test_directory_file_count_limit_is_not_silent(tmp_path):
    for n in range(3):write(tmp_path/f'{n}.jsonl',codex())
    r=scan(tmp_path,max_files=2)
    assert r['files_scanned']==2 and 'file_count_limit' in r['scan_errors']


def test_missing_session_id_and_float_fingerprint_are_not_invented(tmp_path):
    rows=codex();del rows[0]['payload']['id'];rows[1]['payload']['value']=1.5
    f=scan(write(tmp_path/'missing.jsonl',rows))['files'][0]
    assert f['object_id'] is None
    assert f['timeline'][0]['native_record_id'] is None
    assert f['timeline'][0]['fingerprint_status']=='unsupported_canonical_value'
    assert 'missing_session_id' in {e['code'] for e in f['errors']}


def test_metadata_change_with_identical_bytes_is_unstable(tmp_path,monkeypatch):
    from min_agent import activity_reader
    p=write(tmp_path/'x.jsonl',codex())
    original=activity_reader.hash_readonly
    monkeypatch.setattr(activity_reader,'hash_readonly',lambda path,limit:(original(path,limit)[0],False))
    f=scan(p)['files'][0]
    assert not f['stable'] and f['status']=='partial'


def test_raw_reader_never_requests_unbounded_read(tmp_path,monkeypatch):
    from pathlib import Path
    p=write(tmp_path/'x.zst',codex(),True)
    original=Path.open
    class Guard:
        def __init__(self,f):self.f=f
        def __enter__(self):return self
        def __exit__(self,*args):self.f.close()
        def __getattr__(self,key):return getattr(self.f,key)
        def read(self,n=-1):
            assert 0 <= n <= 65536
            return self.f.read(n)
    def guarded(path,*args,**kwargs):
        assert args[0]=='rb'
        return Guard(original(path,*args,**kwargs))
    monkeypatch.setattr(Path,'open',guarded)
    assert scan(p)['files'][0]['status']=='ok'


def test_empty_input_and_bad_magic_not_normal_history_end(tmp_path):
    p=tmp_path/'empty.jsonl';p.write_bytes(b'')
    assert scan(p)['files'][0]['status']=='partial'
    p=tmp_path/'bad.zst';p.write_bytes(b'PRIVATE_SECRET')
    f=scan(p)['files'][0]
    assert 'unsupported_compression' in {e['code'] for e in f['errors']}
    assert 'PRIVATE_SECRET' not in json.dumps(f)
