"""新目录/隔离worker执行器；父进程持有冻结标准，结果比较不改标准。"""
from collections import Counter
import json
import os
from pathlib import Path
import platform
import subprocess
import sqlite3
import sys
from time import monotonic
from uuid import uuid4

from min_agent.evaluation.contracts import Observation,TaskSpec,digest
from min_agent.evaluation.verifier import verify
from min_agent.evaluation.worker import source_version,tool_records
from min_agent.evaluation.processes import start_owned,stop_owned
from min_agent.models import Message

ROOT=Path(__file__).resolve().parents[3]


def save_json(path,value):
    if hasattr(value,'model_dump'):value=value.model_dump(mode='json')
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def cost_range(usage):
    if not usage:return dict(status='UNKNOWN',reason='Provider没有返回可用usage；不是零费用',invoice_usd=None)
    inputs=sum(u.get('input_tokens',0) for u in usage);outputs=sum(u.get('output_tokens',0) for u in usage)
    cached=sum(u.get('cached_input_tokens',0) for u in usage)
    return dict(status='ESTIMATED_NOT_INVOICE',input_tokens=inputs,output_tokens=outputs,cached_input_tokens=cached,
        usd_min=((inputs-cached)*.15+cached*.003+outputs*.6)/1e6,
        usd_max=((inputs-cached)*.3+cached*.006+outputs*1.2)/1e6,
        source='https://api-docs.deepseek.com/quick_start/pricing/',pricing_date='2026-10-26',invoice_usd=None)


def run_task(task,out,*,source_root=ROOT,live=False,credentials=None,proxy=None,cost_limit=None,wall_timeout=None):
    task=TaskSpec.model_validate(task);out=Path(out).resolve();source_root=Path(source_root).resolve()
    if task.data_classification=='private':raise PermissionError('V1默认不保存或传送私人原文，仅public/synthetic')
    if live and (not task.live_eligible or credentials is None):raise PermissionError('需要批准的live任务及明确项目外凭据')
    out.mkdir(parents=True,exist_ok=False)
    save_json(out/'task.json',task)
    save_json(out/'environment.json',dict(python=sys.version,platform=platform.platform(),
        source_version=source_version(source_root),spec_sha256=digest(task),data_classification=task.data_classification,
        backend='LIVE' if live else 'SCRIPTED_ACTUAL_HARNESS'))
    owner=str(uuid4())
    command=[sys.executable,'-X','utf8','-m','min_agent.evaluation.worker','--spec',str(out/'task.json'),'--out',str(out),'--owner',owner]
    if live:
        command+=['--live','--credentials',str(Path(credentials).resolve())]
        if proxy:command+=['--proxy',proxy]
        if cost_limit is not None:command+=['--cost-limit',str(cost_limit)]
    options=dict(cwd=source_root,env=dict(os.environ,PYTHONPATH=str(source_root/'src')),stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    if os.name=='nt':options['creationflags']=subprocess.CREATE_NO_WINDOW
    start=monotonic();process=None;transport='ok';output=b'';process_failure=None
    try:process=start_owned(command,**options)
    except Exception as error:
        transport='worker_error';process_failure={'stage':'start','error_type':type(error).__name__}
    if process is not None:
        try:output,_=process.communicate(timeout=min(task.timeout_seconds,wall_timeout) if wall_timeout is not None else task.timeout_seconds)
        except subprocess.TimeoutExpired as error:
            output=error.output or b'';transport='timeout'
        except BaseException:
            stop_owned(process)
            raise
        try:
            # 正常退出也清理后代；管道 EOF 不能证明本次进程树已退出。
            cleaned,_=stop_owned(process)
            if transport=='timeout':output=cleaned
        except Exception as error:
            transport='worker_error';process_failure={'stage':'cleanup','error_type':type(error).__name__}
    (out/'worker.log').write_bytes(output)
    observation_file=out/'observation.json'
    if transport=='ok' and process is not None and process.returncode==0 and observation_file.is_file():
        try:observation=Observation.model_validate_json(observation_file.read_text(encoding='utf-8'))
        except ValueError:transport='worker_error'
    else:transport='timeout' if transport=='timeout' else 'worker_error'
    if transport!='ok':
        observation=Observation(run_id=str(uuid4()),task_id=task.id,version_id=source_version(source_root),
            transport_status=transport,elapsed_seconds=monotonic()-start,
            extra={'worker_exit':process.returncode if process is not None else None,
                   'data_classification':task.data_classification,
                   **({'process_failure':process_failure} if process_failure else {})})
        save_json(observation_file,observation)
    else:
        # 父进程重新只读打开真实DB，不能只信worker摘要或模型返回的“已创建”。
        try:
            prepared=json.loads((out/'prepared.json').read_text(encoding='utf-8'))
            with sqlite3.connect((out/'workspace.sqlite3').as_uri()+'?mode=ro',uri=True) as con:
                row=con.execute('SELECT owner_id,snapshot_json FROM sessions WHERE session_id=?',(prepared['session_id'],)).fetchone()
                snapshot=json.loads(row[1])
                todos=[{'item':json.loads(row[0])} for row in con.execute('SELECT item_json FROM todos WHERE session_id=? ORDER BY position',(prepared['session_id'],)) if not json.loads(row[0]).get('deleted')]
            messages=[Message.model_validate(m) for m in snapshot['messages']]
            active=snapshot['budgets'].get(snapshot['active_budget_id'])
            budget={'id':snapshot['active_budget_id'],'limits':active['state']['limits'],'usage':active['state']['usage']} if active else {}
            observation=observation.model_copy(update=dict(todos=todos,initial_todos=prepared['initial_todos'],
                execution_status=snapshot['task']['status'] if snapshot['task'] else 'unknown',
                answer=next((m.content for m in reversed(messages) if m.role=='assistant' and not m.tool_calls),''),
                tools=tool_records(messages),budget=budget,artifacts=snapshot['extensions'].get('artifacts',{}),
                memory_delivery=snapshot['extensions'].get('long_memory_delivery',{}),citation=snapshot['extensions'].get('long_memory_citation',{}),
                extra=dict(observation.extra,database_reopened_read_only=True,scope_mismatch=row[0]!=owner)))
            save_json(observation_file,observation)
        except (ValueError,KeyError,TypeError,sqlite3.Error,OSError):
            observation=observation.model_copy(update={'transport_status':'worker_error','execution_status':'unknown'})
    result=verify(task,observation)
    save_json(out/'result.json',result);save_json(out/'cost.json',cost_range(observation.usage))
    lines=[f'# {task.id}',f'\n执行状态：{observation.execution_status}；验证：{result.verdict}；安全：{result.safety}；诚实性：{result.honesty}',
        f'\n契约SHA256：{result.spec_sha256}',f'\n源码SHA256：{observation.version_id}',
        '\n| 检查 | 层 | 判定 | 依据 |','| --- | --- | --- | --- |']
    lines += [f'| {c.id} | {c.layer} | {c.verdict} | {c.reason.replace(chr(10)," ").replace("|","/")} |' for c in result.checks]
    lines += ['\n模型最终回答（合成任务）：','```text',observation.answer,'```',
        '\n原始工具返回、来源与状态见observation.json；原生推理和密钥不导出。',
        '\n局限：PASS仅覆盖本任务声明的机械判据，不证明所有自然语言断言受证据支持。']
    (out/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    return result


def compare_results(before,after):
    if before.task_id!=after.task_id or before.spec_sha256!=after.spec_sha256:
        raise ValueError('任务或评判契约不同，不能作为同标准版本提升')
    return dict(comparable=True,task_id=before.task_id,spec_sha256=before.spec_sha256,
        before=dict(version=before.observation.version_id,verdict=before.verdict,safety=before.safety,requests=before.observation.model_requests,seconds=before.observation.elapsed_seconds),
        after=dict(version=after.observation.version_id,verdict=after.verdict,safety=after.safety,requests=after.observation.model_requests,seconds=after.observation.elapsed_seconds),
        changed_checks=[dict(id=a.id,before=b.verdict,after=a.verdict) for b,a in zip(before.checks,after.checks) if b.id==a.id and b.verdict!=a.verdict],
        recommendation='REVIEW_FOR_KEEP' if before.verdict!='PASS' and after.verdict=='PASS' and after.safety=='PASS' else 'REVIEW_REQUIRED',auto_merge=False)


def run_suite(tasks,out,*,live=False,request_ceiling=16,**kwargs):
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    tasks=list(tasks)
    if len({t.id for t in tasks})!=len(tasks):raise ValueError('任务ID重复')
    reservation=sum(min(t.max_model_calls,t.max_rounds) for t in tasks)
    if live and reservation>request_ceiling:raise ValueError('整批最坏请求数超过本轮上限，未开始请求')
    save_json(out/'frozen-suite.json',[t.model_dump(mode='json') for t in tasks]);save_json(out/'manifest.json',dict(
        suite_sha256=digest([t.model_dump(mode='json') for t in tasks]),reserved_generation_requests=reservation if live else 0,request_ceiling=request_ceiling,live=live))
    results=[];stopped=None
    for task in tasks:
        result=run_task(task,out/task.id,live=live,**kwargs);results.append(result)
        print(f'{task.id}: execution={result.observation.execution_status} verification={result.verdict}',flush=True)
        if live and (result.observation.extra.get('provider_errors') or result.observation.transport_status!='ok'):
            stopped='provider_error_or_timeout_no_auto_retry';break
    summary=dict(results={r.task_id:r.verdict for r in results},counts=dict(Counter(r.verdict for r in results)),
        expected_outcomes_match=all(r.verdict==t.expected_verdict for r,t in zip(results,tasks)) and len(results)==len(tasks),
        model_invocations=sum(r.observation.model_requests for r in results),
        generation_requests=sum(r.observation.model_requests for r in results) if live else 0,stopped=stopped,not_run=[t.id for t in tasks[len(results):]],
        usage_cost=cost_range([u for r in results for u in r.observation.usage]) if live else dict(status='OFFLINE_ZERO_EXTERNAL_CALLS',invoice_usd=0),
        note='SCRIPTED结果验证工程机制，不是模型自主能力成绩；含故意失败/UNKNOWN案例，不能用全PASS比例衡量。' if not live else '真实模型，无外部Judge；语义未审查项保留UNKNOWN。')
    save_json(out/'summary.json',summary)
    return results,summary
