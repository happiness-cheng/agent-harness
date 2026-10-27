"""终止自己创建的进程树，不能留下继续持锁/工作的解释器。"""
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from min_agent.web.launcher import DatabaseLease, DatabaseInUse


def parent_command(tmp_path):
    child = tmp_path/'child.py'
    child.write_text('''from pathlib import Path
import os,sys,time
from min_agent.web.launcher import DatabaseLease
with DatabaseLease(sys.argv[1], {'product':'owned-process-test'}):
 Path(sys.argv[2]).write_text(str(os.getpid()),encoding='utf-8')
 time.sleep(30)
''', encoding='utf-8')
    code = '''import subprocess,sys,time
subprocess.Popen([sys.executable,'-X','utf8',sys.argv[1],sys.argv[2],sys.argv[3]])
time.sleep(30)
'''
    root = Path(__file__).resolve().parents[2]
    command = [sys.executable, '-X', 'utf8', '-c', code, str(child), str(tmp_path/'resource'), str(tmp_path/'ready')]
    options = dict(cwd=root, env=dict(os.environ, PYTHONPATH=str(root/'src')),
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return command, options


def test_stopping_owned_tree_releases_descendant_lock(tmp_path):
    from min_agent.evaluation.processes import start_owned, stop_owned
    command, options = parent_command(tmp_path)
    process = start_owned(command, **options)
    try:
        end = time.monotonic()+12
        while not (tmp_path/'ready').exists() and time.monotonic()<end:
            time.sleep(.05)
        assert (tmp_path/'ready').exists(), '后代进程未进入持锁状态'
        with pytest.raises(DatabaseInUse):
            with DatabaseLease(tmp_path/'resource', {'product':'before-stop'}):
                pass
    finally:
        stop_owned(process)
    with DatabaseLease(tmp_path/'resource', {'product':'after-stop'}):
        pass
    assert process.poll() is not None


def test_timeout_stops_owned_descendants_before_returning(tmp_path, monkeypatch):
    from min_agent.evaluation import processes
    command, options = parent_command(tmp_path)
    original = processes.start_owned

    def prepared(*args, **kwargs):
        process = original(*args, **kwargs)
        end = time.monotonic()+15
        while not (tmp_path/'ready').exists() and time.monotonic()<end:
            time.sleep(.05)
        if not (tmp_path/'ready').exists():
            processes.stop_owned(process)
            raise AssertionError('超时测试的持锁环境未准备完成')
        return process

    # 等持锁环境准备好再启动 communicate 超时，避免把机器启动速度当判据。
    monkeypatch.setattr(processes, 'start_owned', prepared)
    with pytest.raises(subprocess.TimeoutExpired):
        processes.run_owned(command, timeout=.2, **options)
    assert (tmp_path/'ready').exists(), '必须在持锁后触发超时，避免空验收'
    with DatabaseLease(tmp_path/'resource', {'product':'after-timeout'}):
        pass


def test_successful_owned_command_retains_output_and_exit_status():
    from min_agent.evaluation.processes import run_owned
    result = run_owned([sys.executable, '-c', 'print("ok"); raise SystemExit(3)'],
                       timeout=10, capture_output=True, text=True)
    assert result.returncode == 3 and result.stdout.strip() == 'ok'


def exited_parent_command(tmp_path):
    """父进程自然退出；后代不继承输出管道，继续持有合成资源锁。"""
    child = tmp_path/'orphan.py'
    child.write_text('''from pathlib import Path
import os,sys,time
from min_agent.web.launcher import DatabaseLease
out=Path(sys.argv[1])
with DatabaseLease(out/'resource', {'product':'owned-orphan-test'}):
 (out/'ready').write_text(str(os.getpid()),encoding='utf-8')
 end=time.monotonic()+45
 while time.monotonic()<end and not (out/'release').exists(): time.sleep(.05)
''', encoding='utf-8')
    code = '''import os,subprocess,sys,time
from pathlib import Path
out=Path(sys.argv[2])
flags={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
subprocess.Popen([sys.executable,'-X','utf8',sys.argv[1],str(out)],
 stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**flags)
end=time.monotonic()+15
while not (out/'ready').exists() and time.monotonic()<end: time.sleep(.05)
if not (out/'ready').exists(): raise SystemExit(2)
print('root-finished')
'''
    root = Path(__file__).resolve().parents[2]
    return ([sys.executable, '-X', 'utf8', '-c', code, str(child), str(tmp_path)],
            dict(cwd=root, env=dict(os.environ, PYTHONPATH=str(root/'src'))))


def release_probe(tmp_path):
    # RED 时也让探测后代自行结束，保留失败证据但不留下持锁进程。
    (tmp_path/'release').write_text('release', encoding='utf-8')
    end = time.monotonic()+12
    while True:
        try:
            with DatabaseLease(tmp_path/'resource', {'product':'probe-cleanup'}):
                return
        except DatabaseInUse:
            if time.monotonic()>=end:
                raise
            time.sleep(.05)


def test_stop_owned_cleans_descendant_after_root_already_exited(tmp_path):
    from min_agent.evaluation.processes import start_owned, stop_owned
    command, options = exited_parent_command(tmp_path)
    process = start_owned(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)
    try:
        assert process.wait(timeout=20) == 0
        with pytest.raises(DatabaseInUse):
            with DatabaseLease(tmp_path/'resource', {'product':'before-orphan-stop'}):
                pass
        stop_owned(process)
        with DatabaseLease(tmp_path/'resource', {'product':'after-orphan-stop'}):
            pass
    finally:
        release_probe(tmp_path)
        stop_owned(process)


def test_run_owned_cleans_descendants_even_on_success(tmp_path):
    from min_agent.evaluation.processes import run_owned
    command, options = exited_parent_command(tmp_path)
    try:
        result = run_owned(command, timeout=20, capture_output=True, text=True, **options)
        assert result.returncode == 0 and result.stdout.strip() == 'root-finished'
        assert (tmp_path/'ready').exists()
        with DatabaseLease(tmp_path/'resource', {'product':'after-success'}):
            pass
    finally:
        release_probe(tmp_path)


def test_stopping_one_owned_job_does_not_stop_its_sibling(tmp_path):
    from min_agent.evaluation.processes import start_owned, stop_owned
    processes = []
    try:
        for name in ('first', 'second'):
            out = tmp_path/name
            out.mkdir()
            command, options = exited_parent_command(out)
            process = start_owned(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)
            processes.append((process, out))
            assert process.wait(timeout=20) == 0
        stop_owned(processes[0][0])
        with DatabaseLease(processes[0][1]/'resource', {'product':'first-stopped'}):
            pass
        with pytest.raises(DatabaseInUse):
            with DatabaseLease(processes[1][1]/'resource', {'product':'second-still-alive'}):
                pass
    finally:
        for process, out in processes:
            stop_owned(process)
            release_probe(out)


@pytest.mark.skipif(os.name != 'nt', reason='Windows Job 的启动前归属门')
def test_failed_job_assignment_never_executes_command(tmp_path, monkeypatch):
    from min_agent.evaluation._windows_job import WindowsJob
    from min_agent.evaluation.processes import start_owned

    def denied(self, pid):
        raise OSError('synthetic-assignment-denied')

    monkeypatch.setattr(WindowsJob, 'assign', denied)
    marker = tmp_path/'must-not-exist'
    with pytest.raises(OSError, match='synthetic-assignment-denied'):
        start_owned([sys.executable, '-c',
                     'from pathlib import Path; import sys; Path(sys.argv[1]).write_text("executed")',
                     str(marker)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert not marker.exists()


def test_owned_command_keeps_input_and_nonzero_status():
    from min_agent.evaluation.processes import run_owned
    result = run_owned([sys.executable, '-X', 'utf8', '-c',
                        'import sys; print(sys.stdin.read()); raise SystemExit(7)'],
                       input='合成输入', capture_output=True, text=True, encoding='utf-8', timeout=10)
    assert result.returncode == 7 and result.stdout.strip() == '合成输入'


@pytest.mark.skipif(os.name != 'nt', reason='Windows Job 清理异常路径')
def test_job_query_failure_still_initiates_owned_tree_shutdown(tmp_path, monkeypatch):
    from min_agent.evaluation._windows_job import WindowsJob
    from min_agent.evaluation.processes import start_owned, stop_owned
    command, options = parent_command(tmp_path)
    process = start_owned(command, **options)
    try:
        end = time.monotonic()+12
        while not (tmp_path/'ready').exists() and time.monotonic()<end:
            time.sleep(.05)
        assert (tmp_path/'ready').exists()

        def unavailable(self, deadline):
            raise OSError('synthetic-job-query-failure')

        monkeypatch.setattr(WindowsJob, '_process_handles', unavailable)
        with pytest.raises(OSError, match='synthetic-job-query-failure'):
            stop_owned(process)
        # 确认失败仍发起终止，不能等 Python 对象何时被回收。
        process.wait(timeout=5)
    finally:
        process._owned_job.close()
        process.communicate(timeout=10)
    with DatabaseLease(tmp_path/'resource', {'product':'after-query-failure'}):
        pass


@pytest.mark.skipif(os.name != 'nt', reason='Windows Job 停止阶段的新后代竞态')
def test_shutdown_blocks_children_created_after_handle_snapshot(tmp_path, monkeypatch):
    from min_agent.evaluation._windows_job import WindowsJob
    from min_agent.evaluation.processes import start_owned, stop_owned
    child = tmp_path/'late-child.py'
    child.write_text('''from pathlib import Path
import sys,time
from min_agent.web.launcher import DatabaseLease
out=Path(sys.argv[1])
with DatabaseLease(out/'resource', {'product':'late-child'}):
 (out/'child-ready').write_text('locked')
 time.sleep(30)
''', encoding='utf-8')
    code = '''from pathlib import Path
import subprocess,sys,time
out=Path(sys.argv[1])
(out/'parent-ready').write_text('waiting')
end=time.monotonic()+20
while not (out/'spawn').exists() and time.monotonic()<end: time.sleep(.01)
try:
 subprocess.Popen([sys.executable,'-X','utf8',sys.argv[2],str(out)],
  stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
except OSError:
 (out/'spawn-blocked').write_text('blocked')
'''
    root = Path(__file__).resolve().parents[2]
    process = start_owned([sys.executable, '-X', 'utf8', '-c', code, str(tmp_path), str(child)],
                          cwd=root, env=dict(os.environ, PYTHONPATH=str(root/'src')),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    original = WindowsJob._process_handles

    def concurrent_spawn(job, deadline):
        handles = original(job, deadline)
        (tmp_path/'spawn').write_text('spawn-now')
        end = time.monotonic()+12
        while not any((tmp_path/name).exists() for name in ('child-ready', 'spawn-blocked')):
            if time.monotonic()>=end:
                raise AssertionError('受控竞态未得到子进程创建结果')
            time.sleep(.01)
        return handles

    try:
        end = time.monotonic()+12
        while not (tmp_path/'parent-ready').exists() and time.monotonic()<end:
            time.sleep(.05)
        assert (tmp_path/'parent-ready').exists()
        monkeypatch.setattr(WindowsJob, '_process_handles', concurrent_spawn)
        stop_owned(process)
        assert (tmp_path/'spawn-blocked').exists(), '停止阶段仍生成了不在等待清单的新后代'
        with DatabaseLease(tmp_path/'resource', {'product':'after-late-spawn-stop'}):
            pass
    finally:
        process._owned_job.close()
        process.communicate(timeout=10)
