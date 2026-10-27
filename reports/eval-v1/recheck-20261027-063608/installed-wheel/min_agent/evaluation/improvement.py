"""受控修复示例：仅隔离计算器操作绑定，不能改Verifier或执行任意生成代码。"""
import ast
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from time import monotonic

from min_agent.evaluation.contracts import digest
from min_agent.evaluation.runner import ROOT,run_task,compare_results,save_json
from min_agent.evaluation.suites import task_by_id
from min_agent.runtime import Runtime
from min_agent.evaluation.processes import run_owned

TARGET='src/min_agent/tools/calculator.py'
OPERATORS={'Add','Sub','Mult','Div'}
METHODS={'add','subtract','multiply','divide'}
CANDIDATE_SCHEMA={'type':'object','properties':{'bindings':{'type':'object','properties':{
    k:{'type':'string','enum':sorted(METHODS)} for k in sorted(OPERATORS)},'required':sorted(OPERATORS),'additionalProperties':False}},
    'required':['bindings'],'additionalProperties':False}


def render_candidate(source,candidate):
    if not isinstance(candidate,dict) or set(candidate)!={'bindings'}:raise ValueError('只能提交bindings，不接受路径或代码')
    bindings=candidate['bindings']
    if not isinstance(bindings,dict) or set(bindings)!=OPERATORS or any(not isinstance(v,str) or v not in METHODS for v in bindings.values()):
        raise ValueError('非法操作绑定')
    tree=ast.parse(source);found=0
    for node in tree.body:
        if not isinstance(node,ast.Assign) or not any(isinstance(t,ast.Name) and t.id=='_BINARY_OPERATIONS' for t in node.targets):continue
        if not isinstance(node.value,ast.Dict) or len(node.value.keys)!=4:raise ValueError('源结构不匹配')
        for key,value in zip(node.value.keys,node.value.values):
            if not isinstance(key,ast.Attribute) or not isinstance(key.value,ast.Name) or key.value.id!='ast' or key.attr not in OPERATORS:
                raise ValueError('非批准的源操作')
            if not isinstance(value,ast.Attribute) or not isinstance(value.value,ast.Name) or value.value.id!='Context':raise ValueError('非批准的源实现')
            value.attr=bindings[key.attr]
        found+=1
    if found!=1:raise ValueError('绑定表必须恰好一个')
    return ast.unparse(tree)+'\n'


def snapshot_workspace(destination,*,worktree=True):
    destination=Path(destination).resolve()
    if destination.exists():raise FileExistsError(destination)
    if worktree:
        done=subprocess.run(['git','worktree','add','--detach',str(destination),'HEAD'],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace')
        if done.returncode:raise ValueError('无法创建独立worktree，未覆盖已有目录')
    else:destination.mkdir(parents=True)
    # HEAD不包含本地最新改动；必须覆盖这份新worktree中的package快照，绝不覆盖主目录。
    shutil.copytree(ROOT/'src',destination/'src',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.egg-info'))
    return destination


def run_improvement(out,*,model=None,candidate=None,worktree=True,max_seconds=None):
    deadline=monotonic()+max_seconds if max_seconds is not None else None
    def remaining():
        if deadline is None:return None
        value=deadline-monotonic()
        if value<=0:raise TimeoutError('受控改进达到整体时间预算')
        return value
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False)
    workspace=snapshot_workspace(out/'workspace',worktree=worktree)
    standards=[task_by_id(i) for i in ('runtime.calc','runtime.subtract','runtime.multiply')]
    frozen=digest([t.model_dump(mode='json') for t in standards]);save_json(out/'frozen-tasks.json',[t.model_dump(mode='json') for t in standards])
    protected={p.relative_to(workspace).as_posix():sha256(p.read_bytes()).hexdigest() for p in (workspace/'src').rglob('*.py') if p.relative_to(workspace).as_posix()!=TARGET}
    original=(workspace/TARGET).read_text(encoding='utf-8')
    # 故意可恢复的缺陷只存在于独立worktree：将加法错误绑定为减法。
    broken=render_candidate(original,{'bindings':{'Add':'subtract','Sub':'subtract','Mult':'multiply','Div':'divide'}})
    (workspace/TARGET).write_text(broken,encoding='utf-8')
    before={t.id:run_task(t,out/('before-'+t.id),source_root=workspace,wall_timeout=remaining()) for t in standards}
    if before['runtime.calc'].verdict!='FAIL':raise ValueError('故障没有被独立判据检出，停止改进')
    prompt=('你是受控编程修复者。当前隔离计算器在算术任务中失败。只修复四则运算绑定，不能修改验证器、预算或权限。'
        '只返回JSON对象，格式由给定schema约束，不返回代码或路径。不要调用工具。\n'
        +json.dumps(dict(source=broken,failure={'task':standards[0].prompt,'actual_tools':before['runtime.calc'].observation.tools},
            response_schema=CANDIDATE_SCHEMA),ensure_ascii=False))
    (out/'repair-prompt.txt').write_text(prompt,encoding='utf-8')
    if candidate is None:
        if model is None:raise ValueError('需要明确的编程模型或已审查候选；没有伪造生成结果')
        if deadline is not None and hasattr(model,'timeout'):model.timeout=min(model.timeout,remaining())
        response=Runtime(model).run(prompt)
        if response.status!='completed':raise ValueError('编程模型未返回完整候选')
        (out/'repair-raw.txt').write_text(response.response.content,encoding='utf-8')
        candidate=json.loads(response.response.content)
    save_json(out/'candidate.json',candidate)
    fixed=render_candidate(broken,candidate)
    (workspace/TARGET).write_text(fixed,encoding='utf-8')
    assert all(sha256((workspace/name).read_bytes()).hexdigest()==value for name,value in protected.items()),'候选修改了批准范围之外的源码'
    # 候选只可能改变绑定表。独立判据来自父工作区且hash冻结，不把holdout答案给修复者。
    assert digest([t.model_dump(mode='json') for t in standards])==frozen
    after={t.id:run_task(t,out/('after-'+t.id),source_root=workspace,wall_timeout=remaining()) for t in standards}
    comparisons=[compare_results(before[t.id],after[t.id]) for t in standards]
    env=dict(os.environ,PYTHONPATH=str(workspace/'src'))
    regression=run_owned([sys.executable,'-X','utf8','-m','pytest','-q',str(ROOT/'tests/unit/test_calculator.py')],
        cwd=workspace,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=min(90,remaining()) if deadline is not None else 90)
    (out/'calculator-regression.txt').write_text(regression.stdout+regression.stderr,encoding='utf-8')
    verified=all(r.verdict=='PASS' for r in after.values()) and regression.returncode==0
    result=dict(verified=verified,standards_sha256=frozen,comparisons=comparisons,
        regression_exit=regression.returncode,workspace=str(workspace),target=TARGET,auto_merged=False,
        generator='LIVE_EXISTING_RUNTIME' if model else 'SUPPLIED_CANDIDATE_REPLAY',
        allowed_scope_verified=True,protected_source_files=len(protected),
        model_invocations=getattr(model,'request_count',0),invocation_unit=getattr(model,'invocation_unit','PROVIDER_GENERATION_ATTEMPT'),usage=getattr(model,'usage',[]),
        recommendation='REVIEW_FOR_KEEP' if verified else 'REJECT_OR_REVIEW',
        source_original_sha256=sha256(original.encode()).hexdigest(),broken_sha256=sha256(broken.encode()).hexdigest(),fixed_sha256=sha256(fixed.encode()).hexdigest())
    save_json(out/'improvement.json',result)
    return result
