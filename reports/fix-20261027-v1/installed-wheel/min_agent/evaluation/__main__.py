import argparse
import json
from pathlib import Path
from min_agent.evaluation.contracts import TaskSpec,EvaluationResult
from min_agent.evaluation.runner import run_suite,compare_results,save_json
from min_agent.evaluation.suites import default_suite,task_by_id


def main():
    p=argparse.ArgumentParser(description='Adrian统一验证：默认离线/合成/隔离，不连接生产数据库')
    sub=p.add_subparsers(dest='command',required=True)
    run=sub.add_parser('run');run.add_argument('--out',type=Path,required=True);run.add_argument('--tasks',nargs='*');run.add_argument('--file',type=Path)
    run.add_argument('--live',action='store_true');run.add_argument('--credentials',type=Path);run.add_argument('--proxy');run.add_argument('--request-ceiling',type=int,default=16)
    run.add_argument('--cost-limit',type=float)
    export=sub.add_parser('export');export.add_argument('--out',type=Path,required=True)
    compare=sub.add_parser('compare');compare.add_argument('before',type=Path);compare.add_argument('after',type=Path);compare.add_argument('--out',type=Path,required=True)
    improve=sub.add_parser('improve');improve.add_argument('--out',type=Path,required=True);improve.add_argument('--candidate',type=Path)
    improve.add_argument('--live',action='store_true');improve.add_argument('--credentials',type=Path);improve.add_argument('--proxy')
    improve.add_argument('--codex',action='store_true')
    review=sub.add_parser('review');review.add_argument('--result',type=Path,required=True);review.add_argument('--out',type=Path,required=True)
    review.add_argument('--check',required=True);review.add_argument('--decision',choices=['PASS','FAIL','UNKNOWN'],required=True)
    review.add_argument('--reviewer',required=True);review.add_argument('--reason',required=True)
    a=p.parse_args()
    if a.command=='export':save_json(a.out,[t.model_dump(mode='json') for t in default_suite()]);return
    if a.command=='review':
        from min_agent.evaluation.review import record_review
        record_review(a.result,a.out,check_id=a.check,decision=a.decision,reviewer=a.reviewer,reason=a.reason);return
    if a.command=='compare':
        before=EvaluationResult.model_validate_json(a.before.read_text(encoding='utf-8'));after=EvaluationResult.model_validate_json(a.after.read_text(encoding='utf-8'))
        save_json(a.out,compare_results(before,after));return
    if a.command=='improve':
        from min_agent.evaluation.improvement import run_improvement
        candidate=json.loads(a.candidate.read_text(encoding='utf-8')) if a.candidate else None
        if a.codex:
            if not a.live:p.error('真实Codex调用必须显式--live；不会暗中消耗订阅额度')
            from min_agent.evaluation.codex_adapter import CodexCandidateModel
            result=run_improvement(a.out,model=CodexCandidateModel(a.out/'codex',a.out/'workspace',max_calls=1,timeout=180))
        elif a.live:
            if not a.credentials:p.error('live修复需要显式项目外凭据')
            from min_agent.llm.deepseek import DeepSeekResponsesModel,DeepSeekSettings
            with DeepSeekResponsesModel(DeepSeekSettings.from_credentials_file(a.credentials,max_requests=2,max_output_tokens=None),proxy=a.proxy) as model:
                result=run_improvement(a.out,model=model)
        else:result=run_improvement(a.out,candidate=candidate)
        print(json.dumps(result,ensure_ascii=False));return
    tasks=[TaskSpec.model_validate(t) for t in json.loads(a.file.read_text(encoding='utf-8'))] if a.file else [task_by_id(i) for i in a.tasks] if a.tasks else default_suite()
    _,summary=run_suite(tasks,a.out,live=a.live,credentials=a.credentials,proxy=a.proxy,request_ceiling=a.request_ceiling,cost_limit=a.cost_limit)
    raise SystemExit(0 if summary['expected_outcomes_match'] else 1)


if __name__=='__main__':main()
