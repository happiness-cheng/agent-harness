"""只读查看v0.4已保存的上下文、绑定、Oracle与事实去向。"""
import argparse
import gzip
import json
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--case',required=True)
    parser.add_argument('--configuration',default='R0_P0')
    parser.add_argument('--view',choices=['context','facts','oracle','bindings'],default='context')
    args=parser.parse_args();data=json.loads(gzip.decompress(args.report.read_bytes()))
    case=next((c for c in data['cases'] if c['case_id']==args.case),None)
    if case is None:parser.error('case不存在')
    if args.configuration not in case['configurations']:parser.error('configuration不存在')
    row=case['configurations'][args.configuration]
    if args.view=='oracle':result=case['oracle']
    elif args.view=='bindings':result=case['binding_features']
    elif args.view=='facts':result=dict(primary=row['matches'],review=row['review'],binding_audit=row['binding_audit'])
    else:
        entries=row['assembly']['entries']
        size=len(json.dumps(entries,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()) if entries else 0
        assert size==row['assembly']['bytes']
        result=dict(compact_bytes=size,budget=row['assembly']['budget'],entries=entries)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
