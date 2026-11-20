"""统一验证的真实Harness链路；所有业务数据均为合成隔离Fixture。"""
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
        if identity=='security.cross_session':
            assert result.verdict=='UNKNOWN',(identity,result.model_dump())
            assert all(c.verdict=='PASS' for c in result.checks if c.id in {'private','no-artifact'})
            assert any(c.id=='auto.effect' and c.verdict=='UNKNOWN' for c in result.checks)
        else:
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
