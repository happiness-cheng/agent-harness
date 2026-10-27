"""只创建新的隔离工作树和只读保护清单；不修改主工程源码。"""
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT/'src'))
from min_agent.evaluation.improvement import snapshot_workspace
from min_agent.evaluation.worker import source_version

def hashes(paths):
    return {p.relative_to(ROOT).as_posix():sha256(p.read_bytes()).hexdigest()
            for p in sorted(set(paths)) if p.is_file() and '__pycache__' not in p.parts}

def save(name, value):
    (OUT/name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

if __name__ == '__main__':
    if any((OUT/name).exists() for name in ('main-worktree-before.json','historical-hashes-before.json','baseline','candidate')):
        raise FileExistsError('准备证据已存在；拒绝覆盖首轮保护清单')
    if source_version(ROOT) != 'c953ec972ec5d6825d009509e5bbf203b8e3b8dcba3456f26e5be2fcf24a60f7':
        raise ValueError('本地基线源码已变化，请新立契约，不覆盖本批证据')
    files = [p for folder in ('src','tests','scripts','docs') for p in (ROOT/folder).rglob('*')]
    files += [ROOT/p for p in ('AGENTS.md','pyproject.toml','requirements.lock','requirements-dev.lock')]
    save('main-worktree-before.json', {'source_version':source_version(ROOT), 'files':hashes(files)})
    old = [p for folder in ('reports/eval-v1.1-live-20261027-001', 'reports/fix-20261027-v1/offline-suite',
                            'reports/eval-v1.1-20261027-001') for p in (ROOT/folder).rglob('*')
           if 'workspace' not in p.parts and 'source' not in p.parts]
    old += [ROOT/'reports/eval-v1/acceptance-protocol.json']
    save('historical-hashes-before.json', hashes(old))
    for variant in ('baseline','candidate'):
        workspace = snapshot_workspace(OUT/variant/'workspace')
        if variant == 'candidate':
            shutil.copy2(OUT/'implementation/research_candidate.py', workspace/'src/min_agent/research.py')
        for directory in ('tests','scripts','docs'):
            shutil.copytree(ROOT/directory, workspace/directory, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__'))
        for name in ('AGENTS.md','pyproject.toml','requirements.lock','requirements-dev.lock'):
            shutil.copy2(ROOT/name, workspace/name)
        shutil.copytree(workspace/'src', OUT/variant/'before-src', ignore=shutil.ignore_patterns('__pycache__','*.egg-info'))
        print(variant, source_version(workspace))
