"""执行器必须完成自有进程清理，再读取结果；无法确认时留下 UNKNOWN。"""
import json

from min_agent.evaluation import runner
from min_agent.evaluation.suites import task_by_id


def test_runner_cleans_owned_process_after_normal_worker_exit(tmp_path, monkeypatch):
    original = runner.stop_owned
    stopped = []

    def stop(process):
        stopped.append(process.pid)
        return original(process)

    monkeypatch.setattr(runner, 'stop_owned', stop)
    result = runner.run_task(task_by_id('runtime.calc'), tmp_path/'normal')
    assert result.verdict == 'PASS'
    assert len(stopped) == 1


def test_runner_keeps_unknown_if_cleanup_cannot_be_confirmed(tmp_path, monkeypatch):
    original = runner.stop_owned

    def uncertain(process):
        original(process)  # 实际探测进程不遗留，模拟的是确认失败。
        raise RuntimeError('synthetic-cleanup-unknown')

    monkeypatch.setattr(runner, 'stop_owned', uncertain)
    out = tmp_path/'uncertain'
    result = runner.run_task(task_by_id('runtime.calc'), out)
    assert result.verdict == 'UNKNOWN'
    assert result.observation.transport_status == 'worker_error'
    assert result.observation.extra['process_failure'] == {'stage': 'cleanup', 'error_type': 'RuntimeError'}
    assert json.loads((out/'result.json').read_text(encoding='utf-8'))['verdict'] == 'UNKNOWN'


def test_runner_records_start_failure_without_claiming_execution(tmp_path, monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError('synthetic-job-assignment-failure')

    monkeypatch.setattr(runner, 'start_owned', unavailable)
    out = tmp_path/'startup'
    result = runner.run_task(task_by_id('runtime.calc'), out)
    assert result.verdict == 'UNKNOWN'
    assert result.observation.model_requests == 0
    assert result.observation.extra['process_failure'] == {'stage': 'start', 'error_type': 'OSError'}
    assert (out/'result.json').exists()
