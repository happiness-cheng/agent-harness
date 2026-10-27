"""真实HTTP进程/同库互斥/重启持久化，明确Demo，不调用模型API。"""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx

ROOT=Path(__file__).resolve().parents[2]


def free_port():
    with socket.socket() as stream:
        stream.bind(('127.0.0.1',0))
        return stream.getsockname()[1]


def command(database,port,*extra):
    return [sys.executable,'-X','utf8','-m','min_agent.web','--demo','--long-memory',
            '--database',str(database),'--port',str(port),*extra]


def environment():
    return dict(os.environ,PYTHONPATH=str(ROOT/'src'))


def wait_for(client,process):
    end=time.monotonic()+20
    while time.monotonic()<end:
        if process.poll() is not None:
            raise AssertionError(process.communicate()[0])
        try:
            response=client.get('/_adrian/identity')
            if response.status_code==200:return response.json()
        except httpx.HTTPError:pass
        time.sleep(.1)
    raise AssertionError('isolated server not ready')


def stop(client,process,identity):
    try:
        csrf=client.get('/api/bootstrap').json()['csrf']
        result=client.post('/api/adrian/shutdown',json={'instance_id':identity['instance_id']},headers={'X-Harness-CSRF':csrf})
        assert result.status_code==202
        process.wait(timeout=15)
    finally:
        if process.poll() is None:process.terminate();process.wait(timeout=10)


def test_http_single_writer_close_restart_preserves_session(tmp_path):
    database=tmp_path/'中文 空格'/'workspace.sqlite3';port=free_port()
    options=dict(cwd=ROOT,env=environment(),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8')
    if os.name=='nt':options['creationflags']=subprocess.CREATE_NO_WINDOW
    process=subprocess.Popen(command(database,port),**options)
    with httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=2,trust_env=False) as client:
        identity=wait_for(client,process)
        try:
            csrf=client.get('/api/bootstrap').json()['csrf'];headers={'X-Harness-CSRF':csrf}
            session=client.post('/api/sessions',json={'label':'重启保留的隔离会话'},headers=headers).json()['session_id']
            assert client.post(f'/api/sessions/{session}/input',json={'text':'hello'},headers=headers).status_code==202
            end=time.monotonic()+10
            while time.monotonic()<end:
                saved=client.get(f'/api/sessions/{session}').json()
                if saved['task'] and saved['task']['status']=='completed':break
                time.sleep(.1)
            assert saved['task']['status']=='completed'
            duplicate=subprocess.run(command(database,free_port()),**{k:v for k,v in options.items() if k!='creationflags'},timeout=15)
            assert duplicate.returncode!=0 and '数据库已有网页服务' in duplicate.stdout
            assert client.get('/_adrian/identity').json()['pid']==identity['pid']
        finally:stop(client,process,identity)
    process=subprocess.Popen(command(database,port),**options)
    with httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=2,trust_env=False) as client:
        again=wait_for(client,process)
        try:
            assert again['instance_id']!=identity['instance_id']
            client.get('/api/bootstrap')
            resumed=client.get(f'/api/sessions/{session}').json()
            assert resumed['label']=='重启保留的隔离会话' and resumed['messages']==saved['messages']
        finally:stop(client,process,again)


def test_other_program_port_and_missing_credentials_do_not_create_database(tmp_path):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));listener.listen()
        database=tmp_path/'blocked.sqlite3'
        response=subprocess.run(command(database,listener.getsockname()[1]),cwd=ROOT,env=environment(),capture_output=True,text=True,encoding='utf-8',timeout=15)
        assert response.returncode!=0 and '其他程序占用' in response.stderr
        assert not database.exists()
    database=tmp_path/'no-credential.sqlite3'
    response=subprocess.run([sys.executable,'-X','utf8','-m','min_agent.web','--live','--database',str(database),
        '--credentials-file',str(tmp_path/'missing.json'),'--port',str(free_port())],cwd=ROOT,env=environment(),capture_output=True,text=True,encoding='utf-8',timeout=15)
    assert response.returncode!=0 and '没有回退Demo' in response.stderr
    assert not database.exists()
