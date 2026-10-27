"""历史已保存输入与v2实际输入比较；不补造未保存的旧Schema。"""
import difflib
import json
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
rows=[]
for index,name in enumerate(('psf','warranty','api')):
    old=json.loads((ROOT/f'reports/eval-v1.1-live-20261027-001/baseline-{index}/result.json').read_text(encoding='utf-8'))['model_visible_calls'][0]
    new=json.loads((HERE/f'preflight-02/baseline/v2.normal.{name}/model-visible.json').read_text(encoding='utf-8'))[0]
    oldtext=json.dumps(old['messages'],ensure_ascii=False,indent=2).splitlines(keepends=True)
    newtext=json.dumps(new['messages'],ensure_ascii=False,indent=2).splitlines(keepends=True)
    (HERE/f'{name}-messages-v1-to-v2.patch').write_text(''.join(difflib.unified_diff(oldtext,newtext,fromfile='historical-live-v1/messages',tofile='actual-offline-v2/messages')),encoding='utf-8')
    rows.append({'task':'v2.normal.'+name,'old_user_messages':[m['content'] for m in old['messages'] if m['role']=='user'],
        'new_user_messages':[m['content'] for m in new['messages'] if m['role']=='user'],
        'old_recorded_tool_names':old['tools'],'new_tool_names':sorted(t['name'] for t in new['schemas']),
        'catalog_in_new_actual_schema':next(t['description'] for t in new['schemas'] if t['name']=='research'),
        'old_schema_evidence':'旧收据只保存工具名称，不能声称恢复完整历史Schema字节；旧失败记录和受控后端接线代码另支持目录缺失判断。'})
(HERE/'input-diff.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
print('3 actual historical message diffs saved; missing old full Schema explicitly marked')
