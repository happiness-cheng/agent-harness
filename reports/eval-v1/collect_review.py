"""对本轮增量、冻结证据和保护文件作只读审计；只写本目录的派生审查材料。"""
from hashlib import sha256
import difflib
import json
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'src'))
from min_agent.evaluation.worker import source_version

MODIFIED = {
    'README.md', 'docs/ADRIAN.md', 'src/min_agent/application.py',
    'src/min_agent/context.py', 'src/min_agent/model_context.py',
    'src/min_agent/llm/client.py', 'src/min_agent/llm/deepseek.py',
    'src/min_agent/web/__main__.py',
}
ADDED = [
    'tests/integration/test_evaluation_v1.py',
    'tests/unit/test_evaluation_verifier.py', 'tests/unit/test_adaptive_output.py',
    'tests/unit/test_night_mode.py', 'tests/unit/test_codex_night_adapter.py',
    'tests/unit/test_evaluation_live_boundary.py',
    'scripts/draw_adrian_evaluation.py', 'scripts/run_adrian_frozen_eval.py',
    'scripts/evaluate-adrian.cmd', 'scripts/night-adrian.cmd',
    'docs/EVALUATION_V1.md', 'docs/NIGHT_MODE.md',
]
ADDED += [p.relative_to(ROOT).as_posix() for p in sorted((ROOT/'src/min_agent/evaluation').glob('*.py'))]
ADDED += [p.relative_to(ROOT).as_posix() for p in sorted((ROOT/'docs/examples').glob('*')) if p.is_file()]
ADDED += [p.relative_to(ROOT).as_posix() for p in sorted((ROOT/'docs/diagrams/evaluation-v1').glob('*')) if p.is_file()]


def hash_file(path):
    return sha256(path.read_bytes()).hexdigest()


def save(name, value):
    (OUT/name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    before = json.loads((OUT/'before.json').read_text(encoding='utf-8'))
    changed = [name for name, old in before.items() if not (ROOT/name).is_file() or hash_file(ROOT/name) != old]
    unexpected = sorted(set(changed)-MODIFIED)
    missing = [p for p in MODIFIED | set(ADDED) if not (ROOT/p).is_file()]
    assert not unexpected, f'保护文件意外改变：{unexpected}'
    assert not missing, f'交付文件缺失：{missing}'
    assert MODIFIED <= set(before), '缺少本轮修改前的真实基线'
    assert not set(ADDED) & set(before), '新增清单混入既有文件'
    archived = {}
    with ZipFile(OUT/'before-code.zip') as archive:
        for name in before:
            content = archive.read(name)
            assert sha256(content).hexdigest() == before[name], f'基线归档不匹配：{name}'
            archived[name] = content
    patch = []
    before_dir, after_dir = OUT/'review-before', OUT/'review-after'
    for name in sorted(MODIFIED | set(ADDED)):
        current = (ROOT/name).read_bytes()
        if name in archived:
            old_path = before_dir/name
            old_path.parent.mkdir(parents=True, exist_ok=True)
            old_path.write_bytes(archived[name])
        new_path = after_dir/name
        new_path.parent.mkdir(parents=True, exist_ok=True)
        new_path.write_bytes(current)
        if Path(name).suffix in {'.png'}:
            continue
        previous = archived.get(name, b'').decode('utf-8-sig').replace('\r\n', '\n').splitlines(keepends=True)
        present = current.decode('utf-8-sig').replace('\r\n', '\n').splitlines(keepends=True)
        patch.extend(difflib.unified_diff(previous, present, fromfile='a/'+name if previous else '/dev/null', tofile='b/'+name))
    (OUT/'implementation.patch').write_text(''.join(patch), encoding='utf-8')
    check = subprocess.run(['git','-c','core.autocrlf=false','-c','core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol','diff','--no-index','--check',str(before_dir),str(after_dir)], capture_output=True, text=True, encoding='utf-8', errors='replace')
    (OUT/'diff-check.txt').write_text(f'EXIT: {check.returncode}\n'+check.stdout+check.stderr, encoding='utf-8')
    # --no-index在两侧存在内容差异时返回1；--check的空白诊断必须为空。
    assert check.returncode in {0, 1} and not check.stdout.strip() and not check.stderr.strip(), '本轮增量有空白错误，见diff-check.txt'
    version = source_version(ROOT)
    frozen = (OUT/'final-source-version.txt').read_text(encoding='utf-8').strip()
    assert frozen == source_version(OUT/'final-source'), '离线验收冻结源码被改动'
    assert version == source_version(OUT/'delivery-source') == (OUT/'delivery-source-version.txt').read_text(encoding='utf-8').strip(), '交付源码快照不一致'
    delta = []
    for path in sorted((ROOT/'src/min_agent').rglob('*')):
        if path.is_file() and path.suffix in {'.py','.md','.js','.html','.css'}:
            previous = (OUT/'final-source'/path.relative_to(ROOT)).read_bytes()
            current = path.read_bytes()
            if previous != current:
                delta.append(path.relative_to(ROOT).as_posix())
                assert path.name == 'journal.py' and previous.rstrip(b'\r\n')+b'\n' == current, '验收后出现非格式变更'
    selected = [OUT/'acceptance-protocol.json', OUT/'live-protocol.json', OUT/'frozen-manifest.json', OUT/'before.json', OUT/'before-code.zip']
    selected += [ROOT/p for p in sorted(MODIFIED | set(ADDED))]
    selected += [p for p in OUT.glob('*.txt') if p.is_file()]
    selected += [p for p in OUT.glob('*.md') if p.is_file()]
    selected += [p for p in OUT.glob('*.py') if p.is_file()]
    selected += [OUT/'implementation.patch']
    selected += list((OUT/'wheel-delivery').glob('*.whl'))
    for group in ('offline-acceptance','live-acceptance','night-demo','codex-improvement'):
        selected += [p for p in (OUT/group).rglob('*') if p.is_file() and p.suffix in {'.json','.md','.txt','.jsonl'} and not any(part in {'workspace','__pycache__','.pytest_cache'} for part in p.relative_to(OUT/group).parts)]
    selected = sorted(set(selected))
    # 不加载认证文件；检查常见密钥字面量形态，命中只报告文件名，不输出内容。
    import re
    secret_shape = re.compile(r'\b(?:sk-[A-Za-z0-9_-]{24,}|Bearer\s+[A-Za-z0-9_-]{24,})\b')
    hits = [p.relative_to(ROOT).as_posix() for p in selected if p.suffix not in {'.zip','.whl','.png'} and secret_shape.search(p.read_text(encoding='utf-8', errors='replace'))]
    assert not hits, f'疑似密钥字面量，需核对文件：{hits}'
    save('data-hashes.json', {p.relative_to(ROOT).as_posix():hash_file(p) for p in selected})
    save('changes.json', dict(modified=sorted(changed), added=sorted(set(ADDED)), binary_patch_omitted=['docs/diagrams/evaluation-v1/architecture.png'], baseline_files=len(before), protected_unchanged=len(before)-len(changed)))
    checks = dict(
        baseline_archive='PASS', protected_files='PASS', source_snapshot='PASS',
        whitespace_diff='PASS', secret_literal_pattern='PASS',
        secret_scan_scope='incremental source/docs and selected synthetic receipts; pattern scan is not comprehensive DLP',
        baseline_files=len(before), protected_unchanged=len(before)-len(changed),
        delivery_source_sha256=version, offline_acceptance_source_sha256=frozen,
        acceptance_to_delivery_changes=delta, acceptance_to_delivery_logic_unchanged=True,
        head=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        head_alone_reproduces=False, private_data_imported=False, database_migration=False,
        remote_push=False, candidate_auto_merge=False, real_ten_hour_run='NOT_RUN',
        type_checker='NOT_RUN: no configured checker', linter='NOT_RUN: no configured linter',
        complete_regression='1 failed, 1242 passed, 3 deselected; existing long-source-read failure',
    )
    save('review-checks.json', checks)
    print(json.dumps(checks, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
