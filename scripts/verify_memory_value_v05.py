"""离线核验冻结数据、真实调用日志、来源时点及构建产物；不读凭据。"""
import argparse
from datetime import datetime
import gzip
from hashlib import sha256
import json
from pathlib import Path
import py_compile
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from eval_memory_quality_v03 import load_inputs
from min_agent.long_memory.answer_scoring import validate_review


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--book-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():parser.error('不覆盖已有验证证据')
    out=ROOT/'reports/memory_v05'
    digest=lambda p:sha256(p.read_bytes()).hexdigest()
    baseline=json.loads((out/'baseline.json').read_text(encoding='utf-8'))
    assert all(digest(ROOT/p)==expected for p,expected in baseline['old_hashes'].items())
    pack=json.loads(gzip.decompress((out/'prepared-pilot.json.gz').read_bytes()))
    pack_hash=digest(out/'prepared-pilot.json.gz')
    assert pack_hash=='817067eb067672d5a3b929762029cb1c588f3289595b7ec9d63c76252422b954'
    assert all(digest(ROOT/p)==expected for p,expected in pack['source_hashes'].items())
    _,cases,_=load_inputs(args.book_root/'chapter3/user-memory-evaluation')
    assert len(cases)==60
    result=json.loads((out/'live-results.json').read_text(encoding='utf-8'))
    journal=[json.loads(l) for l in (out/'live-results.jsonl').read_text(encoding='utf-8').splitlines()]
    reserved=[r for r in journal if r['event']=='request_reserved']
    responses=[r for r in journal if r['event']=='response']
    assert len(reserved)==len(responses)==result['model_calls']==27
    assert result['pack_sha256']==pack_hash and result['status']=='COMPLETE'
    assert result['automatic_retries']==0 and result['reserved_upper_usd']<=1
    assert result['total_peak_usage_cost_usd']<=1
    assert len({(r['case_id'],r['group']) for r in responses})==27
    actual={(r['case_id'],r['group']):r for r in result['responses']}
    source_cases={c['case_id']:c for c in pack['cases']}
    for case in pack['cases']:
        for group in 'ABC':
            response=actual[(case['case_id'],group)]
            assert sha256(response['raw_answer'].encode()).hexdigest()==response['answer_sha256']
            assert case['groups'][group]['tools']==[]
            assert case['groups'][group]['max_output_tokens']==4096
        for source in case['source_episodes']:
            # Z与+00:00必须解析后比较；字符串字典序不等于时间序。
            assert datetime.fromisoformat(source['observed_at'])<=datetime.fromisoformat(case['as_of'])
    for row in responses:
        assert row['answer_sha256']==actual[(row['case_id'],row['group'])]['answer_sha256']
    reviews=json.loads((out/'final-audit/source-reviews.json').read_text(encoding='utf-8'))
    assert len(reviews)==26
    for review in reviews:
        assert review['original_book_scores_applicable']==review['case_id'].startswith('layer')
        validate_review(source_cases[review['case_id']],review['group'],actual[(review['case_id'],review['group'])],review)
    paths=[*ROOT.glob('src/min_agent/long_memory/answer_*.py'),*ROOT.glob('scripts/*memory_value_v05.py'),
           *ROOT.glob('tests/unit/test_memory_answer_*v05.py')]
    for path in paths:py_compile.compile(str(path),doraise=True)
    wheels=list((out/'wheels').glob('*.whl'));assert len(wheels)==1
    with zipfile.ZipFile(wheels[0]) as wheel:
        assert all('min_agent/long_memory/'+name+'.py' in wheel.namelist()
                   for name in ['answer_eval','answer_run','answer_scoring'])
    pip=subprocess.run([sys.executable,'-m','pip','check'],text=True,capture_output=True)
    assert pip.returncode==0
    check=dict(old_files_verified=len(baseline['old_hashes']),book_cases_hash_verified=len(cases),
        original_gold_sha256=pack['original_gold_sha256'],pack_sha256=pack_hash,
        prepared_source_hashes_verified=len(pack['source_hashes']),measured_source_commit=result['source_commit'],
        current_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        compiled_python_files=len(paths),wheel_sha256=digest(wheels[0]),wheel_modules_verified=True,
        pip_check=pip.stdout.strip(),calls=27,unique_cells=27,valid_responses=26,format_failures=1,
        automatic_retries=0,private_imports=0,tool_executions=0,external_judge_calls=0,
        reserved_upper_usd=result['reserved_upper_usd'],peak_usage_estimate_usd=result['total_peak_usage_cost_usd'],
        invoice='NOT_AVAILABLE',type_check='NOT_RUN_NOT_CONFIGURED',lint='NOT_RUN_NOT_CONFIGURED',
        production_default_changes=0)
    with args.output.open('x',encoding='utf-8',newline='\n') as stream:json.dump(check,stream,ensure_ascii=False,indent=2)
    print(json.dumps(check,ensure_ascii=False))


if __name__=='__main__':main()
