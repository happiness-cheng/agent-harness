"""夜间队列到既有评估/受控编码适配器的连接，不开放任意外部副作用。"""
import json
from pathlib import Path
from min_agent.evaluation.runner import run_task


def execute_job(job,route,out,*,live=False,credentials=None,proxy=None,codex_timeout=180):
    from min_agent.evaluation.night import NightOutcome
    if job.operation=='regression':
        import os,shutil,subprocess,sys
        from time import monotonic
        from min_agent.evaluation.improvement import snapshot_workspace
        from min_agent.evaluation.runner import ROOT,save_json
        from min_agent.evaluation.worker import source_version
        from min_agent.evaluation.contracts import Observation
        from min_agent.evaluation.verifier import verify
        out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False)
        workspace=snapshot_workspace(out/'workspace')
        shutil.copytree(ROOT/'tests',workspace/'tests',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
        paths=[]
        for name in job.test_paths:
            target=(workspace/name).resolve()
            if not target.is_relative_to(workspace/'tests') or not target.exists():raise PermissionError('测试范围不在工作区tests内')
            paths.append(str(target))
        environment={k:v for k,v in os.environ.items() if not any(word in k.upper() for word in ('KEY','TOKEN','SECRET','PASSWORD'))}
        environment['PYTHONPATH']=str(workspace/'src');start=monotonic()
        options=dict(cwd=workspace,env=environment,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=job.task.timeout_seconds)
        if os.name=='nt':options['creationflags']=subprocess.CREATE_NO_WINDOW
        try:
            from min_agent.evaluation.processes import run_owned
            process=run_owned([sys.executable,'-X','utf8','-m','pytest','-q',*paths],**options)
        except subprocess.TimeoutExpired:
            return NightOutcome(verdict='UNKNOWN',reason='隔离项目回归超时，未判为通过')
        (out/'pytest.txt').write_text(process.stdout+process.stderr,encoding='utf-8')
        observation=Observation(run_id=job.id,task_id=job.task.id,version_id=source_version(workspace),
            execution_status='completed' if process.returncode==0 else 'failed',elapsed_seconds=monotonic()-start,
            budget={'usage':{'model_calls':0,'tool_calls':0}},extra={'test_exit_code':process.returncode,'backend':'PURE_PROGRAM'})
        result=verify(job.task,observation);save_json(out/'result.json',result)
        return NightOutcome(verdict=result.verdict,reason='隔离项目测试退出码：'+str(process.returncode),evidence_refs=[str(out/'result.json')])
    if route=='codex':
        from time import monotonic
        start=monotonic()
        from min_agent.evaluation.improvement import TARGET,run_improvement
        if job.operation!='calculator_repair' or job.allowed_paths!=[TARGET]:return NightOutcome(verdict='UNKNOWN',reason='V1仅自动修复批准的计算器绑定；通用编码请人工审核范围')
        if not live:return NightOutcome(verdict='UNKNOWN',reason='Codex真实调用未开启；离线不伪造强模型修复')
        from min_agent.evaluation.codex_adapter import CodexCandidateModel
        # 构造不调用模型；run_improvement创建worktree后才回调generate。
        model=CodexCandidateModel(Path(out)/'codex',Path(out)/'workspace',max_calls=1,timeout=min(codex_timeout,job.task.timeout_seconds))
        try:result=run_improvement(out,model=model,max_seconds=job.task.timeout_seconds)
        except Exception as error:
            return NightOutcome(verdict='UNKNOWN',reason='受控Codex执行未能完成：'+type(error).__name__,
                evidence_refs=[str(Path(out)/'codex')],codex_invocations=model.request_count,provider_blocked=model.execution_failed)
        remaining=job.task.timeout_seconds-(monotonic()-start)
        if remaining<=0:return NightOutcome(verdict='UNKNOWN',reason='整体时间预算已用尽，尚未复核用户声明的验收',codex_invocations=model.request_count)
        acceptance=run_task(job.task,Path(out)/'job-acceptance',source_root=Path(out)/'workspace',wall_timeout=remaining)
        return NightOutcome(verdict=acceptance.verdict if result['verified'] else 'FAIL',reason=result['recommendation'],
            evidence_refs=[str(Path(out)/'job-acceptance/result.json'),str(Path(out)/'improvement.json')],codex_invocations=model.request_count)
    if route not in {'deterministic','deepseek'}:return NightOutcome(verdict='UNKNOWN',reason='没有自动化适配器')
    if route=='deepseek' and not live:return NightOutcome(verdict='UNKNOWN',reason='DeepSeek live未开启，不用脚本结果伪装真实模型')
    result=run_task(job.task,out,live=route=='deepseek',credentials=credentials,proxy=proxy,
        cost_limit=job.max_deepseek_usd if route=='deepseek' else None)
    amount=0;known=route!='deepseek'
    ledger=Path(out)/'cost-ledger.json'
    if ledger.is_file():
        data=json.loads(ledger.read_text(encoding='utf-8'));amount=data['spent_upper']+sum(data['reservations'].values());known=True
    errors=result.observation.extra.get('provider_errors',[])
    blocked=any(e.get('type')=='PermissionError' for e in errors)
    provider_blocked=any(e.get('code') in {'http_401','http_402','http_403'} or
        e.get('diagnostics',{}).get('api_error_code') in {'insufficient_quota','credit_balance_exhausted','organization_spend_limit_exceeded','project_spend_limit_exceeded'} for e in errors)
    uncertain=any(e.get('execution_state')=='unknown' for e in result.observation.trace)
    verdict='UNKNOWN' if uncertain or result.observation.transport_status!='ok' else result.verdict
    if result.observation.execution_status=='waiting_user' and result.verdict!='PASS':verdict='UNKNOWN'
    return NightOutcome(verdict=verdict,reason=','.join(result.failures) or result.verdict,
        evidence_refs=[str(Path(out)/'result.json')],deepseek_usd_upper=amount,cost_known=known,
        budget_blocked=blocked,provider_blocked=provider_blocked,non_retryable=result.safety=='FAIL')
