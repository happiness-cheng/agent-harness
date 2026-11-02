"""模拟时间与假适配器；不启动Codex生成或DeepSeek API。"""
import json
import pytest


class Clock:
    def __init__(self):self.value=1000
    def time(self):return self.value
    def sleep(self,seconds):self.value+=seconds


def setup(tmp_path,**policy):
    from min_agent.evaluation.night import NightQueue,NightPolicy
    from min_agent.evaluation.suites import task_by_id
    q=NightQueue(tmp_path/'night.json')
    q.initialize(NightPolicy(**policy))
    return q,task_by_id


def test_approval_and_dependencies_do_not_block_independent_jobs(tmp_path):
    from min_agent.evaluation.night import NightJob,run_night,NightOutcome
    q,task=setup(tmp_path)
    q.add(NightJob(id='A',goal='需要审批',task=task('runtime.calc'),requires_confirmation=True))
    q.add(NightJob(id='B',goal='独立可执行',task=task('runtime.calc')))
    q.add(NightJob(id='C',goal='依赖A',task=task('runtime.calc'),dependencies=['A']))
    calls=[]
    def execute(job,route,out):calls.append(job.id);return NightOutcome(verdict='PASS',evidence_refs=['fixture:verified'])
    result=run_night(q,executor=execute,clock=Clock(),wait=False)
    assert calls==['B']
    assert result['jobs']['A']['status']=='WAITING_FOR_USER'
    assert result['jobs']['B']['status']=='COMPLETED'
    assert result['jobs']['C']['status']=='PENDING'


def test_ten_hour_simulation_progress_and_budget_stops(tmp_path):
    from min_agent.evaluation.night import NightJob,run_night,NightOutcome
    q,task=setup(tmp_path,deepseek_usd=1,codex_calls=2)
    for i,kind in enumerate(['analysis','analysis','code','code','program']):
        q.add(NightJob(id=str(i),goal=kind,kind=kind,task=task('runtime.calc'),max_deepseek_usd=.7))
    clock=Clock();calls=[]
    def execute(job,route,out):
        calls.append((job.id,route));clock.sleep(3600)
        return NightOutcome(verdict='PASS',evidence_refs=['fixture:verified'],deepseek_usd_upper=.7 if route=='deepseek' else 0)
    result=run_night(q,executor=execute,clock=clock,wait=True)
    assert result['jobs']['1']['status']=='BLOCKED_BY_BUDGET'
    assert result['jobs']['3']['status']=='BLOCKED_BY_BUDGET'
    assert result['jobs']['4']['status']=='COMPLETED'
    assert clock.time()-result['started_at']>=36000
    assert result['usage']['codex_calls']==1 and result['quota_observability']=='UNOBSERVABLE'


def test_restart_keeps_budget_and_does_not_repeat_unknown_running_job(tmp_path):
    from min_agent.evaluation.night import NightJob,run_night,NightOutcome
    q,task=setup(tmp_path,deepseek_usd=2)
    q.add(NightJob(id='crash',goal='已开始但无结果',task=task('runtime.calc')))
    data=q.read();data['jobs']['crash']['status']='RUNNING';data['usage']['deepseek_usd_upper']=.5;q.write(data)
    called=[]
    result=run_night(q,executor=lambda *a:called.append(1),clock=Clock(),wait=False)
    assert not called and result['jobs']['crash']['status']=='WAITING_FOR_USER'
    assert result['usage']['deepseek_usd_upper']==.5


def test_failures_stop_and_reports_are_honest(tmp_path):
    from min_agent.evaluation.night import NightJob,run_night,NightOutcome
    q,task=setup(tmp_path)
    q.add(NightJob(id='bad',goal='失败反例',task=task('runtime.calc'),max_retries=1))
    called=[]
    def execute(*args):called.append(1);return NightOutcome(verdict='FAIL',reason='可复核失败',evidence_refs=['fixture:failed'])
    result=run_night(q,executor=execute,clock=Clock(),wait=False)
    assert len(called)==2 and result['jobs']['bad']['status']=='FAILED'
    assert (tmp_path/'morning-report.md').is_file()
    assert '失败' in (tmp_path/'morning-report.md').read_text(encoding='utf-8')


def test_each_generation_reserves_cost_before_call_and_missing_usage_keeps_reserve(tmp_path):
    from min_agent.evaluation.finance import CostLedger
    ledger=CostLedger(tmp_path/'cost.json',limit=.5)
    ticket=ledger.reserve(.4)
    with pytest.raises(PermissionError):ledger.reserve(.2)
    ledger.settle(ticket,usage=None)
    assert ledger.read()['spent_upper']==.4
    with pytest.raises(PermissionError):ledger.reserve(.2)


def test_dangerous_tasks_and_doubao_are_handed_back_without_execution(tmp_path):
    from min_agent.evaluation.night import NightJob,run_night
    q,task=setup(tmp_path)
    q.add(NightJob(id='deploy',goal='发布',risk='deployment',task=task('runtime.calc')))
    q.add(NightJob(id='doubao',goal='人工接力',kind='manual',task=task('runtime.calc')))
    calls=[]
    result=run_night(q,executor=lambda *a:calls.append(1),clock=Clock(),wait=False)
    assert not calls and all(j['status']=='WAITING_FOR_USER' for j in result['jobs'].values())


def test_provider_quota_failure_stops_same_provider_but_program_jobs_continue(tmp_path):
    from min_agent.evaluation.night import NightJob,run_night,NightOutcome
    q,task=setup(tmp_path,deepseek_usd=5)
    for identity,kind in [('A','analysis'),('B','analysis'),('C','program')]:
        q.add(NightJob(id=identity,goal=identity,kind=kind,task=task('runtime.calc')))
    called=[]
    def execute(job,route,out):
        called.append(job.id)
        if route=='deepseek':return NightOutcome(verdict='UNKNOWN',reason='配额不足',provider_blocked=True,cost_known=False)
        return NightOutcome(verdict='PASS',evidence_refs=['fixture:verified'])
    result=run_night(q,executor=execute,clock=Clock(),wait=False)
    assert called==['A','C']
    assert result['jobs']['B']['status']=='BLOCKED_BY_BUDGET'
    assert result['provider_blocks']['deepseek']
