"""仅离线：回放v06公开工具请求，记录未发送第三轮的可证实下界。"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'src'))
from eval_memory_value_v06 import load,fixture
from min_agent.budget import BudgetLedger,BudgetLimits
from min_agent.context import ContextLimit
from min_agent.models import ModelResponse,ToolCall
from experiments.memory_v07.budget import request_breakdown
from min_agent.long_memory.active_evidence import active_registry,ProjectedRuntime
from min_agent.long_memory.answer_eval import encoded


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():raise FileExistsError('不覆盖诊断')
    base=ROOT/'reports/memory_v06'
    pack=load(base/'prepared-client-fix.json.gz')
    old=json.loads((base/'live-client-fix.json').read_text(encoding='utf-8'))
    events=[json.loads(l) for l in (base/'live-client-fix.jsonl').read_text(encoding='utf-8').splitlines()]
    logged={(r['case_id'],r['group'],r['number']):r for r in events if r['event']=='request'}
    originals={r['case_id']:r for r in old['cells'] if r['group']=='B2'};rows=[]
    for case in pack['cases']:
        previous=originals[case['case_id']];memory,ctx,q=fixture(case)
        registry,state=active_registry(memory,ctx,q);measurements=[]
        class Replay:
            def generate(self,messages,*,tools=None):
                current=len(measurements)
                metrics=request_breakdown(messages,tools or [])
                if current<2:
                    old_request=previous['provider_requests'][current]
                    sent=logged[(case['case_id'],'B2',old_request['number'])]
                    # 安全日志没保存原生状态；仅验证公开Message部分确实可精确重放。
                    safe=[m.model_dump(mode='json',exclude={'provider_state'}) for m in messages]
                    assert safe==sent['messages'],case['case_id']
                    exact=sent['input_units_upper']+4096
                    metrics.update(legacy_exact_request_upper_units=exact,
                        omitted_native_state_delta_bytes=exact-metrics['legacy_request_upper_units'],
                        actual_api_tokens=old_request['usage'],
                        historical_internal_message_bytes_exact=exact-metrics['internal_tool_schema_bytes']-8192,
                        measurement_basis='safe-log replay; memory/system/tool history exact; wire/native detail redacted; combined legacy count exact')
                    delta=metrics['omitted_native_state_delta_bytes']
                    metrics['original_native_state_json_bytes']=delta+4 if current==1 else 0
                    measurements.append(metrics)
                    return ModelResponse(finish_reason=old_request['finish_reason'],content=old_request['raw_answer'],
                        tool_calls=[ToolCall.model_validate(c) for c in old_request['tool_calls']])
                metrics['observation']='counterfactual third request lower bound; original native state absent'
                metrics['original_native_state_json_bytes']='NOT_RECORDED'
                metrics['measurement_basis']='counterfactual lower bound only; not an original third API request'
                measurements.append(metrics)
                raise ContextLimit('OFFLINE_DIAGNOSTIC_ONLY_NO_MODEL_REQUEST')
        limits=BudgetLimits(model_calls=3,tool_calls=4);budget=BudgetLedger(limits).start_segment(limits)
        runtime=ProjectedRuntime(Replay(),registry,budget=budget,query=q,view_builder=registry.memory_view)
        runtime.run_session(q.text,store=memory.sessions,context=ctx)
        assert len(measurements)==3
        spent=sum(r['legacy_exact_request_upper_units'] for r in measurements[:2])
        assert spent==previous['reserved_token_upper_units']
        # 第二轮计数暴露第一份原生状态的增量；第三轮至少继续携带这份状态。
        state1=max(0,measurements[1]['omitted_native_state_delta_bytes'])
        lower=measurements[2]['legacy_request_upper_units']+state1
        rows.append(dict(case_id=case['case_id'],requests=measurements,
            historical_first_two_reserved_exact=spent,third_legacy_upper_count_lower_bound=lower,
            cumulative_lower_bound=spent+lower,cumulative_limit=65536,
            cumulative_block_proved=spent+lower>65536,single_limit=32768,
            single_block_proved=lower>32768,single_block_excluded=False,
            limitation='third native state was not logged; cannot prove an exact historical third-request size or exclude a simultaneous single gate',
            provider_calls=0,source_run_sha256=sha256((base/'live-client-fix.json').read_bytes()).hexdigest()))
    result=dict(rows=rows,provider_calls=0,cumulative_block_proved=sum(r['cumulative_block_proved'] for r in rows),
        original_cases=len(rows),not_a_new_model_quality_result=True)
    with args.output.open('x',encoding='utf-8',newline='\n') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(dict(cases=len(rows),cumulative_block_proved=result['cumulative_block_proved'],provider_calls=0)))


if __name__=='__main__':main()
