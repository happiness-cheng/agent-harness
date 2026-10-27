"""仅管理本模块创建的进程树；不按名称结束进程、不绕过Runtime预算。"""
import os
import signal
import subprocess


def start_owned(command, **options):
    if options.get('shell'):
        raise ValueError('受控任务不使用隐式Shell')
    if os.name == 'nt':
        from min_agent.evaluation._windows_job import WindowsJob
        job = WindowsJob()
        # 先纳入 Job，再恢复主线程，关闭启动器与后代之间的登记竞态。
        options['creationflags'] = options.get('creationflags', 0) | subprocess.CREATE_NO_WINDOW | 0x4
        process = None
        try:
            process = subprocess.Popen(command, **options)
            job.assign(process.pid)
            process._owned_job = job
            job.resume(process.pid)
        except BaseException:
            try:
                if process is not None:
                    process.kill()
                    process.communicate(timeout=10)
            finally:
                job.close()
            raise
    else:
        options['start_new_session'] = True
        process = subprocess.Popen(command, **options)
    process._owned_scope = True
    return process


def stop_owned(process):
    """父进程退出后仍清理后代；只有本模块创建的进程可以被清理。"""
    if not hasattr(process, '_owned_scope'):
        raise ValueError('只能停止 start_owned 创建的进程')
    if process._owned_scope:
        if os.name == 'nt':
            process._owned_job.stop()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process._owned_scope = False
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
    # 根进程正常退出也可能留下重定向输出的后代。
    stop_owned(process)
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
