"""20 题冻结 A/B/C/D 与独立原子卡片探索；禁止覆盖旧结果，零外部调用。"""
import argparse
from datetime import timedelta
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
from min_agent.long_memory.atomic_cards import QuoteRef,build_atomic_card
from min_agent.long_memory.evaluation import build_approved_fixture
from min_agent.long_memory.models import Query
from min_agent.long_memory.quality_evaluation import evaluate_quality_case
from min_agent.long_memory.retrieval_quality import WEIGHTS,FAMILIES,STOP,RRF_K,ANCHORS_PER_ROUTE,body

TEST_GOLD_SHA='51b8259238c8b1ca71616d7b5d58f10ce26278b8a8f1321e22d75b8205c4f0f8'


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def load_inputs(base):
    import yaml
    baseline=json.loads((ROOT/'reports/memory_v03/baseline.json').read_text(encoding='utf-8'))
    old_gold_path=base/'fixtures/gold_facts.json'
    if digest(old_gold_path)!=baseline['original_gold_sha256']:
        raise ValueError('原 Gold 已改变')
    previous_path=ROOT/'tests/fixtures/memory_v02_holdout.json'
    previous=json.loads(previous_path.read_text(encoding='utf-8'))
    current_path=ROOT/'tests/fixtures/memory_v03_holdout.json'
    if digest(current_path)!=TEST_GOLD_SHA:
        raise ValueError('固定 v03 Gold 已改变')
    current=json.loads(current_path.read_text(encoding='utf-8'))
    assert not set(current['cases']) & set(baseline['seen_dev_ids'])
    old=json.loads(old_gold_path.read_text(encoding='utf-8'))
    cases={}
    for item in baseline['book_manifest']:
        path=base/item['path']
        if digest(path)!=item['sha256']:
            raise ValueError('原书数据被修改: '+item['path'])
        case=yaml.safe_load(path.read_text(encoding='utf-8'))
        cases[case['test_id']]=case
    assert len(cases)==60
    for fixture in (previous,current):
        for identity,row in fixture['cases'].items():
            histories={h['conversation_id']:h for h in cases[identity]['conversation_histories']}
            for fact in row['required_facts']:
                for source in fact['sources']:
                    text=histories[source['conversation_id']]['messages'][source['message_index']]['content']
                    assert sha256(text.encode()).hexdigest()==source['message_sha256']
                    assert text[source['start']:source['end']]==source['quote']
    splits=[('original_dev',[i for i in old if i!='_comment'],old),
            ('previous_test_now_dev',list(previous['cases']),previous['cases']),
            ('frozen_test',list(current['cases']),current['cases'])]
    return baseline,cases,splits


def _quote(memory,ctx,query,needle,*,index=None,scenario=None):
    doc=memory.document(ctx)
    for m in memory.records(ctx,as_of=query.as_of):
        if index is not None and doc.evidence[m.evidence_refs[0]].message_ref!=index:
            continue
        if scenario is not None and f';session={scenario};' not in m.content:
            continue
        text=body(m)
        if needle in text:
            start=text.index(needle)
            return QuoteRef(episode_id=m.id,start=start,end=start+len(needle),quote=needle)
    raise ValueError('原文找不到指定片段: '+needle)


def atomic_exploration(cases):
    fixture=dict(test_id='v03-atomic-public',category='synthetic',conversation_histories=[dict(
        conversation_id='orchid',timestamp='2026-01-01T00:00:00Z',messages=[
            dict(role='assistant',content='Orchid Travel offers a membership.'),
            dict(role='assistant',content='Which tier would you like?'),
            dict(role='user',content='I choose Bronze.')]),dict(
        conversation_id='harbor',timestamp='2026-02-01T00:00:00Z',messages=[
            dict(role='assistant',content='Harbor also offers Bronze.')])])
    memory,ctx,info=build_approved_fixture(fixture,approval='approved_fixture')
    query=Query(text='membership tier',as_of=info['last_time'])
    subject=_quote(memory,ctx,query,'Orchid Travel')
    attribute=_quote(memory,ctx,query,'tier')
    value=_quote(memory,ctx,query,'Bronze',index=2)
    kwargs=dict(subject=subject,attribute=attribute,value=value,approval='approved_fixture')
    card=build_atomic_card(memory,ctx,query,**kwargs)
    checks=[dict(name='same_event_cross_sentence',expected='accepted_inferred_pending',
        actual=card.claim_kind+'_'+card.confirmation_status,
        passed=card.claim_kind=='inferred' and card.confirmation_status=='pending',card=card.model_dump(mode='json'))]
    variants=[('cross_event',dict(value=_quote(memory,ctx,query,'Bronze',index=0))),
              ('fabricated_quote',dict(value=value.model_copy(update={'quote':'MISSING'}))),
              ('inference_upgrade',dict(claim_kind='explicit')),
              ('unapproved',dict(approval='none'))]
    for name,changes in variants:
        try:
            build_atomic_card(memory,ctx,query,**(kwargs|changes))
            checks.append(dict(name=name,expected='rejected',actual='accepted',passed=False))
        except (ValueError,PermissionError) as error:
            checks.append(dict(name=name,expected='rejected',actual=type(error).__name__,passed=True,message=str(error)))
    book=cases['layer2_03_multiple_credit_cards']
    book_memory,book_ctx,book_info=build_approved_fixture(
        {k:book[k] for k in ('test_id','category','conversation_histories')},approval='approved_fixture')
    at=book_info['last_time']+timedelta(microseconds=1)
    book_query=Query(text='card issuer and product',as_of=at)
    scenario='amex_application_002'
    book_card=build_atomic_card(book_memory,book_ctx,book_query,
        subject=_quote(book_memory,book_ctx,book_query,'American Express',index=1,scenario=scenario),
        attribute=_quote(book_memory,book_ctx,book_query,'card',index=0,scenario=scenario),
        value=_quote(book_memory,book_ctx,book_query,'Gold Card',index=16,scenario=scenario),approval='approved_fixture')
    checks.append(dict(name='book_cross_sentence_issuer_and_selected_product',expected='inferred_pending',
        actual=book_card.claim_kind+'_'+book_card.confirmation_status,
        passed=book_card.claim_kind=='inferred' and book_card.confirmation_status=='pending',
        card=book_card.model_dump(mode='json')))
    return dict(scope='controlled quote recipe construction only; no automatic extraction, retrieval gains or production import claims',
        checks=checks,passed=sum(c['passed'] for c in checks),total=len(checks),
        production_imports=0,used_in_abcd=False,model_quality='NOT_RUN')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--book-root',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    if args.report.exists():
        parser.error('不覆盖既有结果，请指定新路径')
    base=args.book_root/'chapter3/user-memory-evaluation'
    if not base.is_dir():base=args.book_root
    baseline,cases,splits=load_inputs(base)
    prior=json.loads(gzip.decompress((ROOT/'reports/memory_v03/baseline-results.json.gz').read_bytes()))
    frozen_a={c['case_id']:c for c in prior['cases']}
    sources=sorted((ROOT/'src/min_agent').rglob('*.py'))+[Path(__file__).resolve()]
    code_hashes={p.relative_to(ROOT).as_posix():digest(p) for p in sources}
    rows=[]
    for split,ids,gold in splits:
        for identity in ids:
            row=evaluate_quality_case(cases[identity],gold=gold[identity]['required_facts'])
            row['split']=split
            old=frozen_a[identity]
            assert row['input_history_sha256']==old['input_history_sha256']
            assert row['groups']['A']['retrieval']['hits']==old['A']['retrieval']['hits']
            assert row['groups']['A']['visible_ids']==old['A']['selected_ids']
            assert row['groups']['A']['recall_at_8']==old['A']['fact_recall_at_k']
            assert row['groups']['A']['visible_fact_recall']==old['A']['visible_fact_recall']
            row['a_matches_frozen_baseline']=True
            rows.append(row)
            print(split,identity,{g:(r['candidate_facts'],r['topk_facts'],r['visible_facts']) for g,r in row['groups'].items()},flush=True)
    summaries={}
    for split,_,_ in splits:
        selected=[r for r in rows if r['split']==split]
        summaries[split]={}
        for group in ('A','B','C','D'):
            values=[r['groups'][group] for r in selected]
            count=sum(v['fact_count'] for v in values)
            totals={name:sum(v[name] for v in values) for name in ('candidate_facts','topk_facts','visible_facts')}
            summaries[split][group]=dict(cases=len(selected),facts=count,**totals,
                macro_candidate_recall=mean(v['candidate_recall'] for v in values),
                macro_recall_at_8=mean(v['recall_at_8'] for v in values),
                macro_visible_recall=mean(v['visible_fact_recall'] for v in values),
                micro_candidate_recall=totals['candidate_facts']/count,
                micro_recall_at_8=totals['topk_facts']/count,
                micro_visible_recall=totals['visible_facts']/count,
                mean_candidate_count=mean(v['candidate_count'] for v in values),
                mean_pre_cap_count=mean(v['pre_cap_count'] for v in values),
                mean_context_bytes=mean(v['context_bytes'] for v in values),
                mean_search_ms=mean(v['median_search_ms'] for v in values))
    atomic=atomic_exploration(cases)
    assert atomic['passed']==atomic['total']
    assert code_hashes=={p.relative_to(ROOT).as_posix():digest(p) for p in sources}
    result=dict(protocol='Adrian Memory v03 frozen raw Episode 2x2 retrieval ablation',
        base_head=baseline['base_head'],v02_actual_source_manifest='reports/memory_v03/baseline.json',
        book_commit=baseline['book_commit'],book_manifest=baseline['book_manifest'],
        original_gold_sha256=baseline['original_gold_sha256'],test_gold_sha256=TEST_GOLD_SHA,
        baseline_result_sha256=digest(ROOT/'reports/memory_v03/baseline-results.json.gz'),
        python=platform.python_version(),code_hashes=code_hashes,weights=WEIGHTS,families=FAMILIES,
        stop_words=sorted(STOP),rrf_k=RRF_K,anchors_per_route=ANCHORS_PER_ROUTE,
        top_k=8,candidate_cap=100,budget=2048,budget_unit='compact serialized UTF-8 bytes',
        timing='mean across cases of median 3 experimental calls; includes shared full-pool baseline diagnostics; not comparable to production SLA or v02 absolute latency',
        scoring='v02 unchanged literal variant match; evidence-bound source association for v02/v03 heldout; no business-header credit',
        runtime_default_changed=False,live_model_calls=0,private_imports=0,provider_tokens='NOT_RUN',
        model_task_quality='NOT_RUN',judge='NOT_RUN',summaries=summaries,cases=rows,atomic_exploration=atomic)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    with args.report.open('xb') as stream:
        stream.write(gzip.compress((json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode(),mtime=0))
    print(json.dumps(summaries,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
