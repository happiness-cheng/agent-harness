"""仅报告可疑文件位置，不打印命中值；不读取工作树以外的个人资料。"""
import json
from pathlib import Path
import re
HERE=Path(__file__).resolve().parent
WORK=HERE/'publication/workspace'
manifest=json.loads((HERE/'publication-manifest.json').read_text(encoding='utf-8'))
patterns=[re.compile(rb'sk-[A-Za-z0-9_-]{24,}'),re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
          re.compile(rb'Bearer [A-Za-z0-9_-]{32,}')]
findings=[]
private_states=[]
def inspect(value,path):
    if isinstance(value,dict):
        for key,item in value.items():
            if key=='provider_state' and item:
                private_states.append(path)
            inspect(item,path)
    elif isinstance(value,list):
        for item in value:inspect(item,path)
for row in manifest['files']:
    file=WORK/row['path']
    if not file.is_file():continue
    data=file.read_bytes()
    if any(pattern.search(data) for pattern in patterns):
        findings.append(row['path'])
    if file.suffix=='.json' and row['path'].startswith('reports/'):
        try:inspect(json.loads(data),row['path'])
        except (ValueError,UnicodeError):pass
result={'files_checked':len(manifest['files']),'credential_pattern_files':sorted(set(findings)),
        'nonempty_provider_state_report_files':sorted(set(private_states)),
        'private_runtime_databases_in_manifest':[r['path'] for r in manifest['files'] if '.sqlite3' in r['path'] or r['path'].endswith('.db')],
        'memory_v01_in_manifest':[r['path'] for r in manifest['files'] if r['path'].startswith('reports/memory_v01/')],
        'scope':'仅显式发布快照；不读取正式Memory、私人文件或项目外凭据'}
(HERE/'publication-security.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False,indent=2))
