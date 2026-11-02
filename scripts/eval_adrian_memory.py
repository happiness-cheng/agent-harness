"""同池同预算比较Memory策略；零外部调用，模型质量明确NOT_RUN。"""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from min_agent.long_memory.evaluation import evaluate_case, inspect_private_corpus
from min_agent.long_memory.selection import SelectionPolicy


def run(book_root,*,limit,budget,approval,policy=None):
    import yaml
    root=Path(book_root).resolve()
    if (root/'chapter3/user-memory-evaluation').is_dir(): base=root/'chapter3/user-memory-evaluation'
    elif (root/'test_cases').is_dir(): base=root
    else: raise ValueError('找不到test_cases目录')
    paths=sorted((base/'test_cases').rglob('*.yaml'))
    if len(paths)!=60: raise ValueError('固定公共集应有60 YAML；不静默丢弃案例')
    gold=json.loads((base/'fixtures/gold_facts.json').read_text(encoding='utf-8'))
    manifest=[];cases=[];ids=set()
    for path in paths:
        case=yaml.safe_load(path.read_text(encoding='utf-8'))
        if case['test_id'] in ids: raise ValueError('重复case ID')
        ids.add(case['test_id'])
        manifest.append(dict(path=path.relative_to(base).as_posix(),sha256=sha256(path.read_bytes()).hexdigest(),case_id=case['test_id']))
        cases.append(case)
    if Counter(c['category'] for c in cases)!=dict(layer1=20,layer2=20,layer3=20): raise ValueError('层级数不符')
    project=Path(__file__).resolve().parents[1]
    code_files=sorted((project/'src/min_agent/long_memory').glob('*.py'))+[Path(__file__).resolve()]
    code_files += [project/'src/min_agent'/name for name in ('sessions.py','storage.py','application.py','runtime.py','tools/registry.py','web/app.py')]
    code_hashes={p.relative_to(project).as_posix():sha256(p.read_bytes()).hexdigest() for p in code_files}
    result=[];started=perf_counter()
    for i,case in enumerate(cases[:limit],1):
        result.append(evaluate_case(case,gold=gold.get(case['test_id']),budget=budget,approval=approval,policy=policy))
        print(f'processed {i}/{min(limit,len(cases))}: {case["test_id"]}',flush=True)
    summaries={}
    for layer in ('layer1','layer2','layer3','all'):
        rows=[r for r in result if layer=='all' or r['category']==layer]
        summaries[layer]={}
        for strategy in ('NONE','RECENCY','FREQUENCY','GOAL_VALUE'):
            values=[r['strategies'][strategy] for r in rows]
            labeled=[r for r in values if isinstance(r['core_fact_coverage'],(int,float))]
            summaries[layer][strategy]=dict(cases=len(values),gold_cases=len(labeled),
                mean_core_fact_coverage=sum(r['core_fact_coverage'] for r in labeled)/len(labeled) if labeled else 'NOT_RUN',
                mean_fact_recall_at_k=sum(r['fact_recall_at_k'] for r in labeled)/len(labeled) if labeled else 'NOT_RUN',
                mean_first_relevant_mrr=sum(r['first_relevant_mrr'] for r in labeled)/len(labeled) if labeled else 'NOT_RUN',
                mean_selected_bytes_upper_bound=sum(r['selected_bytes_upper_bound'] for r in values)/len(values) if values else 0,
                task_quality='NOT_RUN',success_rate='NOT_RUN',proactivity='NOT_RUN',hallucination='NOT_RUN')
    paired={}
    for baseline in ('RECENCY','FREQUENCY'):
        differences=[]
        for row in result:
            a=row['strategies']['GOAL_VALUE']['core_fact_coverage'];b=row['strategies'][baseline]['core_fact_coverage']
            if isinstance(a,(int,float)) and isinstance(b,(int,float)):
                differences.append(dict(case_id=row['case_id'],difference=a-b))
        paired[baseline]=dict(cases=len(differences),win=sum(r['difference']>0 for r in differences),
            tie=sum(r['difference']==0 for r in differences),loss=sum(r['difference']<0 for r in differences),
            mean_difference=sum(r['difference'] for r in differences)/len(differences) if differences else 'NOT_RUN',
            per_case=differences,scope='source fact coverage proxy, not task quality or measured counterfactual value')
    git=subprocess.run(['git','rev-parse','HEAD'],capture_output=True,text=True,check=True).stdout.strip()
    tracked=subprocess.run(['git','status','--porcelain','--untracked-files=no'],capture_output=True,text=True,check=True).stdout
    if code_hashes!={p.relative_to(project).as_posix():sha256(p.read_bytes()).hexdigest() for p in code_files}:
        raise RuntimeError('评测期间源码变化，不发布混合版本的报告')
    reference=subprocess.run(['git','-C',str(base),'rev-parse','HEAD'],capture_output=True,text=True)
    return dict(protocol='Adrian Memory v0.1 offline source evidence',code_base_commit=git,
        working_tree_dirty=bool(tracked),code_hashes=code_hashes,
        book_source_commit=reference.stdout.strip() if reference.returncode==0 else 'UNVERSIONED_LOCAL_DATA',
        public_manifest=manifest,gold_sha256=sha256((base/'fixtures/gold_facts.json').read_bytes()).hexdigest(),
        approval=approval,policy=(policy or SelectionPolicy()).model_dump(),core_budget=budget,
        budget_unit='UTF-8 serialized bytes as conservative token upper bound; not provider usage',
        live_model_requests=0,provider_usage='NOT_RUN',judge='NOT_RUN',cost='NOT_RUN',
        total_local_seconds=round(perf_counter()-started,3),summaries=summaries,paired=paired,cases=result)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--book-root',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--approval',choices=['approved_fixture'],required=True)
    parser.add_argument('--limit',type=int,default=60)
    parser.add_argument('--core-budget',type=int,default=2048)
    parser.add_argument('--policy',type=Path)
    parser.add_argument('--private-root',type=Path)
    parser.add_argument('--private-review-output',type=Path)
    args=parser.parse_args()
    if not 1<=args.limit<=60 or args.core_budget<0: parser.error('limit需1—60且预算非负')
    if args.report.exists(): parser.error('不覆盖已有报告；请选择新路径')
    policy=SelectionPolicy.model_validate_json(args.policy.read_text(encoding='utf-8')) if args.policy else SelectionPolicy()
    result=run(args.book_root,limit=args.limit,budget=args.core_budget,approval=args.approval,policy=policy)
    if args.private_root:
        result['private_corpus']=inspect_private_corpus(args.private_root,review_output=args.private_review_output)
    elif args.private_review_output: parser.error('评审输出需要private-root')
    args.report.parent.mkdir(parents=True,exist_ok=True)
    with args.report.open('x',encoding='utf-8',newline='\n') as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps(dict(summaries=result['summaries'],paired=result['paired'],live_model_requests=0),ensure_ascii=False,indent=2))


if __name__=='__main__': main()
