from pathlib import Path
import os,sys,time
from min_agent.web.launcher import DatabaseLease
out=Path(sys.argv[1])
with DatabaseLease(out/'resource', {'product':'owned-orphan-test'}):
 (out/'ready').write_text(str(os.getpid()),encoding='utf-8')
 end=time.monotonic()+45
 while time.monotonic()<end and not (out/'release').exists(): time.sleep(.05)
