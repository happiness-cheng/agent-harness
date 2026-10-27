"""本机服务身份、数据库进程锁与就绪探测；不读取密钥、不发模型请求。"""
import json
import os
from pathlib import Path
import socket
from hashlib import sha256
from time import monotonic, sleep
from uuid import uuid4
import webbrowser

import httpx


class LaunchError(RuntimeError):
    pass


class DatabaseInUse(LaunchError):
    pass


def identity_for(database, port, *, demo, memory_mode, workspace=None):
    def fingerprint(path):
        return sha256(os.path.normcase(str(Path(path).resolve())).encode()).hexdigest()
    root = workspace or Path(__file__).resolve().parents[3]
    return dict(product='adrian-workbench', protocol=1, workspace_id=fingerprint(root),
        database_id=fingerprint(database), instance_id=str(uuid4()), pid=os.getpid(), port=port,
        mode='demo' if demo else 'live', memory_mode=memory_mode)


class DatabaseLease:
    def __init__(self, database, identity):
        self.path=Path(str(Path(database).resolve())+'.writer.lock')
        self.identity=identity;self.file=None

    def __enter__(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        stream=self.path.open('a+b')
        stream.seek(0,2)
        if stream.tell()==0:stream.write(b'\n');stream.flush()
        stream.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            stream.close()
            raise DatabaseInUse('当前数据库已有写库进程，不能重复启动') from None
        self.file=stream
        try:
            stream.seek(1);stream.truncate(1)
            stream.write(json.dumps(self.identity,separators=(',',':')).encode());stream.flush()
        except BaseException:
            self.__exit__(None,None,None)
            raise
        return self

    def read_metadata(self):
        with self.path.open('rb') as stream:
            stream.seek(1)
            return json.loads(stream.read())

    def __exit__(self,*args):
        if self.file is None:return
        try:
            self.file.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(),fcntl.LOCK_UN)
        finally:
            self.file.close();self.file=None


def port_available(port):
    with socket.socket() as stream:
        try:stream.bind(('127.0.0.1',port))
        except OSError:return False
    return True


def matches_identity(actual, expected):
    keys=('product','protocol','workspace_id','database_id','instance_id','pid','port','mode','memory_mode')
    return isinstance(actual,dict) and actual.get('ready') is True and all(actual.get(k)==expected.get(k) for k in keys)


def fetch_identity(identity):
    return httpx.get(f"http://127.0.0.1:{identity['port']}/_adrian/identity",timeout=.5,trust_env=False).json()


def wait_ready(identity,*,timeout=30,fetch=fetch_identity,opener=webbrowser.open,stop_event=None):
    end=monotonic()+timeout
    while monotonic()<end:
        if stop_event is not None and stop_event.is_set():raise LaunchError('已停止等待服务就绪')
        try:actual=fetch(identity)
        except (OSError,ValueError,httpx.HTTPError):actual=None
        if matches_identity(actual,identity):
            url=f"http://127.0.0.1:{identity['port']}/"
            if opener(url) is False:raise LaunchError('服务已就绪，但浏览器未打开；请访问 '+url)
            return url
        sleep(min(.1,max(0,end-monotonic())))
    raise LaunchError('服务未通过身份/就绪检查，未打开其他程序的页面')


def open_existing(database,wanted,*,timeout=25,opener=webbrowser.open):
    lease=DatabaseLease(database,wanted);end=monotonic()+timeout
    while monotonic()<end:
        try:existing=lease.read_metadata()
        except (OSError,ValueError):sleep(.1);continue
        keys=('product','protocol','workspace_id','database_id','port','mode','memory_mode')
        if any(existing.get(k)!=wanted.get(k) for k in keys):
            raise LaunchError('该数据库由不同工作区或运行模式占用；请先关闭原服务')
        return wait_ready(existing,timeout=max(.1,end-monotonic()),opener=opener)
    raise LaunchError('已有启动进程尚未就绪；请查看其服务窗口，不另开写库进程')
