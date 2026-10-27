from pathlib import Path
import sys,time
from min_agent.web.launcher import DatabaseLease
out=Path(sys.argv[1])
with DatabaseLease(out/'resource', {'product':'late-child'}):
 (out/'child-ready').write_text('locked')
 time.sleep(30)
