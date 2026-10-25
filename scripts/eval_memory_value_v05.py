"""有界单轮回答实测或NOT_RUN报告；没有授权则不读取凭据。"""
import argparse
import gzip
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from min_agent.long_memory.answer_run import run_authorized,validate_authorization
from min_agent.long_memory.answer_scoring import answer_metrics,validate_review


def load(path):return json.loads(gzip.decompress(path.read_bytes()))


def report(pack,result,reviews):
    actual={(r['case_id'],r['group']):r for r in (result or {}).get('responses',[])}
    reviewed={(r['case_id'],r['group']):r for r in reviews}
    rows=[]
    for case in pack['cases']:
        for group in ['A','B','C']:
            response=actual.get((case['case_id'],group));metrics=answer_metrics(case,group,response)
            annotation=reviewed.get((case['case_id'],group))
            if annotation is not None:
                if response is None:raise ValueError('没有真实回答就不能导入评分')
                validate_review(case,group,response,annotation)
                metrics.update(status='ANSWERED_SOURCE_REVIEWED',book_scores=annotation['book_scores'],dimensions=annotation['dimensions'],
                    verified_failure_categories=annotation.get('failure_categories',[]),reviewer=annotation['reviewer'],
                    rationale=annotation['rationale'],bottleneck='REVIEWED_CASE_ONLY')
            rows.append(dict(case_id=case['case_id'],group=group,question=case['question'],
                reference_bytes=case['groups'][group]['reference_bytes'],request_sha256=case['groups'][group]['request_sha256'],
                response=response,metrics=metrics,evaluation_audit=case['evaluation_audit']))
    return dict(protocol=pack['protocol'],rows=rows,model_calls=(result or {}).get('model_calls',0),
        run_status=(result or {}).get('status','NOT_RUN'),
        known_peak_usage_cost_usd=(result or {}).get('total_peak_usage_cost_usd','NOT_RUN'),
        reserved_upper_usd=(result or {}).get('reserved_upper_usd','NOT_RUN'),
        invoice_cost=(result or {}).get('invoice_cost','NOT_RUN'),
        overall_bottleneck='SOURCE_REVIEW_AVAILABLE_SEE_PAIRED_ANALYSIS' if reviewed else 'NOT_DETERMINED_WITHOUT_PAIRED_SEMANTIC_REVIEW',
        external_judge='NOT_RUN',private_imports=0)


def markdown(pack,scored):
    lines=['# Adrian Memory v0.5：逐题真实回答评测','',
        '范围：生产自动首轮Memory投影后的受控单次回答；不是自主工具循环或业务执行成绩。A无长期材料，B生产默认4096字节自动视图，C只给截至任务时点的原始必要证据。三组共享system/任务/模型/推理配置与总预算。','',
        f"实际调用：{scored['model_calls']}；执行状态：{scored['run_status']}；已知峰值计价usage费用：{scored['known_peak_usage_cost_usd']} USD；账单实付：{scored['invoice_cost']}。",'',
        '未执行/未审语义维度全部NOT_RUN。事实表面覆盖只是代理，不是事实准确率；自报引用不是模型内部因果证明。没有外部Judge。','']
    by={(r['case_id'],r['group']):r for r in scored['rows']}
    for case in pack['cases']:
        lines+=['## '+case['case_id'],'','问题：'+case['question'],'','任务时点：'+case['as_of'],'']
        if case['evaluation_audit']:lines+=['评分审计：'+'；'.join(case['evaluation_audit']),'']
        for group in ['A','B','C']:
            row=by[(case['case_id'],group)];response=row['response'];metric=row['metrics']
            lines += ['### '+group,'',f"参考材料：{row['reference_bytes']} UTF-8字节；请求SHA256 `{row['request_sha256']}`。",'',
                '模型原始回答：','```text',response['raw_answer'] if response else 'NOT_RUN：未获本轮模型调用授权或该请求未执行。','```','',
                '评分：`'+json.dumps(dict(book=metric['book_scores'],dimensions=metric['dimensions'],
                    surface_recall=metric['fact_surface_recall'],failure_categories=metric['verified_failure_categories']),ensure_ascii=False)+'`','']
            if response:lines+=['使用/声称使用证据：`'+json.dumps(metric.get('cited_ids',[]),ensure_ascii=False)+'`；usage：`'+json.dumps(response.get('usage'),ensure_ascii=False)+'`；耗时：'+str(response['latency_ms'])+' ms。','']
            lines+=['实际提供的参考材料：','```json',case['groups'][group]['messages'][-1]['content'],'```','']
    lines+=['## 判断边界','',
        '没有真实回答或完整成对源审时，不能判断主瓶颈在模型。STORAGE/RETRIEVAL/RANKING/PACKING必须结合合法存储、FTS及Core两条路径；Core可绕过FTS直接注入，不可仅因FTS漏召就说模型没有证据。REASONING/CONFLICT需要已审正确证据确实可见而模型仍失败。争议Gold、等价证据不足、当前时点矛盾归EVALUATION。','',
        '原书四项precision/recall/reasoning/proactivity需依据原始对话审查，额外实体/时间/冲突/幻觉/引用独立记录。未审不算对、不算错。当前fixture模拟批准完整原文，不证明自动提炼/真实确认质量。','',
        '本轮不修改任何生产默认，不迁移数据库，不导入私人记录、不推远端。']
    return '\n'.join(lines)+'\n'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['run','report'])
    parser.add_argument('--pack',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--allow-live',action='store_true')
    parser.add_argument('--authorization',type=Path)
    parser.add_argument('--credentials-file',type=Path)
    parser.add_argument('--proxy')
    parser.add_argument('--results',type=Path)
    parser.add_argument('--reviews',type=Path)
    args=parser.parse_args()
    if args.output.exists():parser.error('不覆盖已有输出')
    pack=load(args.pack);pack_sha=sha256(args.pack.read_bytes()).hexdigest()
    if args.mode=='run':
        if not args.allow_live or not args.authorization or not args.credentials_file or not args.proxy:
            parser.error('需要本轮明确授权文件及allow-live/指定凭据/代理；尚未读取凭据')
        permission=json.loads(args.authorization.read_text(encoding='utf-8'))
        validate_authorization(pack,permission,pack_sha)
        for p,h in pack['source_hashes'].items():
            if sha256((ROOT/p).read_bytes()).hexdigest()!=h:parser.error('制包源码已变，需重新检查，尚未读取凭据')
        def factory():
            from min_agent.llm.deepseek import DeepSeekSettings,DeepSeekResponsesModel
            settings=DeepSeekSettings.from_credentials_file(args.credentials_file,model=pack['model'],
                reasoning_effort=pack['reasoning_effort'],max_output_tokens=pack['max_output_tokens'],
                max_requests=permission['max_requests'],timeout_seconds=180.0)
            return DeepSeekResponsesModel(settings,proxy=args.proxy)
        result=run_authorized(pack,permission,pack_sha=pack_sha,factory=factory,
            journal=args.output.with_suffix('.jsonl'),claim=args.authorization.with_suffix('.claimed.json'))
        result.update(pack_sha256=pack_sha,source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip())
        with args.output.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    else:
        result=json.loads(args.results.read_text(encoding='utf-8')) if args.results else None
        if result is not None and result.get('pack_sha256')!=pack_sha:parser.error('实际回答不对应此请求包')
        reviews=json.loads(args.reviews.read_text(encoding='utf-8')) if args.reviews else []
        scored=report(pack,result,reviews)
        with args.output.open('x',encoding='utf-8') as f:json.dump(scored,f,ensure_ascii=False,indent=2)
        with args.output.with_suffix('.md').open('x',encoding='utf-8') as f:f.write(markdown(pack,scored))
    print(json.dumps(dict(mode=args.mode,output=str(args.output),model_calls=(result or {}).get('model_calls',0)),ensure_ascii=False))


if __name__=='__main__':main()
