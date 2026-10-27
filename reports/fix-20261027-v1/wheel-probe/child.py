from pathlib import Path
import os,sys,time
from min_agent.web.launcher import DatabaseLease
out=Path(sys.argv[1])
with DatabaseLease(out/'resource', {'product':'wheel-child'}):
 (out/'ready').write_text(str(os.getpid()))
 time.sleep(30)
