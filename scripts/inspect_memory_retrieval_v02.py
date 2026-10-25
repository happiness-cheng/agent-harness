"""只读检查已保存的完整候选池/逐事实证据，不运行模型，不重建检索。"""
import argparse
import gzip
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--case', required=True)
    parser.add_argument('--group', choices=['A','B','C'], default='A')
    parser.add_argument('--pool', choices=['topk','initial','pre-cap','capped','all-legal','global-probe'], default='pre-cap')
    parser.add_argument('--facts', action='store_true')
    args = parser.parse_args()
    data = json.loads(gzip.decompress(args.report.read_bytes()))
    case = next((c for c in data['cases'] if c['case_id'] == args.case), None)
    if case is None:
        parser.error('未找到 case')
    if args.facts:
        result = case['raw_fact_attribution']
    else:
        row = case['groups'][args.group]['retrieval']
        key = {'initial':'initial_candidates','pre-cap':'merged_before_cap','capped':'merged_candidates',
               'all-legal':'eligible_ids','global-probe':'all_pool_lexical_hits'}.get(args.pool)
        result = row['hits'] if args.pool == 'topk' else row['diagnostic'][key]
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
