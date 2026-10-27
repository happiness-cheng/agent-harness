"""单调度者、持久任务队列；执行交给原Harness与独立Verifier。"""
from hashlib import sha256
import json
from math import floor
from pathlib import Path
import time
from typing import Literal
from uuid import uuid4
from pydantic import Field
from min_agent.evaluation.contracts import Contract,TaskSpec,EvaluationResult,digest,Verdict
from min_agent.evaluation.journal import JsonDocument
from min_agent.evaluation.finance import micros
from min_agent.web.launcher import DatabaseLease


class NightPolicy(Contract):
    hours:float=Field(default=10,gt=0,le=24)
    deepseek_usd:float=Field(default=0,ge=0)
    codex_calls:int=Field(default=0,ge=0,le=100)
    reserve_ratio:float=Field(default=.2,ge=0,lt=1)
    codex_timeout_seconds:int=Field(default=180,ge=1,le=3600)


class NightJob(Contract):
    id:str=Field(pattern=r'^[a-zA-Z0-9_.-]+$')
    goal:str=Field(min_length=1,max_length=2000)
    kind:Literal['program','analysis','code','manual']='program'
    task:TaskSpec
    priority:int=0
    dependencies:list[str]=Field(default_factory=list,max_length=50)
    requires_confirmation:bool=False
    risk:Literal['low','deletion','deployment','installation','permission_change','external_message','unapproved_cost']='low'
    max_retries:int=Field(default=1,ge=0,le=3)
    allow_escalation:bool=False
    max_deepseek_usd:float=Field(default=.75,gt=0)
    allowed_paths:list[str]=Field(default_factory=list)
    operation:Literal['evaluation','regression','calculator_repair']='evaluation'
    test_paths:list[str]=Field(default_factory=list,max_length=30)


class NightOutcome(Contract):
    verdict:Verdict
    reason:str=''
    evidence_refs:list[str]=Field(default_factory=list)
    deepseek_usd_upper:float=Field(default=0,ge=0)
    cost_known:bool=True
    budget_blocked:bool=False
    non_retryable:bool=False
    codex_invocations:int=0


class NightQueue(JsonDocument):
    def initialize(self,policy):
        with self.transaction():
            if self.path.exists():raise FileExistsError('队列已存在，不能重置预算')
            self.write(dict(version=1,policy=policy.model_dump(),started_at=None,jobs={},
                usage=dict(deepseek_usd_upper=0,reserved_deepseek_usd=0,codex_calls=0),
                quota_observability='UNOBSERVABLE',events=[]))
    def add(self,job):
        if job.task.data_classification=='private':raise PermissionError('夜间V1不自动处理私人数据')
        if any(Path(p).is_absolute() or '..' in Path(p).parts or ':' in p or p.startswith('-') for p in job.allowed_paths+job.test_paths):raise ValueError('允许路径必须是工作区相对路径')
        if job.operation=='regression' and (job.kind!='program' or not job.test_paths or any(not p.startswith('tests/') for p in job.test_paths)):
            raise ValueError('回归仅允许program类别和明确tests/下相对路径')
        with self.transaction():
            data=self.read()
            if job.id in data['jobs']:raise ValueError('任务ID已存在，不能覆盖执行证据')
            data['jobs'][job.id]=dict(spec=job.model_dump(mode='json'),status='PENDING',attempts=[],reason='',escalations=0)
            def visit(identity,stack):
                if identity in stack:raise ValueError('依赖存在环')
                if identity not in data['jobs']:return
                for child in data['jobs'][identity]['spec']['dependencies']:visit(child,stack|{identity})
            for identity in data['jobs']:visit(identity,set())
            self.write(data)


def route_for(job,record):
    if job.kind=='manual':return 'doubao_manual'
    if job.kind=='program':return 'deterministic'
    if job.kind=='code' or record['escalations']>0:return 'codex'
    return 'deepseek'


def write_morning(queue,data):
    lines=['# Adrian 早晨工作报告',
        '\n运行类型：'+data.get('executor_kind','UNKNOWN')+'。注入测试适配器的费用/模型调用数据属于模拟，不是真实账单或真实模型能力成绩。',
        '\nCodex真实剩余额度：不可观测；只记录CLI启动次数与证据。',
        '\n费用为配置峰值费率/未知预留的上界估计，不是Provider账单。',
        '\n时钟：'+data.get('clock_kind','UNKNOWN')+'；停止原因：'+data.get('stop_reason','尚在执行'),
        '\n| 任务 | 状态 | 尝试 | 原因 / 验证证据 |','| --- | --- | --- | --- |']
    for identity,item in data['jobs'].items():
        evidence=[r for a in item['attempts'] for r in a.get('outcome',{}).get('evidence_refs',[])]
        text=(item['reason']+' '+'; '.join(evidence)).replace('|','/').replace('\n',' ')
        lines.append(f"| {identity} | {item['status']} | {len(item['attempts'])} | {text} |")
    lines+=['\n预算与可观测使用：','```json',json.dumps(data['usage'],ensure_ascii=False,indent=2),'```',
        '\nFAILED表示失败；WAITING_FOR_USER等待审批/未知结果核对；BLOCKED_BY_BUDGET不继续相关付费调用。',
        '\n下一步：先核对未知副作用与审批事项，再决定是否补充预算；已完成任务不自动重跑。']
    (queue.path.parent/'morning-report.md').write_text('\n'.join(lines),encoding='utf-8')


def recover(data):
    for item in data['jobs'].values():
        if item['status'] not in {'RUNNING','VERIFYING'}:continue
        last=item['attempts'][-1] if item['attempts'] else {}
        evidence=last.get('outcome',{}).get('evidence_refs',[])
        # 仅恢复已经持久化并带hash的验收结果；RUNNING未知结果绝不盲目重放。
        valid=False
        if item['status']=='VERIFYING' and evidence and last.get('evidence_sha256'):
            path=Path(evidence[0])
            if path.is_file() and sha256(path.read_bytes()).hexdigest()==last['evidence_sha256']:
                try:
                    result=EvaluationResult.model_validate_json(path.read_text(encoding='utf-8'))
                    valid=result.verdict=='PASS' and result.spec_sha256==last.get('effective_spec_sha256',digest(TaskSpec.model_validate(item['spec']['task'])))
                except (ValueError,OSError):valid=False
        item['status']='COMPLETED' if valid else 'WAITING_FOR_USER'
        item['reason']='从持久验收证据恢复' if valid else '中断后结果未知；保留预算预留，人工核对后再决定，不重放'


def run_night(queue,*,executor=None,clock=time,wait=False,live=False,credentials=None,proxy=None):
    from min_agent.evaluation.night_executor import execute_job
    custom=executor is not None
    executor=executor or (lambda job,route,out:execute_job(job,route,out,live=live,credentials=credentials,proxy=proxy,
        codex_timeout=NightPolicy.model_validate(queue.read()['policy']).codex_timeout_seconds))
    # 与文档读写锁分离：只允许一个调度者，但任务可以通过短事务追加。
    with DatabaseLease(str(queue.path)+'.runner',{'product':'adrian-night-runner'}):
        with queue.transaction():
            data=queue.read();policy=NightPolicy.model_validate(data['policy']);recover(data)
            if data['started_at'] is None:data['started_at']=clock.time()
            data['clock_kind']='INJECTED_SIMULATION' if clock is not time else 'WALL_CLOCK'
            data['executor_kind']='INJECTED_TEST_ADAPTER' if custom else 'EXISTING_HARNESS_AND_CODEX_ADAPTER'
            queue.write(data)
        deadline=data['started_at']+policy.hours*3600
        while clock.time()<deadline:
            chosen=None
            with queue.transaction():
                data=queue.read();policy=NightPolicy.model_validate(data['policy']);changed=False
                for identity,item in sorted(data['jobs'].items(),key=lambda v:(-v[1]['spec']['priority'],v[0])):
                    if item['status'] not in {'PENDING','BLOCKED_BY_BUDGET'}:continue
                    job=NightJob.model_validate(item['spec'])
                    if job.requires_confirmation or job.risk!='low' or job.kind=='manual':
                        item.update(status='WAITING_FOR_USER',reason='需要批准或豆包人工接力，夜间不绕过');changed=True
                        if job.kind=='manual':
                            (queue.path.parent/('doubao-'+job.id+'.json')).write_text(job.model_dump_json(indent=2),encoding='utf-8')
                        continue
                    missing=[d for d in job.dependencies if d not in data['jobs']]
                    if missing:
                        item.update(status='WAITING_FOR_USER',reason='缺失依赖：'+','.join(missing));changed=True;continue
                    if any(data['jobs'][d]['status']!='COMPLETED' for d in job.dependencies):
                        if item['reason']!='依赖尚未完成':item['reason']='依赖尚未完成';changed=True
                        continue
                    fingerprint=digest([data['usage'],data['policy']])
                    if item['status']=='BLOCKED_BY_BUDGET' and item.get('budget_fingerprint')==fingerprint:continue
                    route=route_for(job,item);usage=data['usage'];reason=None
                    if route=='deepseek':
                        available=floor(micros(policy.deepseek_usd)*(1-policy.reserve_ratio))-micros(usage['deepseek_usd_upper'])-micros(usage['reserved_deepseek_usd'])
                        if micros(job.max_deepseek_usd)>available:reason='DeepSeek费用包络不足，保留预留额度'
                    if route=='codex' and usage['codex_calls']>=floor(policy.codex_calls*(1-policy.reserve_ratio)):
                        reason='Codex启动次数达到保守上限，真实剩余额度不可观测'
                    if reason:
                        changed=changed or item['status']!='BLOCKED_BY_BUDGET';item.update(status='BLOCKED_BY_BUDGET',reason=reason);continue
                    out=queue.path.parent/'attempts'/job.id/('attempt-'+str(len(item['attempts'])+1))
                    if route=='deepseek':usage['reserved_deepseek_usd']+=job.max_deepseek_usd
                    if route=='codex':usage['codex_calls']+=1
                    item['attempts'].append(dict(route=route,started_at=clock.time(),directory=str(out)))
                    item.update(status='RUNNING',reason='已持久化执行意图及预算预留')
                    remaining=deadline-clock.time()
                    job=job.model_copy(update={'task':job.task.model_copy(update={'timeout_seconds':min(job.task.timeout_seconds,max(.001,remaining))})})
                    item['attempts'][-1]['effective_spec_sha256']=digest(job.task)
                    chosen=(job,route,out);changed=True;break
                if changed:queue.write(data)
            if chosen is None:
                if not wait:break
                clock.sleep(min(30,max(0,deadline-clock.time())));continue
            job,route,out=chosen
            try:outcome=NightOutcome.model_validate(executor(job,route,out))
            except Exception as error:
                outcome=NightOutcome(verdict='UNKNOWN',reason='执行器异常：'+type(error).__name__,cost_known=False)
            with queue.transaction():
                data=queue.read();item=data['jobs'][job.id];last=item['attempts'][-1]
                if route=='deepseek':
                    data['usage']['reserved_deepseek_usd']=max(0,data['usage']['reserved_deepseek_usd']-job.max_deepseek_usd)
                    data['usage']['deepseek_usd_upper']+=outcome.deepseek_usd_upper if outcome.cost_known else job.max_deepseek_usd
                last.update(ended_at=clock.time(),outcome=outcome.model_dump(mode='json'))
                data['usage']['observed_codex_invocations']=data['usage'].get('observed_codex_invocations',0)+outcome.codex_invocations
                item['status']='VERIFYING';item['reason']='检查独立验证证据'
                if outcome.evidence_refs and Path(outcome.evidence_refs[0]).is_file():
                    last['evidence_sha256']=sha256(Path(outcome.evidence_refs[0]).read_bytes()).hexdigest()
                queue.write(data)
                if outcome.verdict=='PASS' and outcome.evidence_refs:item.update(status='COMPLETED',reason='独立验收通过，证据已保存')
                elif outcome.budget_blocked:item.update(status='BLOCKED_BY_BUDGET',reason=outcome.reason,budget_fingerprint=digest([data['usage'],data['policy']]))
                elif outcome.verdict=='UNKNOWN':item.update(status='WAITING_FOR_USER',reason=outcome.reason or '证据不足或执行结果未知')
                elif outcome.non_retryable:item.update(status='FAILED',reason='安全/契约失败，不通过重试绕过：'+outcome.reason)
                elif len(item['attempts'])<=job.max_retries:item.update(status='PENDING',reason='明确失败，允许有限重试')
                elif job.allow_escalation and route=='deepseek' and item['escalations']==0:
                    item.update(status='PENDING',reason='有限升级Codex');item['escalations']=1
                else:item.update(status='FAILED',reason=outcome.reason or '验证失败，达到重试/升级边界')
                queue.write(data)
        with queue.transaction():
            result=queue.read();result['last_finished_at']=clock.time()
            result['stop_reason']='deadline_reached' if clock.time()>=deadline else 'no_eligible_jobs'
            queue.write(result)
        write_morning(queue,result);return result
