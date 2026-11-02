"""进程锁与本地服务身份；不启动真实模型请求或浏览器。"""
import json
import socket
from pathlib import Path
import pytest

from min_agent.web.launcher import DatabaseLease, DatabaseInUse, identity_for, matches_identity, port_available, wait_ready, LaunchError


def identity(tmp_path):
    return identity_for(tmp_path/'中文 空格'/'workspace.sqlite3',8765,demo=False,memory_mode='active',workspace=tmp_path)


def test_same_database_has_one_writer_and_can_restart_without_deleting_lock(tmp_path):
    database=tmp_path/'中文 空格'/'workspace.sqlite3'
    original=identity(tmp_path)
    with DatabaseLease(database,original) as first:
        assert first.read_metadata()['instance_id']==original['instance_id']
        with pytest.raises(DatabaseInUse):
            with DatabaseLease(database,identity(tmp_path)):pass
    with DatabaseLease(database,identity(tmp_path)) as restarted:
        assert restarted.read_metadata()['instance_id']!=original['instance_id']
    assert not database.exists()  # 写库锁本身不创建或迁移数据库。


def test_identity_rejects_other_program_port_and_other_workspace(tmp_path):
    wanted=identity(tmp_path)
    assert matches_identity(dict(wanted,ready=True),wanted)
    for key in ['product','instance_id','database_id','workspace_id','mode','memory_mode']:
        assert not matches_identity(dict(wanted,ready=True,**{key:'different'}),wanted)
    assert not matches_identity({'ready':True},wanted)


def test_occupied_port_is_not_treated_as_ready(tmp_path):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));listener.listen()
        assert not port_available(listener.getsockname()[1])


def test_browser_only_opens_after_exact_ready_identity(tmp_path):
    wanted=identity(tmp_path);opened=[]
    url=wait_ready(wanted,timeout=.2,fetch=lambda _:dict(wanted,ready=True),opener=opened.append)
    assert opened==[url] and url=='http://127.0.0.1:8765/'
    opened.clear()
    with pytest.raises(LaunchError):
        wait_ready(wanted,timeout=.01,fetch=lambda _:dict(wanted,ready=True,instance_id='other'),opener=opened.append)
    assert opened==[]
