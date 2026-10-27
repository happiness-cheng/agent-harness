"""真正Harness/SQLite/上下文路径，只用受控Model，不调用外部API。"""
import json
from uuid import uuid4

from fastapi.testclient import TestClient
from min_agent.application import HarnessApplication
from min_agent.models import ModelResponse, ToolCall
from min_agent.web.app import create_app
from tests.integration.test_long_memory_runtime import seed


class RecallModel:
    def __init__(self):self.requests=[];self.before_read=None
    def generate(self,messages,*,tools=None):
        self.requests.append(messages)
        history=[m for m in messages if m.role=='tool']
        if not history:
            return ModelResponse(finish_reason='tool_calls',tool_calls=[ToolCall(
                id='search',name='search_long_memory',arguments={'question':'Python'})])
        if len(history)==1:
            ids=json.loads(json.loads(history[-1].content)['result'])['memory_ids']
            if self.before_read:self.before_read()
            if ids:
                return ModelResponse(finish_reason='tool_calls',tool_calls=[ToolCall(
                    id='read',name='read_long_memory_evidence',arguments={'memory_ids':ids})])
            return ModelResponse(finish_reason='stop',content='未找到可读的已确认记忆，不据此推断完整历史不存在。')
        view=json.loads(messages[-1].content)['untrusted_user_memory']
        entry=view['evidence'][0]
        return ModelResponse(finish_reason='stop',content=entry['quotes'][0]['text']+' [memory:'+entry['source']+']')


def test_active_memory_cross_session_uses_actual_runtime_and_records_delivery(tmp_path):
    model=RecallModel();app=HarnessApplication(tmp_path/'shared.sqlite3',model=model,memory_mode='active')
    try:
        first=app.create_session()['session_id'];ctx=app.context(first);seed(app,ctx)
        item=app.memory.remember(ctx,source_index=0)
        second=app.create_session()['session_id'];app.submit(second,'请回忆我的Python讲解偏好');app.wait(second)
        saved=app.snapshot(second)
        assert saved['task']['status']=='completed' and len(model.requests)==3
        assert item.id in saved['messages'][-1]['content']
        memory=saved['long_memory']
        assert memory['receipt']['status']=='delivered' and memory['receipt']['delivered']==[item.id]
        assert memory['citation']['status']=='valid'
        assert all('untrusted_user_memory' not in m['content'] for m in saved['messages'])
        assert len(app.memory.document(ctx).memories)==1 and not app.memory.list_pending(ctx)
    finally:app.close()


def test_memory_switch_is_authenticated_persistent_and_does_not_change_other_defaults(tmp_path):
    path=tmp_path/'switch.sqlite3';app=HarnessApplication(path,memory_mode='active')
    session=app.create_session()['session_id']
    with TestClient(create_app(app),base_url='http://127.0.0.1') as client:
        csrf=client.get('/api/bootstrap').json()['csrf']
        url=f'/api/sessions/{session}/memory-permission'
        assert client.post(url,json={'enabled':False}).status_code==403
        assert client.post(url,json={'enabled':False},headers={'X-Harness-CSRF':csrf}).status_code==200
        assert not client.get(f'/api/sessions/{session}').json()['long_memory']['enabled']
    reopened=HarnessApplication(path,memory_mode='active')
    try:assert not reopened.snapshot(session)['long_memory']['enabled']
    finally:reopened.close()
    legacy=HarnessApplication(tmp_path/'legacy.sqlite3')
    try:assert legacy.memory_mode=='legacy'
    finally:legacy.close()


def test_ordinary_task_and_explicit_off_do_not_require_memory(tmp_path):
    class Plain:
        def generate(self,messages,*,tools=None):
            assert 'search_long_memory' not in {t['name'] for t in tools}
            assert all('untrusted_user_memory' not in m.content for m in messages)
            return ModelResponse(finish_reason='stop',content='普通任务答复')
    app=HarnessApplication(tmp_path/'off.sqlite3',model=Plain(),memory_mode='off')
    try:
        session=app.create_session()['session_id'];app.submit(session,'你好');app.wait(session)
        assert app.snapshot(session)['messages'][-1]['content']=='普通任务答复'
    finally:app.close()


def test_revoked_or_foreign_memory_never_reaches_provider(tmp_path):
    model=RecallModel();app=HarnessApplication(tmp_path/'revoked.sqlite3',model=model,memory_mode='active')
    try:
        first=app.create_session()['session_id'];ctx=app.context(first);seed(app,ctx,'记住：Python SECRET_SYNTHETIC_ONLY')
        record=app.memory.remember(ctx,source_index=0)
        model.before_read=lambda:app.memory.delete(ctx,record.id)
        second=app.create_session()['session_id'];app.submit(second,'回忆Python偏好');app.wait(second)
        assert len(model.requests)==2
        assert all('SECRET_SYNTHETIC_ONLY' not in m.content for request in model.requests for m in request)
        index=len(app.sessions.snapshot(ctx).messages)
        seed(app,ctx,'记住：Python OTHER_OWNER_ONLY')
        app.memory.remember(ctx,source_index=index)
    finally:app.close()
    other_model=RecallModel();other=HarnessApplication(tmp_path/'revoked.sqlite3',owner_id=uuid4(),model=other_model,memory_mode='active')
    try:
        session=other.create_session()['session_id'];other.submit(session,'回忆Python');other.wait(session)
        assert '未找到' in other.snapshot(session)['messages'][-1]['content']
        assert all('OTHER_OWNER_ONLY' not in m.content for request in other_model.requests for m in request)
    finally:other.close()


def test_invalid_memory_citation_is_not_published_as_valid_answer(tmp_path):
    class Invalid:
        def generate(self,messages,*,tools=None):
            return ModelResponse(finish_reason='stop',content='伪造的记忆 [memory:not-a-delivered-id]')
    app=HarnessApplication(tmp_path/'citation.sqlite3',model=Invalid(),memory_mode='active')
    try:
        session=app.create_session()['session_id'];app.submit(session,'回忆');app.wait(session)
        saved=app.snapshot(session)
        assert saved['task']['status']=='failed'
        assert saved['long_memory']['citation']['status']=='invalid'
        assert all('伪造的记忆' not in m['content'] for m in saved['messages'])
    finally:app.close()


def test_read_source_endpoint_rechecks_delivery_and_revocation(tmp_path):
    app=HarnessApplication(tmp_path/'source.sqlite3',model=RecallModel(),memory_mode='active')
    with TestClient(create_app(app),base_url='http://127.0.0.1') as client:
        client.get('/api/bootstrap')
        source=app.create_session()['session_id'];ctx=app.context(source);seed(app,ctx)
        item=app.memory.remember(ctx,source_index=0)
        target=app.create_session()['session_id'];url=f'/api/sessions/{target}/memory/evidence/{item.id}'
        assert client.get(url).status_code==403
        app.submit(target,'回忆Python');app.wait(target)
        assert client.get(url).status_code==200
        app.memory.delete(ctx,item.id)
        assert client.get(url).status_code==403


def test_shutdown_requires_cookie_csrf_and_current_instance(tmp_path):
    app=HarnessApplication(tmp_path/'shutdown.sqlite3')
    from min_agent.web.launcher import identity_for
    identity=identity_for(tmp_path/'shutdown.sqlite3',8765,demo=True,memory_mode='legacy')
    web=create_app(app,identity=identity);calls=[];web.state.shutdown_callback=lambda:calls.append(1)
    with TestClient(web,base_url='http://127.0.0.1') as client:
        csrf=client.get('/api/bootstrap').json()['csrf'];headers={'X-Harness-CSRF':csrf}
        assert client.post('/api/adrian/shutdown',json={'instance_id':identity['instance_id']}).status_code==403
        assert client.post('/api/adrian/shutdown',json={'instance_id':'old'},headers=headers).status_code==409
        assert calls==[]
        assert client.post('/api/adrian/shutdown',json={'instance_id':identity['instance_id']},headers=headers).status_code==202
        assert calls==[1]


def test_explicit_scenario_limits_recall_without_changing_memory_lifecycle(tmp_path):
    model=RecallModel();app=HarnessApplication(tmp_path/'scenario.sqlite3',model=model,memory_mode='active')
    try:
        first=app.create_session()['session_id'];ctx=app.context(first);seed(app,ctx)
        scene=app.memory.register_scenario(ctx,'出差')
        record=app.memory.remember(ctx,source_index=0,scenario_ids=[scene.id])
        shared=app.create_session()['session_id'];app.submit(shared,'回忆Python');app.wait(shared)
        assert '未找到' in app.snapshot(shared)['messages'][-1]['content']
        scoped=app.create_session()['session_id'];app.configure_memory(scoped,True,[scene.id])
        app.submit(scoped,'回忆出差时Python偏好');app.wait(scoped)
        saved=app.snapshot(scoped)
        assert saved['long_memory']['receipt']['delivered']==[record.id]
        assert app.memory.document(ctx).memories[record.id][-1].lifecycle=='active'
        import pytest
        with pytest.raises(PermissionError):app.configure_memory(scoped,True,['not-an-owned-scene'])
    finally:app.close()
