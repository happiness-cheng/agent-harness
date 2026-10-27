"""保留旧报告，核对本次进程退出修复的最小增量。"""
from hashlib import sha256
from pathlib import Path
import difflib
import json
import re
import shutil
import subprocess
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
sys.path.insert(0, str(ROOT/'src'))
from min_agent.evaluation.worker import source_version

MODIFIED = {
    'src/min_agent/evaluation/runner.py',
    'src/min_agent/evaluation/codex_adapter.py',
    'src/min_agent/evaluation/improvement.py',
    'src/min_agent/evaluation/night_executor.py',
    'tests/integration/test_evaluation_v1.py',
    'tests/unit/test_codex_night_adapter.py',
}
ADDED = {'src/min_agent/evaluation/processes.py', 'tests/unit/test_evaluation_processes.py'}


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def main():
    baseline = json.loads((ROOT/'reports/adrian-integration/recheck-20261027-063256/source-before.json').read_text(encoding='utf-8'))
    changed = {name for name, old in baseline.items() if digest(ROOT/name) != old}
    assert changed == MODIFIED, f'意外变更：{changed ^ MODIFIED}'
    originals = {}
    for name in MODIFIED:
        candidate = ROOT/'reports/eval-v1'/('delivery-source' if name.startswith('src/') else 'review-after')/name
        assert digest(candidate) == baseline[name], f'旧源码基线不匹配：{name}'
        originals[name] = candidate.read_bytes()
    patch = []
    for name in sorted(MODIFIED | ADDED):
        current = (ROOT/name).read_bytes()
        if name in originals:
            previous = OUT/'review-before'/name
            previous.parent.mkdir(parents=True, exist_ok=True)
            previous.write_bytes(originals[name])
        target = OUT/'review-after'/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(current)
        before = originals.get(name, b'').decode('utf-8').replace('\r\n', '\n').splitlines(keepends=True)
        after = current.decode('utf-8').replace('\r\n', '\n').splitlines(keepends=True)
        patch.extend(difflib.unified_diff(before, after, fromfile='a/'+name if before else '/dev/null', tofile='b/'+name))
    (OUT/'fix.patch').write_text(''.join(patch), encoding='utf-8')
    command = ['git', '-c', 'core.autocrlf=false', '-c',
               'core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol',
               'diff', '--no-index', '--check', str(OUT/'review-before'), str(OUT/'review-after')]
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace')
    assert result.returncode in {0, 1} and not result.stdout.strip() and not result.stderr.strip(), result.stdout+result.stderr
    (OUT/'diff-check.txt').write_text(f'EXIT: {result.returncode}; no whitespace diagnostics\n', encoding='utf-8')
    previous_hashes = json.loads((ROOT/'reports/eval-v1/data-hashes.json').read_text(encoding='utf-8'))
    altered_artifacts = [name for name, old in previous_hashes.items() if name not in MODIFIED and digest(ROOT/name) != old]
    assert not altered_artifacts, f'旧实验或判据改变：{altered_artifacts}'
    version = source_version(ROOT)
    snapshot = OUT/'source'
    if not snapshot.exists():
        shutil.copytree(ROOT/'src', snapshot/'src', ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.egg-info'))
    assert source_version(snapshot) == version
    (OUT/'source-version.txt').write_text(version, encoding='utf-8')
    full = (OUT/'full-tests.txt').read_text(encoding='utf-8')
    failures = re.findall(r'^FAILED (\S+)', full, flags=re.M)
    expected = 'tests/integration/test_memory_tools.py::test_explicit_source_read_and_structured_recall_are_not_silently_head_truncated'
    assert set(failures) <= {expected}, failures
    review = dict(source_sha256=version, modified=sorted(MODIFIED), added=sorted(ADDED),
        git_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        protected_source_files=len(baseline)-len(MODIFIED), protected_unchanged=True,
        prior_artifacts_unchanged=True, evaluation_standards_unchanged=True,
        full_test_failures=failures,
        full_test_summary=next(line for line in reversed(full.splitlines()) if 'passed' in line and 'deselected' in line),
        whitespace='PASS', type_checker='NOT_RUN: not configured', linter='NOT_RUN: not configured',
        self_review='PASS: owned PID tree only, original UNKNOWN and budget boundaries retained; no independent agent claimed',
        external_model_calls=0, production_database_migrated=False, remote_push=False,
        live_model_retest='NOT_RUN: existing raw generations retained; process fix verified offline',
        code_quality_rule='code-quality.md unavailable; applied AGENTS and verification checklist equivalents')
    (OUT/'fix-review.json').write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding='utf-8')
    selected = [ROOT/name for name in MODIFIED | ADDED] + list(OUT.glob('*.txt')) + list(OUT.glob('*.json')) + list(OUT.glob('*.py'))
    selected += [OUT/'fix.patch'] + list(OUT.glob('*.md')) + list((OUT/'process-probe').glob('*.json'))
    selected += list((OUT/'wheel').glob('*.whl'))
    for group in ('offline-suite', 'candidate-replay'):
        selected += [p for p in (OUT/group).rglob('*') if p.is_file() and p.suffix in {'.json','.txt','.md'}
                     and not any(part in {'workspace','__pycache__','.pytest_cache'} for part in p.relative_to(OUT/group).parts)]
    selected = [p for p in selected if p.name != 'artifact-hashes.json']
    secret = re.compile(r'\bsk-[A-Za-z0-9_-]{24,}\b')
    assert not any(secret.search(p.read_text(encoding='utf-8', errors='replace')) for p in selected if p.suffix != '.whl'), '疑似密钥字面量，拒绝交付'
    (OUT/'artifact-hashes.json').write_text(json.dumps({p.relative_to(ROOT).as_posix():digest(p) for p in selected}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(review, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
