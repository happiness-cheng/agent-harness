from uuid import uuid4
from fastapi.testclient import TestClient

from min_agent.application import HarnessApplication
from min_agent.web.app import create_app
from min_agent.models import ModelResponse
from tests.fakes.model import ScriptedModel


def test_web_sessions_owner_boundary_csrf_and_background_answer(tmp_path):
    harness = HarnessApplication(tmp_path / "app.sqlite3", model=ScriptedModel([
        ModelResponse(finish_reason="stop", content="真实应用链路答复"),
    ]))
    try:
        client = TestClient(create_app(harness), base_url="http://127.0.0.1")
        assert client.get('/api/sessions').status_code == 401
        bootstrap = client.get('/api/bootstrap').json()
        headers = {'X-Harness-CSRF': bootstrap['csrf']}
        first = client.post('/api/sessions', json={'label': '会话A'}, headers=headers).json()['session_id']
        second = client.post('/api/sessions', json={'label': '会话B'}, headers=headers).json()['session_id']
        assert client.post(f'/api/sessions/{first}/input', json={'text': '问题'}).status_code == 403
        assert client.post(f'/api/sessions/{first}/input', json={'text': '问题'}, headers={**headers,'Origin':'https://evil.example'}).status_code == 403
        assert client.get(f'/api/sessions/{uuid4()}').status_code == 403
        assert client.post(f'/api/sessions/{first}/input', json={'text': '问题'}, headers=headers).status_code == 202
        harness.wait(first)
        saved = client.get(f'/api/sessions/{first}').json()
        assert saved['messages'][-1]['content'] == '真实应用链路答复'
        assert client.get(f'/api/sessions/{second}').json()['messages'] == []
        assert client.get('/').status_code == 200
    finally:
        harness.close()


def test_explicit_dispatch_kicks_existing_queue_without_new_text(tmp_path):
    from min_agent.events import SessionEvents
    harness = HarnessApplication(tmp_path / "dispatch.sqlite3", model=ScriptedModel([
        ModelResponse(finish_reason="stop", content="已处理队列"),
    ]))
    try:
        client = TestClient(create_app(harness), base_url="http://127.0.0.1")
        headers = {'X-Harness-CSRF': client.get('/api/bootstrap').json()['csrf']}
        identity = harness.create_session()['session_id']
        events = SessionEvents(harness.sessions, harness.context(identity))
        events.stop(); harness.submit(identity, '保留的消息'); harness.wait(identity)
        assert harness.snapshot(identity)['messages'] == []
        assert client.post(f'/api/sessions/{identity}/control', json={'kind':'dispatch'}, headers=headers).status_code == 202
        harness.wait(identity)
        assert harness.snapshot(identity)['messages'][-1]['content'] == '已处理队列'
    finally: harness.close()


def test_confirmation_cannot_mutate_goal_during_running_model(tmp_path):
    from threading import Event
    import pytest
    entered, release = Event(), Event()
    class Model:
        def generate(self, *args, **kwargs):
            entered.set(); assert release.wait(10)
            return ModelResponse(finish_reason="stop", content="完成")
    harness = HarnessApplication(tmp_path / "confirm.sqlite3", model=Model())
    try:
        identity = harness.create_session()['session_id']; context = harness.context(identity)
        proposal = harness.propose(context, {'objective':'只读', 'completion_criteria':['回答'], 'actions':[]})
        harness.submit(identity, '开始'); assert entered.wait(10)
        with pytest.raises(RuntimeError): harness.confirm(identity, proposal.proposal_id)
        assert harness.snapshot(identity)['work_state']['confirmed_goal_id'] is None
    finally: release.set(); harness.close()
