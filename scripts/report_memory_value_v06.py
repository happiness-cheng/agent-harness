"""只读汇总v06离线证据与可选真实回答。语义支持率必须来源审查，不自动填分。"""
import argparse
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from eval_memory_value_v06 import load,fixture,surface,GROUPS
from min_agent.long_memory.answer_eval import encoded
from min_agent.long_memory.task_evidence import select_task_evidence
from min_agent.long_memory.retrieval_quality import body


def text_of(view):
    result=[]
    for lane in ('stable_core','active_core','retrieved'):
        result.extend(e['content'] for e in view.get(lane,[]))
    for e in view.get('evidence',[]):
        if 'text' in e:result.append(e['text'])
        result.extend(q['text'] for q in e.get('quotes',[]))
    return '\n'.join(result)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pack',type=Path,required=True)
    parser.add_argument('--offline',type=Path,required=True)
    parser.add_argument('--results',type=Path)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    if args.output_dir.exists():raise FileExistsError('不覆盖原始报告')
    args.output_dir.mkdir(parents=True)
    pack=load(args.pack);offline=json.loads(args.offline.read_text(encoding='utf-8'))
    real=json.loads(args.results.read_text(encoding='utf-8')) if args.results else None
    if real and real['pack_sha256']!=sha256(args.pack.read_bytes()).hexdigest():raise ValueError('不对应此冻结包')
    cells={(r['case_id'],r['group']):r for r in (real or {}).get('cells',[])}
    controls={r['case_id']:r for r in offline['b2_scripted_controls']}
    totals=defaultdict(lambda:dict(hit=0,total=0,reference_bytes=0,cases=0))
    diagnostics=[];timings=[];reviews=[]
    lines=['# v06逐题五组完整材料与回答','',
        '新回答只有在有本轮授权且确实运行后才出现。NOT_RUN不计对也不计错。离线词面代理不等于必要事实被正确使用；B2脚本接线不属于真实模型成绩。','',
        '## 共用完整System提示词','', '```text',pack['cases'][0]['requests']['A'][0]['content'],'```','',
        '```diff',' A: 无长期材料','+B0: 原生产Core+FTS动态视图','+B1: 任务感知候选/局部绑定/逐字证据预算装配',
        '+B2: 真实Runtime工具循环，只有已披露ID可读取；最多3轮/4工具，成本单列','+C: 仅合法原句Oracle，不影响A/B0/B1/B2','```','']
    for case in pack['cases']:
        identity=case['case_id'];facts=case['gold']['required_facts']
        memory,ctx,q=fixture(case)
        start=perf_counter();b0=memory.assemble_context(ctx,q)['view'];t0=(perf_counter()-start)*1000
        start=perf_counter();b1=select_task_evidence(memory,ctx,q);t1=(perf_counter()-start)*1000
        assert encoded(b0)==encoded(case['views']['B0'])
        assert encoded(b1['view'])==encoded(case['views']['B1'])
        timings.append(dict(case_id=identity,b0_selection_ms=t0,b1_selection_ms=t1,b1_candidate_count=b1['trace']['candidate_count']))
        source={m.id:body(m) for m in memory.records(ctx,as_of=q.as_of,scenario_ids=[])}
        candidate_text='\n'.join(source[i] for i in b1['trace']['candidate_ids'] if i in source)
        top_text='\n'.join(source[i] for i in b1['trace']['ranked_ids'] if i in source)
        selected_text=text_of(b1['view'])
        for index,fact in enumerate(facts):
            c=surface(candidate_text,fact);t=surface(top_text,fact);v=surface(selected_text,fact)
            diagnostics.append(dict(case_id=identity,fact_index=index,fact=fact,candidate=c,top8=t,visible=v,
                label='VISIBLE_SURFACE_ONLY' if v else 'PACKING_OR_EXCERPT' if t else 'RANKING' if c else 'RETRIEVAL_OR_LEXICAL_VARIANT',
                caveat='literal proxy; entity binding, equivalent expressions and unsupported rules require source audit'))
        lines+=['## '+identity,'','分组：'+case['split']+'；任务时点：'+case['as_of'],'','问题：'+case['question'],'']
        for group in GROUPS:
            view=case['views'].get(group,{})
            if group=='B2':view=(cells.get((identity,group)) or controls[identity]).get('final_reference',{})
            body_text=text_of(view);matches=[surface(body_text,f) for f in facts]
            key=case['split']+' / '+group;total=totals[key]
            if group!='B2':
                total['hit']+=sum(matches);total['total']+=len(matches)
                total['reference_bytes']+=len(encoded(view)) if view else 0;total['cases']+=1
            actual=cells.get((identity,group))
            lines+=['### '+group,'','实际模型回答：','```text',
                actual.get('raw_answer') or 'NOT_RUN_OR_NO_FINAL_ANSWER' if actual else 'NOT_RUN：没有本轮真实模型输出。','```','']
            if actual:
                lines+=['确定性结构/ID检查：`'+json.dumps(actual['validation'],ensure_ascii=False)+'`','',
                    'Token/次数/成本：`'+json.dumps({k:actual.get(k) for k in ['provider_calls','tool_calls','input_tokens','output_tokens','latency_ms','cost_peak_usd']},ensure_ascii=False)+'`','']
                if group=='B2':
                    for step in actual.get('provider_requests',[]):
                        lines+=['模型调用'+str(step['number'])+'原始公开输出/工具请求：','```json',json.dumps({k:step.get(k) for k in ['raw_answer','finish_reason','tool_calls']},ensure_ascii=False,indent=2),'```','']
            if group=='B2' and not actual:
                control=controls[identity]
                lines+=['脚本控制（非真实模型）：`'+json.dumps({k:control[k] for k in ['runtime_status','error_code','model_calls','tool_calls','reserved_token_upper_units']},ensure_ascii=False)+'`；实际进入脚本模型的次数：'+str(len(control['requests']))+'。','']
            lines+=['提供给模型的参考材料（B2未实测时展示脚本末轮投影）：','```json',json.dumps(view,ensure_ascii=False,indent=2),'```','',
                '语义必要事实覆盖/回答支持率/无依据断言/消歧遗漏：NOT_RUN，需对真实回答逐条来源审查。','']
            reviews.append(dict(case_id=identity,group=group,answer_sha256=sha256(actual['raw_answer'].encode()).hexdigest() if actual and actual.get('raw_answer') else None,
                status='SOURCE_REVIEW_REQUIRED' if actual and actual.get('raw_answer') else 'NOT_RUN',
                reviewed_required_fact_matches='NOT_RUN',supported_assertions='NOT_RUN',audited_assertions='NOT_RUN',
                unsupported_assertions='NOT_RUN',disambiguation_omissions='NOT_RUN',claim_source_checks=[]))
    summary=dict(body_only_surface_proxy=dict(totals),source_audit='NOT_RUN',model_calls=(real or {}).get('model_calls',0),
        cost_peak_usd=(real or {}).get('cost_peak_usd','NOT_RUN'),invoice=(real or {}).get('invoice','NOT_RUN'),
        b2_scripted_completed=sum(r['runtime_status']=='completed' for r in controls.values()),b2_scripted_total=len(controls),
        frozen_pack_sha256=sha256(args.pack.read_bytes()).hexdigest(),timings=timings)
    for name,data in [('summary.json',summary),('fact-stage-proxies.json',diagnostics),('source-review-template.json',reviews)]:
        (args.output_dir/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')
    (args.output_dir/'complete-comparison.md').write_text('\n'.join(lines)+'\n',encoding='utf-8',newline='\n')
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':main()
