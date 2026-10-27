"""保存基于实际隔离源码的可审阅差异，无生成调用。"""
import difflib
import json
from pathlib import Path
HERE=Path(__file__).resolve().parent
for variant in ('baseline','candidate'):
    patches=[]
    for file in sorted((HERE/variant/'workspace/src/min_agent').rglob('*.py')):
        relative=file.relative_to(HERE/variant/'workspace/src')
        old=HERE/variant/'before-src'/relative
        a=old.read_text(encoding='utf-8').splitlines(keepends=True) if old.exists() else []
        b=file.read_text(encoding='utf-8').splitlines(keepends=True)
        patches.extend(difflib.unified_diff(a,b,fromfile='a/src/'+relative.as_posix(),tofile='b/src/'+relative.as_posix()))
    (HERE/(variant+'-entry-v2.patch')).write_text(''.join(patches),encoding='utf-8')
a=(HERE/'baseline/workspace/src/min_agent/research.py').read_text(encoding='utf-8').splitlines(keepends=True)
b=(HERE/'candidate/workspace/src/min_agent/research.py').read_text(encoding='utf-8').splitlines(keepends=True)
(HERE/'research-candidate.patch').write_text(''.join(difflib.unified_diff(a,b,fromfile='a/src/min_agent/research.py',tofile='b/src/min_agent/research.py')),encoding='utf-8')
assert (HERE/'baseline-entry-v2.patch').read_bytes()==(HERE/'candidate-entry-v2.patch').read_bytes()
print('identical entry patches; research candidate remains a separate change')
