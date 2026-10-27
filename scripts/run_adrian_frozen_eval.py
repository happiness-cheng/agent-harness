"""用冻结协议与可信源码快照重放统一评估；默认离线。"""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))


def main():
    from min_agent.evaluation.contracts import TaskSpec
    from min_agent.evaluation.runner import run_suite
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,default=ROOT);p.add_argument('--protocol',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--live',action='store_true');p.add_argument('--credentials',type=Path);p.add_argument('--proxy');p.add_argument('--cost-limit',type=float)
    p.add_argument('--request-ceiling',type=int,default=16)
    a=p.parse_args()
    tasks=[TaskSpec.model_validate(v) for v in json.loads(a.protocol.read_text(encoding='utf-8'))]
    _,summary=run_suite(tasks,a.out,source_root=a.source,live=a.live,credentials=a.credentials,proxy=a.proxy,
        cost_limit=a.cost_limit,request_ceiling=a.request_ceiling)
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    raise SystemExit(0 if summary['expected_outcomes_match'] else 1)


if __name__=='__main__':main()
