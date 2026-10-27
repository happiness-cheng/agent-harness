"""核验fetch后的GitHub tree与本地实际文件，包含本地尚未跟踪的文件。"""
from hashlib import sha256
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).resolve().parent
def git(*args):
    return subprocess.check_output(['git',*args],cwd=ROOT)

remote=git('rev-parse','origin/main').decode().strip()
head=git('rev-parse','HEAD').decode().strip()
paths=git('ls-tree','-r','--name-only','origin/main').decode().splitlines()
records=[]
for name in paths:
    local=ROOT/name
    theirs=git('show','origin/main:'+name)
    ours=local.read_bytes() if local.is_file() else None
    # Git的工作树CRLF与仓库LF按文本归一化，另外保留原始字节hash。
    same=ours is not None and ours.replace(b'\r\n',b'\n')==theirs.replace(b'\r\n',b'\n')
    records.append({'path':name,'same_text':same,'local_exists':ours is not None,
                    'github_sha256':sha256(theirs).hexdigest(),'local_sha256':sha256(ours).hexdigest() if ours else None})
local_paths=git('ls-files','--others','--exclude-standard').decode().splitlines()
report={'remote':'https://github.com/happiness-cheng/agent-harness.git','branch':'main','local_HEAD':head,
        'fetched_origin_main':remote,'remote_only_commits':git('log','--oneline','HEAD..origin/main').decode().splitlines(),
        'files':records,'local_new_non_report_files':[p for p in local_paths if not p.startswith('reports/')],
        'remote_only_files_to_preserve':[r['path'] for r in records if not r['local_exists']],
        'note':'git diff不统计尚未跟踪的本地新增；此核验逐个读取真实本地文件后再比较。'}
(OUT/'github-vs-local-before.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k not in ('files','local_new_non_report_files')},ensure_ascii=False,indent=2))
print('remote tracked files',len(records),'same',sum(r['same_text'] for r in records),'changed',sum(r['local_exists'] and not r['same_text'] for r in records))
