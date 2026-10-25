"""离线实际Runtime闭环对照；回答和usage均来自Mock，不是模型质量成绩。"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src'), str(ROOT / 'tests/unit')]

from min_agent.long_memory.active_evidence import run_active
from min_agent.long_memory.evidence_budget import CallLedger
from experiments.memory_v07.runtime_loop import run_active_v07
from test_active_loop_v07 import NativeMock
from test_task_evidence_v06 import fixture


def main():
    output = Path(sys.argv[1])
    memory, context, query = fixture()
    old = run_active(memory, context, query, model=NativeMock())
    memory, context, query = fixture()
    new = run_active_v07(memory, context, query, provider=NativeMock(),
                         ledger=CallLedger(max_requests=3, max_cost_usd=1))
    assert old['error_code'] == 'context_limit'
    assert new['runtime_status'] == 'completed' and new['provider_calls'] == 3
    result = dict(backend='MOCK', actual_external_requests=0,
                  warning='Scripted answer and controlled usage; no model quality or bill measured.',
                  old=old, repaired=new)
    with output.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(dict(old=old['runtime_status'], new=new['runtime_status'],
                          new_provider_calls=new['provider_calls'], external_requests=0)))


if __name__ == '__main__':
    main()
