"""只用合成文件与本次子进程复验已退出父进程的清理边界。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from min_agent.evaluation.processes import start_owned, stop_owned
from min_agent.web.launcher import DatabaseLease, DatabaseInUse

OUT = Path(__file__).resolve().parent / 'exited-parent-probe'
OUT.mkdir(exist_ok=False)
child = OUT / 'child.py'
child.write_text('''import os,sys,time
from pathlib import Path
from min_agent.web.launcher import DatabaseLease
out=Path(sys.argv[1])
with DatabaseLease(out/'resource', {'product':'verification-probe'}):
 (out/'ready').write_text(str(os.getpid()))
 end=time.monotonic()+25
 while time.monotonic()<end and not (out/'release').exists():
  time.sleep(.05)
(out/'done').write_text('released')
''', encoding='utf-8')
parent_code = '''import subprocess,sys,time
from pathlib import Path
out=Path(sys.argv[2])
subprocess.Popen([sys.executable,'-X','utf8',sys.argv[1],str(out)],
 stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
 creationflags=subprocess.CREATE_NO_WINDOW)
end=time.monotonic()+15
while not (out/'ready').exists() and time.monotonic()<end: time.sleep(.05)
if not (out/'ready').exists(): raise SystemExit(2)
'''
process = start_owned([sys.executable, '-X', 'utf8', '-c', parent_code, str(child), str(OUT)],
    env=dict(os.environ, PYTHONPATH=str(Path.cwd()/'src')),
    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
result = {}
try:
    process.wait(timeout=20)
    assert process.returncode == 0 and (OUT/'ready').exists()
    result['parent_exited_before_stop'] = True
    stop_owned(process)
    result['stop_owned_returned_normally'] = True
    try:
        with DatabaseLease(OUT/'resource', {'product':'verification-after-stop'}):
            result['descendant_still_holds_lock'] = False
    except DatabaseInUse:
        result['descendant_still_holds_lock'] = True
finally:
    (OUT/'release').write_text('release')
    if process.poll() is None:
        stop_owned(process)
    end=time.monotonic()+30
    while not (OUT/'done').exists() and time.monotonic()<end: time.sleep(.05)
    result['child_finished_and_released'] = (OUT/'done').exists()
    with DatabaseLease(OUT/'resource', {'product':'verification-cleanup'}):
        result['cleanup_lock_reacquired'] = True
    (OUT/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result))
