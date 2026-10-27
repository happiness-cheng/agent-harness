"""统一验证的真实Harness链路；所有业务数据均为合成隔离Fixture。"""
from pathlib import Path
import json

import pytest


def test_real_harness_task_and_false_success_have_different_verdicts(tmp_path):
    from min_agent.evaluation.runner import run_task
    from min_agent.evaluation.suites import task_by_id
    success=run_task(task_by_id('tools.add'),tmp_path/'good')
    false=run_task(task_by_id('tools.false_success'),tmp_path/'false')
    assert success.verdict=='PASS' and len(success.observation.todos)==1
    assert success.observation.execution_status=='completed'
    assert false.verdict=='FAIL' and not false.observation.todos
    assert false.observation.execution_status=='completed'
    assert false.honesty=='FAIL'
    assert success.observation.session_id!=false.observation.session_id
    assert (tmp_path/'good'/'result.json').is_file()


def test_five_categories_share_contract_and_expected_rejections_are_real(tmp_path):
    from min_agent.evaluation.runner import run_task
    from min_agent.evaluation.suites import task_by_id
    cases=['research.conflict','memory.recall','tools.update','runtime.recover','security.plan']
    for identity in cases:
        result=run_task(task_by_id(identity),tmp_path/identity)
        assert result.verdict=='PASS',(identity,result.model_dump())
        assert result.observation.trace and result.observation.version_id
        assert result.observation.budget


def test_timeout_is_unknown_not_success_and_cannot_reuse_run_directory(tmp_path):
    from min_agent.evaluation.runner import run_task
    from min_agent.evaluation.suites import task_by_id
    spec=task_by_id('runtime.timeout').model_copy(update={'timeout_seconds':0.2})
    result=run_task(spec,tmp_path/'timeout')
    assert result.verdict=='UNKNOWN' and result.observation.transport_status=='timeout'
    with pytest.raises(FileExistsError):run_task(spec,tmp_path/'timeout')


def test_unconfirmed_revoked_foreign_memory_and_cross_session_sources_do_not_leak(tmp_path):
    from min_agent.evaluation.runner import run_task
    from min_agent.evaluation.suites import task_by_id
    for identity in ('memory.retracted','memory.foreign','security.cross_session','security.injection','security.arguments'):
        result=run_task(task_by_id(identity),tmp_path/identity)
        assert result.verdict=='PASS',(identity,result.model_dump())


def test_comparison_refuses_different_acceptance_standards(tmp_path):
    from min_agent.evaluation.runner import run_task,compare_results
    from min_agent.evaluation.suites import task_by_id
    spec=task_by_id('runtime.calc')
    first=run_task(spec,tmp_path/'one')
    second=run_task(spec,tmp_path/'two')
    assert compare_results(first,second)['comparable'] is True
    changed=second.model_copy(update={'spec_sha256':'different'})
    with pytest.raises(ValueError):compare_results(first,changed)


def test_night_program_job_runs_real_project_regression_without_models(tmp_path):
    from min_agent.evaluation.contracts import TaskSpec,CheckSpec
    from min_agent.evaluation.night import NightJob,NightPolicy,NightQueue,run_night
    task=TaskSpec(id='project-regression',category='runtime',prompt='运行现有计算器回归',
        timeout_seconds=120,checks=[CheckSpec(id='pytest',kind='test_exit',expected=0)])
    queue=NightQueue(tmp_path/'queue.json');queue.initialize(NightPolicy())
    queue.add(NightJob(id='regression',goal=task.prompt,task=task,operation='regression',test_paths=['tests/unit/test_calculator.py']))
    result=run_night(queue)
    assert result['jobs']['regression']['status']=='COMPLETED'
    assert result['usage']['codex_calls']==0 and result['usage']['deepseek_usd_upper']==0
    evidence=Path(result['jobs']['regression']['attempts'][0]['outcome']['evidence_refs'][0])
    assert json.loads(evidence.read_text(encoding='utf-8'))['observation']['extra']['backend']=='PURE_PROGRAM'


def test_actual_scheduler_process_crash_does_not_replay_running_job(tmp_path):
    import os,subprocess,sys,time
    from min_agent.evaluation.night import NightQueue,NightPolicy,NightJob,run_night
    from min_agent.evaluation.suites import task_by_id
    queue=NightQueue(tmp_path/'queue.json');queue.initialize(NightPolicy())
    for identity in ['A','B']:queue.add(NightJob(id=identity,goal=identity,task=task_by_id('runtime.calc')))
    marker=tmp_path/'started.txt'
    code='''from pathlib import Path
import sys,time
from min_agent.evaluation.night import NightQueue,run_night
def hang(job,route,out):
 Path(sys.argv[2]).write_text('started once',encoding='utf-8')
 time.sleep(300)
run_night(NightQueue(sys.argv[1]),executor=hang)
'''
    root=Path(__file__).resolve().parents[2]
    options=dict(cwd=root,env=dict(os.environ,PYTHONPATH=str(root/'src')),stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    if os.name=='nt':options['creationflags']=subprocess.CREATE_NO_WINDOW
    process=subprocess.Popen([sys.executable,'-X','utf8','-c',code,str(queue.path),str(marker)],**options)
    try:
        end=time.monotonic()+15
        while not marker.exists() and time.monotonic()<end:time.sleep(.1)
        assert marker.exists()
    finally:
        process.terminate();process.wait(timeout=10)
    recovered=run_night(NightQueue(queue.path))
    assert recovered['jobs']['A']['status']=='WAITING_FOR_USER'
    assert recovered['jobs']['B']['status']=='COMPLETED'
    assert len(recovered['jobs']['A']['attempts'])==1
