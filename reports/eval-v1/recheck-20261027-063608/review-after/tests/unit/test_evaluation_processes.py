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


def test_timeout_stops_owned_descendants_before_returning(tmp_path):
    from min_agent.evaluation.processes import run_owned
    command, options = parent_command(tmp_path)
    with pytest.raises(subprocess.TimeoutExpired):
        run_owned(command, timeout=5, **options)
    assert (tmp_path/'ready').exists(), '必须在持锁后触发超时，避免空验收'
    with DatabaseLease(tmp_path/'resource', {'product':'after-timeout'}):
        pass


def test_successful_owned_command_retains_output_and_exit_status():
    from min_agent.evaluation.processes import run_owned
    result = run_owned([sys.executable, '-c', 'print("ok"); raise SystemExit(3)'],
                       timeout=10, capture_output=True, text=True)
    assert result.returncode == 3 and result.stdout.strip() == 'ok'
