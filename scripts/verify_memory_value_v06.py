"""本地核对v06调用收据/来源/旧文件，重算数字，不执行外部模型。"""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import py_compile
import re
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from eval_memory_value_v06 import load,sources
from min_agent.long_memory.evaluation import timestamp


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():raise FileExistsError('不覆盖收据')
    out=ROOT/'reports/memory_v06'
    baseline=json.loads((out/'baseline.json').read_text(encoding='utf-8'))
    for path,expected in baseline['hashes'].items():
        if path!='.gitattributes':assert sha256((ROOT/path).read_bytes()).hexdigest()==expected,path
    pack=load(out/'prepared-client-fix.json.gz')
    assert pack['source_hashes']==sources()
    freeze=json.loads((out/'policy-freeze.json').read_text(encoding='utf-8'))
    assert all(sha256((ROOT/p).read_bytes()).hexdigest()==h for p,h in freeze['sha256'].items())
    data=json.loads((out/'live-client-fix.json').read_text(encoding='utf-8'))
    initial=json.loads((out/'live-results.json').read_text(encoding='utf-8'))
    assert initial['model_calls']==0
    events=[json.loads(l) for l in (out/'live-client-fix.jsonl').read_text(encoding='utf-8').splitlines()]
    requests=[e for e in events if e['event']=='request'];responses=[e for e in events if e['event']=='response']
    assert len(requests)==len(responses)==data['model_calls']==90
    assert len({r['number'] for r in requests})==90
    assert len(data['cells'])==75 and data['automatic_retries']==0
    assert data['reserved_usd']<=2 and data['cost_peak_usd']<=2
    assert len({(c['case_id'],c['group']) for c in data['cells']})==75
    cases={c['case_id']:c for c in pack['cases']}
    allowed={'search_long_memory','read_long_memory_topic','read_long_memory_evidence'}
    b2_delivery=[]
    for request in requests:
        case=cases[request['case_id']];pool={m['id']:m for m in case['source_episodes']}
        assert request['input_units_upper']+4096<=32768
        for tool in request['tools']:
            assert (tool.get('name') or tool.get('function',{}).get('name')) in allowed
        tail=json.loads(request['messages'][-1]['content'])
        view=tail.get('untrusted_user_memory',{})
        if request['group']=='B2':b2_delivery.append(dict(case_id=case['case_id'],request_number=request['number'],evidence_entries=len(view.get('evidence',[]))))
        entries=[e for lane in ('stable_core','active_core','retrieved') for e in view.get(lane,[])]+view.get('evidence',[])
        for entry in entries:
            identity=entry.get('source',entry.get('id'));assert identity in pool,identity
            assert timestamp(pool[identity]['observed_at'])<=timestamp(case['as_of'])
            if 'text' in entry:assert entry['text'] in pool[identity]['content']
            for quote in entry.get('quotes',[]):assert quote['text'] in pool[identity]['content']
    assert len(b2_delivery)==30 and all(d['evidence_entries']==0 for d in b2_delivery)
    for response in responses:
        assert response['usage']['output_tokens']<=4096
        assert all(c['name'] in allowed for c in response['tool_calls'])
    for cell in data['cells']:
        assert all(e['name'] in allowed for e in cell.get('tool_events',[]))
    audit=json.loads((out/'source-audit.json').read_text(encoding='utf-8'))
    actual={(c['case_id'],c['group']):c for c in data['cells']}
    for row in audit['rows']:
        cell=actual[(row['case_id'],row['group'])]
        if row['answer_sha256']:assert sha256(cell['raw_answer'].encode()).hexdigest()==row['answer_sha256']
    files=[*ROOT.glob('src/min_agent/long_memory/*evidence*.py'),*ROOT.glob('scripts/*memory_value_v06.py'),*ROOT.glob('tests/unit/*evidence*v06.py')]
    for p in files:py_compile.compile(str(p),doraise=True)
    wheel=next((out/'wheels').glob('*.whl'))
    with zipfile.ZipFile(wheel) as z:
        for name in ('task_evidence','active_evidence','evidence_budget'):
            assert z.read('min_agent/long_memory/'+name+'.py')==(ROOT/'src/min_agent/long_memory'/f'{name}.py').read_bytes()
    # 不读凭据；仅扫描新增公开实验文本是否意外出现常见密钥格式，不打印疑似值。
    scan=[*out.rglob('*.json'),*out.rglob('*.jsonl'),*out.rglob('*.md'),*files]
    assert not any(re.search(r'sk-[A-Za-z0-9]{24,}',p.read_text(encoding='utf-8',errors='replace')) for p in scan)
    b2=[c for c in data['cells'] if c['group']=='B2']
    receipt=dict(base_commit=baseline['commit'],measured_commit=data['source_commit'],
        old_tracked_files_byte_identical=len(baseline['hashes'])-1,allowed_old_metadata_change='.gitattributes only',
        frozen_policy_sources_verified=len(freeze['sha256']),prepared_source_hashes_verified=len(pack['source_hashes']),
        original_authorized_pack_sha256=sha256((out/'prepared.json.gz').read_bytes()).hexdigest(),
        repaired_execution_pack_sha256=sha256((out/'prepared-client-fix.json.gz').read_bytes()).hexdigest(),
        local_initialization_requests=0,actual_requests=len(requests),authorized_request_ceiling=105,
        comparison_cells=75,schema_valid_final_answers=sum(c['validation']['schema_valid'] for c in data['cells']),
        format_invalid_final_answers=sum(c['raw_answer'] is not None and not c['validation']['json_valid'] for c in data['cells']),
        schema_invalid_json_answers=sum(c['validation']['json_valid'] and not c['validation']['schema_valid'] for c in data['cells']),
        b2_final_answers=sum(c['raw_answer'] is not None for c in b2),b2_stop_codes=dict(Counter(c['error_code'] for c in b2)),
        b2_provider_calls=sum(c['provider_calls'] for c in b2),b2_tool_calls=sum(c['tool_calls'] for c in b2),
        b2_actual_sent_body_entries=sum(r['evidence_entries'] for r in b2_delivery),b2_delivery=b2_delivery,
        input_tokens=sum(c['input_tokens'] for c in data['cells']),output_tokens=sum(c['output_tokens'] for c in data['cells']),
        reserved_peak_usd=data['reserved_usd'],reported_usage_peak_estimate_usd=data['cost_peak_usd'],invoice='NOT_AVAILABLE',
        automatic_api_retries=0,external_judge_calls=0,private_imports=0,production_default_changes=0,
        policy_changed_after_holdout=False,syntax_compiled_files=len(files),wheel_sha256=sha256(wheel.read_bytes()).hexdigest(),
        type_check='NOT_RUN_NOT_CONFIGURED',lint='NOT_RUN_NOT_CONFIGURED',
        source_audit_integrity='answer hashes verified; not independent semantic correctness proof',
        provider_state_note='native hidden state omitted from logs; request hash is not a replayable full wire payload')
    with args.output.open('x',encoding='utf-8',newline='\n') as f:json.dump(receipt,f,ensure_ascii=False,indent=2)
    print(json.dumps({k:v for k,v in receipt.items() if k!='b2_delivery'},ensure_ascii=False))


if __name__=='__main__':main()
