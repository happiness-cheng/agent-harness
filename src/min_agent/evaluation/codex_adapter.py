"""复用本机codex exec，只读结构化候选；不提供通用Shell或任意补丁执行器。"""
import json
import os
from pathlib import Path
import shutil
import subprocess
from time import monotonic
from min_agent.models import ModelResponse
from min_agent.evaluation.improvement import CANDIDATE_SCHEMA
from min_agent.evaluation.runner import save_json
from min_agent.evaluation.processes import run_owned


class CodexCandidateModel:
    invocation_unit='CODEX_EXEC_PROCESS; internal API calls/quota unobservable'
    def __init__(self,out,workspace,*,max_calls=1,timeout=180,executable=None):
        self.out=Path(out).resolve();self.workspace=Path(workspace).resolve()
        self.executable=executable or shutil.which('codex')
        if not self.executable:raise ValueError('未安装Codex CLI；不假装可自动调用')
        if type(max_calls) is not int or not 1<=max_calls<=4 or not 0<timeout<=3600:raise ValueError('Codex调用必须有有限次数与时间上限')
        self.max_calls,self.timeout=max_calls,timeout;self.request_count=0;self.usage=[];self.execution_failed=False
    def generate(self,messages,*,tools=None):
        if tools:raise ValueError('受控Codex候选适配器不执行任意工具')
        if self.request_count>=self.max_calls:raise PermissionError('Codex调用槽位已用尽')
        self.request_count+=1
        directory=self.out/('call-'+str(self.request_count));directory.mkdir(parents=True,exist_ok=False)
        schema=directory/'schema.json';answer=directory/'candidate.json';save_json(schema,CANDIDATE_SCHEMA)
        prompt='不要调用工具或读取其他文件，仅根据下面提供的非敏感源码和失败信息生成JSON候选。\n'+'\n'.join(m.content for m in messages)
        command=[self.executable,'--ask-for-approval','never','exec','--ignore-user-config','--sandbox','read-only',
            '--ephemeral','--skip-git-repo-check','--model','gpt-6.1-sol','--output-schema',str(schema),
            '--output-last-message',str(answer),'--json','--color','never','-C',str(self.workspace),'-']
        allowed={'PATH','SystemRoot','SYSTEMROOT','WINDIR','TEMP','TMP','USERPROFILE','APPDATA','LOCALAPPDATA','HOMEDRIVE','HOMEPATH',
            'ComSpec','COMSPEC','PATHEXT','PROGRAMFILES','ProgramFiles','PROGRAMFILES(X86)','SystemDrive','SYSTEMDRIVE','CODEX_HOME'}
        environment={k:v for k,v in os.environ.items() if k in allowed}
        options=dict(cwd=self.workspace,env=environment,input=prompt,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=self.timeout)
        if os.name=='nt':options['creationflags']=subprocess.CREATE_NO_WINDOW
        start=monotonic()
        try:result=run_owned(command,**options)
        except Exception as error:
            self.execution_failed=True
            status='timeout' if isinstance(error,subprocess.TimeoutExpired) else 'execution_error'
            save_json(directory/'receipt.json',dict(status=status,error_type=type(error).__name__,quota_remaining='UNOBSERVABLE',elapsed_seconds=monotonic()-start));raise
        events=[]
        for line in result.stdout.splitlines():
            try:record=json.loads(line)
            except ValueError:continue
            events.append({'type':record.get('type')})
            if record.get('type')=='turn.completed' and isinstance(record.get('usage'),dict):
                self.usage.append({k:v for k,v in record['usage'].items() if k in {'input_tokens','cached_input_tokens','output_tokens'} and type(v) is int})
        # 不保存原始CLI stdout/stderr，避免工具输出、认证诊断或推理文本泄漏。
        save_json(directory/'receipt.json',dict(exit_code=result.returncode,elapsed_seconds=monotonic()-start,events=events,
            usage=self.usage,quota_remaining='UNOBSERVABLE',sandbox='read-only',approval='never',model='gpt-6.1-sol'))
        if result.returncode or not answer.is_file():
            self.execution_failed=True
            raise ValueError('Codex非交互调用失败，未回退或继续消耗额度')
        content=answer.read_text(encoding='utf-8')
        if len(content.encode())>16384:raise ValueError('候选数据超出批准大小，不执行')
        return ModelResponse(finish_reason='stop',content=content)
