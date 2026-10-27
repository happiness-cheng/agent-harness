"""仅管理本模块创建的进程树；不按名称结束进程、不绕过Runtime预算。"""
import os
from pathlib import Path
import signal
import subprocess


def start_owned(command, **options):
    if options.get('shell'):
        raise ValueError('受控任务不使用隐式Shell')
    if os.name == 'nt':
        options['creationflags'] = options.get('creationflags', 0) | subprocess.CREATE_NO_WINDOW
    else:
        options['start_new_session'] = True
    return subprocess.Popen(command, **options)


def stop_owned(process):
    """先终止仍活动的自有进程树，再排空管道；不能证明退出时直接报错。"""
    if process.poll() is None:
        if os.name == 'nt':
            # Windows venv的Popen PID可能只是启动器，必须包括其后代。
            taskkill = Path(os.environ['SystemRoot'])/'System32'/'taskkill.exe'
            stopped = subprocess.run([str(taskkill), '/PID', str(process.pid), '/T', '/F'],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
            if stopped.returncode and process.poll() is None:
                raise RuntimeError('无法确认本次子进程树已停止，保留未知结果')
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    # 后代继承的输出管道也必须关闭，不能只等待启动器的句柄。
    return process.communicate(timeout=10)


def run_owned(command, *, input=None, capture_output=False, timeout=None, **options):
    if capture_output:
        if 'stdout' in options or 'stderr' in options:
            raise ValueError('capture_output不能同时指定stdout/stderr')
        options.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if input is not None:
        if 'stdin' in options:
            raise ValueError('input不能同时指定stdin')
        options['stdin'] = subprocess.PIPE
    process = start_owned(command, **options)
    try:
        stdout, stderr = process.communicate(input=input, timeout=timeout)
    except BaseException:
        stop_owned(process)
        raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
