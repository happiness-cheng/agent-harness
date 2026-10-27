from pathlib import Path
import os,sys,time
from min_agent.web.launcher import DatabaseLease
with DatabaseLease(sys.argv[1], {'product':'owned-process-test'}):
 Path(sys.argv[2]).write_text(str(os.getpid()),encoding='utf-8')
 time.sleep(30)
