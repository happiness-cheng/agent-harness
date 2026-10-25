"""冻结公共输入的 A/B/C 检索组件实验；不导入在线模型，不读取凭据。"""
import argparse
import gzip
from hashlib import sha256
import json
from pathlib import Path
import platform
from statistics import mean
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from min_agent.long_memory.retrieval_experiment import compare_case


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def main():
    import yaml
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--book-root', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        parser.error('不覆盖已有结果，请使用新文件名')
    project = Path(__file__).resolve().parents[1]
    base = args.book_root/'chapter3/user-memory-evaluation'
    if not base.is_dir():
        base = args.book_root
    baseline = json.loads((project/'reports/memory_v02/baseline.json').read_text(encoding='utf-8'))
    holdout_path = project/'tests/fixtures/memory_v02_holdout.json'
    holdout = json.loads(holdout_path.read_text(encoding='utf-8'))
    expected_holdout = '5771618d056d6e32b8635d32152079edaa3c7c784eb002964140fcb9dbcd6a5e'
    if digest(holdout_path) != expected_holdout:
        raise ValueError('固定独立 Gold 被修改，不执行实验')
    original_gold_path = base/'fixtures/gold_facts.json'
    if digest(original_gold_path) != baseline['gold_sha256']:
        raise ValueError('书中原始 Gold 改变')
    gold = json.loads(original_gold_path.read_text(encoding='utf-8'))
    cases = {}
    for item in baseline['manifest']:
        path = base/item['path']
        if digest(path) != item['sha256']:
            raise ValueError('公共历史被修改: '+item['path'])
        cases[item['case_id']] = yaml.safe_load(path.read_text(encoding='utf-8'))
    assert len(cases) == 60
    assert not set(holdout['cases']) & set(baseline['diagnostic_ids'])
    # 先验锚点核验，不根据任何检索/模型输出修改 Gold。
    for case_id, entry in holdout['cases'].items():
        case = cases[case_id]
        histories = {h['conversation_id']:h for h in case['conversation_histories']}
        for fact in entry['required_facts']:
            for source in fact['sources']:
                text = histories[source['conversation_id']]['messages'][source['message_index']]['content']
                assert sha256(text.encode()).hexdigest() == source['message_sha256']
                assert text[source['start']:source['end']] == source['quote']
    code = sorted((project/'src/min_agent/long_memory').glob('*.py'))+[Path(__file__).resolve()]
    hashes = {p.relative_to(project).as_posix():digest(p) for p in code}
    v01 = json.loads(gzip.decompress((project/'reports/memory_v01/book-reviewed.json.gz').read_bytes()))
    old_cases = {c['case_id']:c for c in v01['cases']}
    rows = []
    for split, ids in [('diagnostic_dev', baseline['diagnostic_ids']), ('independent_test', list(holdout['cases']))]:
        for case_id in ids:
            facts = (gold[case_id] if split == 'diagnostic_dev' else holdout['cases'][case_id])['required_facts']
            row = compare_case(cases[case_id], gold=facts, budget=2048)
            row['split'] = split
            row['v01_baseline_topk_identical'] = (
                row['groups']['A']['retrieval']['trace']['final_ids'] == old_cases[case_id]['shared_retrieval_trace']['final_ids'])
            if not row['v01_baseline_topk_identical']:
                raise ValueError('A 组与 v01 Top-K 不同，停止发布: '+case_id)
            rows.append(row)
            print(split, case_id, {g:round(r['fact_recall_at_k'],4) for g,r in row['groups'].items()}, flush=True)
    summaries = {}
    measures = ('hit_at_k','fact_recall_at_k','mrr','candidate_fact_coverage','full_pre_cap_fact_coverage',
                'legal_history_fact_coverage','visible_fact_recall','context_bytes','unique_before_topk',
                'unique_before_cap','duplicate_hits_before_topk','retrieval_median_ms',
                'storage_canonical_bytes','storage_card_sidecar_bytes','indexed_text_bytes',
                'cross_scenario_surface_collisions')
    for split in ('diagnostic_dev','independent_test'):
        summaries[split] = {}
        subset = [r for r in rows if r['split'] == split]
        for group in ('A','B','C'):
            summaries[split][group] = {name:mean(r['groups'][group][name] for r in subset) for name in measures}
            summaries[split][group].update(cases=len(subset), facts=sum(r['gold_fact_count'] for r in subset),
                exception_facts_covered=sum(r['groups'][group]['exception_facts_covered'] for r in subset),
                exception_facts_total=sum(r['groups'][group]['exception_facts_total'] for r in subset))
    assert hashes == {p.relative_to(project).as_posix():digest(p) for p in code}
    result = dict(protocol='Adrian v02 retrieval components; one fixed run; Gold only posthoc',
        base_commit=baseline['base_commit'], execution_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=project,text=True).strip(),
        source_hashes=hashes, gold_sha256=digest(original_gold_path), independent_gold_sha256=digest(holdout_path),
        public_manifest=baseline['manifest'], book_commit=baseline['book_commit'], python=platform.python_version(),
        top_k=8, candidate_cap=100, top_g=3, context_budget=2048, context_budget_unit='UTF-8 compact serialized bytes',
        timing_protocol='median of 3 ordinary searches per group/case; full diagnostics excluded; same process, not production SLA',
        cards_protocol='own Episode plus up to 600 characters of immediately preceding message in same source conversation; explicit roles',
        structured_protocol='verbatim proper-name/identifier plus original Name (ALIAS); exact phrase hit score fixed at 1.0',
        dev_metric='original any-variant source text match, no business-header credit',
        test_metric='variant plus frozen source-message evidence association; 23 source facts, not complete 6-task rubrics',
        private_imports=0, live_model_calls=0, model_quality='NOT_RUN', judge='NOT_RUN', provider_tokens='NOT_RUN',
        semantic_wrong_scenario_rate='NOT_RUN', summaries=summaries, cases=rows)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    # 一份压缩 JSON 保留完整原始 Episode/卡片、候选池及路径，避免只留下汇总。
    with args.report.open('xb') as stream:
        stream.write(gzip.compress((json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode(),mtime=0))
    print(json.dumps(summaries,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
