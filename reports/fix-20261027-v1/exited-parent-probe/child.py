import os,sys,time
from pathlib import Path
from min_agent.web.launcher import DatabaseLease
out=Path(sys.argv[1])
with DatabaseLease(out/'resource', {'product':'verification-probe'}):
 (out/'ready').write_text(str(os.getpid()))
 end=time.monotonic()+25
 while time.monotonic()<end and not (out/'release').exists():
  time.sleep(.05)
(out/'done').write_text('released')
