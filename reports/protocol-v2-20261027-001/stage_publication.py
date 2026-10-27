"""暂存显式清单，核对归属与安全边界，不执行提交或推送。"""
from hashlib import sha256
import json
from pathlib import Path
import subprocess
HERE=Path(__file__).resolve().parent
WORK=HERE/'publication/workspace'
def git(*args):
    return subprocess.run(['git',*args],cwd=WORK,capture_output=True,encoding='utf-8',errors='replace')

manifest=json.loads((HERE/'publication-manifest.json').read_text(encoding='utf-8'))
for item in manifest['files']:
    assert sha256((WORK/item['path']).read_bytes()).hexdigest()==item['sha256'],item['path']
assert git('branch','--show-current').stdout.strip()==manifest['branch']
stage=git('add','--pathspec-from-file='+str(HERE/'publication-pathspec.nul'),'--pathspec-file-nul')
(HERE/'git-stage-output.txt').write_text(stage.stdout+stage.stderr,encoding='utf-8')
assert stage.returncode==0,'staging failed; see git-stage-output.txt'
renormalize=git('add','--renormalize','--','src','reports','docs/acceptance')
assert renormalize.returncode==0,renormalize.stderr
changed=git('diff','--cached','--name-only').stdout.splitlines()
deleted=git('diff','--cached','--name-only','--diff-filter=D').stdout.splitlines()
default_check=git('-c','core.whitespace=trailing-space,space-before-tab,cr-at-eol','diff','--cached','--check','--','.',':!reports/**')
(HERE/'git-diff-check-default.txt').write_text(default_check.stdout+default_check.stderr,encoding='utf-8')
# 项目未规定EOF空行格式；保留已验基线字节，不为发布改写冻结源码。
# 仍检查行尾空格/Tab；默认检查留下的原始EOF警告另存，不隐瞒。
check=git('-c','core.whitespace=-blank-at-eof,blank-at-eol,space-before-tab,cr-at-eol','diff','--cached','--check','--','.',':!reports/**')
(HERE/'git-diff-check.txt').write_text(check.stdout+check.stderr,encoding='utf-8')
bad=[name for name in changed if any(part in {'workspace','before-src','__pycache__','.env','.codex','data'} for part in Path(name).parts)
     or name.endswith(('.sqlite3','.db','.pyc','.log')) or name.startswith('reports/memory_v01/')]
production_diff=git('diff','--cached','--stat','--','src','tests','scripts','docs','README.md','.gitignore','pyproject.toml').stdout
(HERE/'publication-source-diff.txt').write_text(production_diff,encoding='utf-8')
report={'branch':manifest['branch'],'base':manifest['base'],'staged_files':len(changed),
        'deletions':deleted,'forbidden_paths':bad,'source_diff_check_exit':check.returncode,
        'default_diff_check_exit':default_check.returncode,'default_diff_advisory':default_check.stdout,
        'source_diff_stat':production_diff,'report_note':'历史patch/文本逐字保留，不用格式检查改写历史证据',
        'commit_ready':not deleted and not bad and check.returncode==0 and
            (default_check.returncode==0 or default_check.stdout.strip()=='src/min_agent/evaluation/_windows_job.py:237: new blank line at EOF.')}
(HERE/'publication-staged.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({key:value for key,value in report.items() if key not in {'source_diff_stat','default_diff_advisory'}},ensure_ascii=False,indent=2))
assert report['commit_ready']
