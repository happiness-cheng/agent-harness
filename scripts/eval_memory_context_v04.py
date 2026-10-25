"""冻结23题：同候选/两排序/三装配；Gold和等价来源仅用于后验评分。"""
import argparse
import gzip
from hashlib import sha256
import json
from pathlib import Path
import platform
from statistics import mean
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from eval_memory_quality_v03 import load_inputs
from min_agent.long_memory.context_evaluation import evaluate_context_case

HOLDOUT_SHA='bed32caaff82391bf57c9e4c818e0dd6d2e5e62cd1ac6c30f1b41b54e453a974'
REVIEW_SHA='2313b7dc424b01e1cfb3ec52cfd21a5b3522a19ddc8d392d83fd6fffd364c834'


def digest(path):return sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--book-root',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    if args.report.exists():parser.error('不覆盖原实验输出，请换新路径')
    base=args.book_root/'chapter3/user-memory-evaluation'
    if not base.is_dir():base=args.book_root
    inherited,cases,old_splits=load_inputs(base)
    holdout_path=ROOT/'tests/fixtures/memory_v04_holdout.json'
    review_path=ROOT/'tests/fixtures/memory_v04_evidence_review.json'
    assert digest(holdout_path)==HOLDOUT_SHA and digest(review_path)==REVIEW_SHA
    holdout=json.loads(holdout_path.read_text(encoding='utf-8'))
    review=json.loads(review_path.read_text(encoding='utf-8'))
    old_ids={i for _,ids,_ in old_splits for i in ids}
    assert not old_ids & set(holdout['cases'])
    def verify(identity,source):
        histories={h['conversation_id']:h for h in cases[identity]['conversation_histories']}
        history=histories[source['conversation_id']];text=history['messages'][source['message_index']]['content']
        assert sha256(text.encode()).hexdigest()==source['message_sha256']
        assert text[source['start']:source['end']]==source['quote']
        assert str(history['timestamp'])==source['timestamp']
    for identity,row in holdout['cases'].items():
        for f in row['required_facts']:
            for s in f['sources']:verify(identity,s)
    for fact in review['facts']:
        for alternative in fact['alternatives']:
            assert alternative['decision']=='approved_source_equivalence'
            verify(fact['case_id'],alternative['anchor'])
            for field in alternative['required']:verify(fact['case_id'],field)
    previous=json.loads(gzip.decompress((ROOT/'reports/memory_v03/abcd-fixed.json.gz').read_bytes()))
    previous={c['case_id']:c for c in previous['cases']}
    files=sorted((ROOT/'src/min_agent').rglob('*.py'))+[Path(__file__).resolve()]
    hashes={p.relative_to(ROOT).as_posix():digest(p) for p in files}
    source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    splits=[('seen_'+name,ids,gold) for name,ids,gold in old_splits]+[
        ('frozen_test',list(holdout['cases']),holdout['cases'])]
    rows=[]
    for split,ids,gold in splits:
        for identity in ids:
            audited=[r for r in review['facts'] if r['case_id']==identity]
            row=evaluate_context_case(cases[identity],gold=gold[identity]['required_facts'],reviews=audited)
            row['split']=split
            if identity in previous:
                old=previous[identity]['groups']['B'];current=row['configurations']['R0_P0']
                assert row['candidate_ids']==[h['memory_id'] for h in old['retrieval']['candidates']]
                assert current['top_ids']==[h['memory_id'] for h in old['retrieval']['hits']]
                assert current['assembly']['selected_ids']==old['visible_ids']
                assert current['visible_facts']==old['visible_facts']
                row['v03_r0_p0_identical']=True
            rows.append(row)
            print(split,identity,{k:(v['topk_facts'],v['visible_facts']) for k,v in row['configurations'].items()},flush=True)
    summaries={}
    for split,_,_ in splits:
        selected=[r for r in rows if r['split']==split];summaries[split]={}
        for name in selected[0]['configurations']:
            values=[r['configurations'][name] for r in selected]
            facts=sum(v['fact_count'] for v in values)
            totals={key:sum(v[key] for v in values) for key in ('candidate_facts','topk_facts','visible_facts')}
            audited=sum(v['review']['audited_facts'] for v in values)
            reviewed_values=sum(v['review']['reviewed_value_facts'] for v in values)
            complete=sum(v['review']['complete_evidence_facts'] for v in values)
            text_complete=sum(v['review']['complete_text_facts'] for v in values)
            predictions=sum(v['binding_audit']['predictions'] for v in values)
            checked=sum(v['binding_audit']['reviewed'] for v in values)
            wrong=sum(v['binding_audit']['wrong'] for v in values)
            summaries[split][name]=dict(cases=len(selected),facts=facts,**totals,
                candidate_recall=totals['candidate_facts']/facts,topk_recall=totals['topk_facts']/facts,
                visible_coverage=totals['visible_facts']/facts,
                macro_visible_coverage=mean(v['visible_facts']/v['fact_count'] for v in values),
                context_bytes=mean(v['assembly']['bytes'] for v in values),
                assembly_ms=mean(v['assembly_ms'] for v in values),
                retrieval_ms=mean(r['retrieval_ms'] for r in selected),binding_ms=mean(r['binding_ms'] for r in selected),
                audited_facts=audited,reviewed_value_facts=reviewed_values,complete_text_facts=text_complete,
                complete_evidence_facts=complete,full_evidence_retention=complete/reviewed_values if reviewed_values else None,
                text_evidence_retention=text_complete/reviewed_values if reviewed_values else None,
                binding_predictions=predictions,binding_checked=checked,binding_wrong=wrong,
                binding_error_rate=wrong/checked if checked else None,binding_audit_coverage=checked/predictions if predictions else None,
                mechanical_complete=all(v['assembly']['mechanical_complete'] for v in values))
    assert hashes=={p.relative_to(ROOT).as_posix():digest(p) for p in files}
    result=dict(protocol='v04 fixed raw candidate pool, stable binding partition x evidence packing',
        source_commit=source_commit,baseline_commit='5a962bd46835a21048f4568e84372f849c1cefcf',code_hashes=hashes,
        old_manifest=inherited['book_manifest'],original_gold_sha256=inherited['original_gold_sha256'],
        holdout_sha256=HOLDOUT_SHA,review_sha256=REVIEW_SHA,python=platform.python_version(),
        cases=rows,summaries=summaries,budget=2048,top_k=8,candidate_cap=100,
        timing='one fixed local run; retrieval includes inherited diagnostics, assembly includes source revalidation; no SLA claim',
        oracle='exact mask/count minimum-byte DP for frozen literal source facts, not semantic task success; posthoc only',
        reviewed_equivalence='separate small source-audited subset; all required text and visible provenance for complete metric',
        model_quality='NOT_RUN',judge='NOT_RUN',provider_tokens='NOT_RUN',private_imports=0,live_model_calls=0,
        runtime_default_changed=False)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    with args.report.open('xb') as f:f.write(gzip.compress((json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode(),mtime=0))
    print(json.dumps(summaries,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
