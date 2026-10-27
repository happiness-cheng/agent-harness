"""从独立安装包执行真实的已退出父进程清理，不从项目 src 导入。"""
import json
import os
from pathlib import Path
import subprocess
import sys

from min_agent.evaluation import processes
from min_agent.web.launcher import DatabaseLease

out = Path(__file__).resolve().parent/'wheel-probe'
out.mkdir(exist_ok=False)
assert 'installed-wheel' in str(processes.__file__)
child = out/'child.py'
child.write_text('''from pathlib import Path
import os,sys,time
from min_agent.web.launcher import DatabaseLease
out=Path(sys.argv[1])
with DatabaseLease(out/'resource', {'product':'wheel-child'}):
 (out/'ready').write_text(str(os.getpid()))
 time.sleep(30)
''', encoding='utf-8')
code = '''from pathlib import Path
import subprocess,sys,time
out=Path(sys.argv[1])
subprocess.Popen([sys.executable,'-X','utf8',sys.argv[2],str(out)],
 stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
end=time.monotonic()+15
while not (out/'ready').exists() and time.monotonic()<end: time.sleep(.02)
if not (out/'ready').exists():raise SystemExit(2)
print('wheel-root-done')
'''
process = processes.start_owned([sys.executable, '-X', 'utf8', '-c', code, str(out), str(child)],
    env=dict(os.environ), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
try:
    assert process.wait(timeout=20)==0
    output,_ = processes.stop_owned(process)
    assert output.decode().strip()=='wheel-root-done'
    with DatabaseLease(out/'resource', {'product':'wheel-after-stop'}):
        pass
finally:
    processes.stop_owned(process)
echo = processes.run_owned([sys.executable, '-X', 'utf8', '-c', 'import sys; print(sys.stdin.read())'],
    input='独立安装验证', capture_output=True, text=True, encoding='utf-8', timeout=10)
assert echo.returncode==0 and echo.stdout.strip()=='独立安装验证'
result={'module':processes.__file__, 'exited_parent_cleaned':True, 'lock_reacquired':True,
        'job_drained':process._owned_job.drained, 'stdin_and_output_preserved':True}
(out.parent/'wheel-smoke.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result))
