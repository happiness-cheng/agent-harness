"""仅更新本次报告快照，保留发布README/.gitignore及既有历史内容。"""
from hashlib import sha256
import json
from pathlib import Path
import shutil

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
WORK=HERE/'publication/workspace'
for file in HERE.rglob('*'):
    if not file.is_file():continue
    relative=file.relative_to(HERE)
    if any(part in {'workspace','before-src','publication','__pycache__'} for part in relative.parts):continue
    if file.suffix.lower() not in {'.md','.json','.py','.ps1','.patch','.txt','.svg','.png'}:continue
    if file.name in {'publication-manifest.json','github-push-receipt.json'}:continue
    target=WORK/'reports'/HERE.name/relative
    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(file,target)
manifest=json.loads((HERE/'publication-manifest.json').read_text(encoding='utf-8'))
names={row['path'] for row in manifest['files']}
names.update(file.relative_to(WORK).as_posix() for file in (WORK/'reports'/HERE.name).rglob('*') if file.is_file())
names.update({'README.md','.gitignore','.gitattributes'})
manifest['files']=[{'path':name,'sha256':sha256((WORK/name).read_bytes()).hexdigest(),'bytes':(WORK/name).stat().st_size}
                   for name in sorted(names) if (WORK/name).is_file() and name!='reports/'+HERE.name+'/publication-manifest.json']
manifest['note']=manifest['note'].split(' README/.gitignore')[0]
manifest['publication_note']='README/.gitignore/.gitattributes的发布说明有意新增，代码与主工程一致；reproduce.ps1包含UTF-8 BOM且在清单中；源码与历史证据保持原始字节。'
(HERE/'publication-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
shutil.copy2(HERE/'publication-manifest.json',WORK/'reports'/HERE.name/'publication-manifest.json')
assert (WORK/'reports'/HERE.name/'reproduce.ps1').is_file()
# 暂存使用显式的已审阅路径，清单本身也单独列入。
paths=[row['path'] for row in manifest['files']]+['reports/'+HERE.name+'/publication-manifest.json']
(HERE/'publication-pathspec.nul').write_bytes(b''.join(name.encode()+b'\0' for name in paths))
print('refreshed',len(paths),'explicit paths')
