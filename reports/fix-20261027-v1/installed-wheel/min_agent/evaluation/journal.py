"""小型原子JSON文档；复用现有OS文件租约，不创建数据库Schema。"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
from uuid import uuid4
from min_agent.web.launcher import DatabaseLease


class JsonDocument:
    def __init__(self,path):self.path=Path(path).resolve()
    @contextmanager
    def transaction(self):
        with DatabaseLease(self.path,{'product':'adrian-evaluation-document','pid':os.getpid()}):yield
    def read(self):
        if self.path.stat().st_size>8*1024*1024:raise ValueError('文档过大，拒绝静默丢失记录')
        return json.loads(self.path.read_text(encoding='utf-8'))
    def write(self,value):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        if self.path.is_symlink():raise PermissionError('状态文档不能是符号链接')
        pending=self.path.with_name(self.path.name+'.pending-'+uuid4().hex)
        data=json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)
        if len(data.encode())>8*1024*1024:raise ValueError('状态文档超过容量')
        with pending.open('x',encoding='utf-8') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(pending,self.path)
