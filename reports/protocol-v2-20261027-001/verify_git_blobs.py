"""从暂存区真实blob验证源码/归档字节，不把工作树hash当作已上传字节。"""
from hashlib import sha256
import json
from pathlib import Path
import subprocess

HERE=Path(__file__).resolve().parent
WORK=HERE/'publication/workspace'
manifest=json.loads((HERE/'publication-manifest.json').read_text(encoding='utf-8'))
entries=[]
raw=subprocess.check_output(['git','ls-files','--stage','-z'],cwd=WORK)
for row in raw.split(b'\0'):
    if not row:continue
    info,name=row.split(b'\t',1)
    mode,oid,stage=info.split()
    assert stage==b'0'
    name=name.decode('utf-8')
    entries.append((name,oid))
# 与source_version的Path排序一致（Windows按路径组件/大小写规则比较）。
entries.sort(key=lambda row:Path(row[0]))
result=subprocess.run(['git','cat-file','--batch'],cwd=WORK,input=b''.join(oid+b'\n' for _,oid in entries),capture_output=True,check=True)
position=0
hashes={}
source=sha256()
for name,_ in entries:
    end=result.stdout.index(b'\n',position)
    header=result.stdout[position:end].split()
    assert header[1]==b'blob'
    size=int(header[2])
    data=result.stdout[end+1:end+1+size]
    position=end+size+2
    hashes[name]=sha256(data).hexdigest()
    if name.startswith('src/min_agent/') and Path(name).suffix in {'.py','.md','.js','.html','.css'}:
        source.update(name.encode());source.update(data)
protected=[row for row in manifest['files'] if row['path'].startswith(('src/','reports/','docs/acceptance/'))]
mismatches=[row['path'] for row in protected if hashes.get(row['path'])!=row['sha256']]
report={'index_source_version':source.hexdigest(),'expected_root_source_version':'c953ec972ec5d6825d009509e5bbf203b8e3b8dcba3456f26e5be2fcf24a60f7',
        'protected_blob_files_checked':len(protected),'byte_mismatches':mismatches,
        'reproduce_script_blob_matches':hashes.get('reports/'+HERE.name+'/reproduce.ps1')==sha256((HERE/'reproduce.ps1').read_bytes()).hexdigest(),
        'index_files':len(entries)}
(HERE/'git-blob-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
assert report['index_source_version']==report['expected_root_source_version'] and not mismatches and report['reproduce_script_blob_matches']
