"""从最新远端建立发布快照，仅复制显式项目路径和白名单报告。"""
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
DEST=HERE/'publication/workspace'
BRANCH='codex/adrian-v11-source-entry-v2'

def git(*args):
    return subprocess.check_output(['git',*args],cwd=ROOT)

if DEST.exists():raise FileExistsError('发布快照已存在，拒绝覆盖')
subprocess.run(['git','worktree','add','-b',BRANCH,str(DEST),'origin/main'],cwd=ROOT,check=True,capture_output=True)
names=set(git('ls-files').decode().splitlines())
for folder in ('src','tests','scripts','docs'):
    names.update(p.relative_to(ROOT).as_posix() for p in (ROOT/folder).rglob('*') if p.is_file())
names.add('AGENTS.md')
# 这些历史目录仅发布文本/可视资产，不发布源码副本、数据库、Memory评测数据或运行缓存。
historical=('eval-v1','fix-20261027-v1','eval-v1.1-20261027-001','eval-v1.1-live-20261027-001')
for folder in historical:
    for file in (ROOT/'reports'/folder).rglob('*'):
        if not file.is_file():continue
        relative=file.relative_to(ROOT)
        if any(part in {'workspace','source','baseline-source','wheel','__pycache__'} for part in relative.parts):continue
        if file.suffix.lower() in {'.md','.json','.py','.patch','.txt','.svg','.png'}:
            names.add(relative.as_posix())
for file in HERE.rglob('*'):
    if not file.is_file():continue
    relative=file.relative_to(HERE)
    if any(part in {'workspace','before-src','publication','__pycache__'} for part in relative.parts):continue
    if file.suffix.lower() in {'.md','.json','.py','.ps1','.patch','.txt','.svg','.png'}:
        names.add(file.relative_to(ROOT).as_posix())
copied=[]
for name in sorted(names):
    file=ROOT/name
    if not file.is_file():continue  # 保留远端独有文件，不将本地缺失当成删除指令。
    if any(part in {'__pycache__','.venv','.codex','data','build','dist'} or part.endswith('.egg-info') for part in Path(name).parts):continue
    if file.suffix.lower() in {'.sqlite3','.db','.pyc','.log','.bak'} or '.sqlite3-' in file.name:continue
    if file.name.startswith('.env') or 'credentials' in file.name.lower() or file.name in {'CLAUDE.md','start.bat','task_plan.md','findings.md','progress.md'}:continue
    target=DEST/name
    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(file,target)
    copied.append({'path':name,'sha256':sha256(file.read_bytes()).hexdigest(),'bytes':file.stat().st_size})
manifest={'branch':BRANCH,'base':git('rev-parse','origin/main').decode().strip(),'files':copied,
          'excluded':'所有运行数据库、正式Memory/业务数据、凭据、隔离源码树/缓存；reports/memory_v01不读不发布',
          'note':'src/tests/scripts/docs取当前本地真实文件；实验入口与候选只在报告内作为实现和patch保存，不默认为生产已合并。'}
(HERE/'publication-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
print('prepared',len(copied),'files',sum(row['bytes'] for row in copied),'bytes',BRANCH)
