"""只读查看冻结 v0.3 结果中的候选、排序特征、原文上下文与逐事实去向。"""
import argparse
import gzip
import json
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--case',required=True)
    parser.add_argument('--group',choices=['A','B','C','D'],default='D')
    parser.add_argument('--view',choices=['facts','candidates','ranking','context'],default='facts')
    args=parser.parse_args()
    data=json.loads(gzip.decompress(args.report.read_bytes()))
    case=next((c for c in data['cases'] if c['case_id']==args.case),None)
    if case is None:parser.error('case 不存在')
    row=case['groups'][args.group]
    pool={m['id']:m for m in case['source_episodes']}
    if args.view=='facts':result=case['fact_analysis']
    elif args.view=='candidates':result=row['retrieval']['candidates']
    elif args.view=='ranking':
        result=[dict(rank=n+1,**h,features=row['retrieval']['ranking_features'].get(h['memory_id']),
                     episode=pool[h['memory_id']]) for n,h in enumerate(row['retrieval']['ranked'])]
    else:
        entries=[dict(memory_id=i,content=pool[i]['content'],evidence_type=pool[i]['evidence_type'],
                      evidence_refs=pool[i]['evidence_refs']) for i in row['visible_ids']]
        size=len(json.dumps(entries,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()) if entries else 0
        assert size==row['context_bytes']
        result=dict(compact_bytes=size,budget=row['context_budget'],entries=entries)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
