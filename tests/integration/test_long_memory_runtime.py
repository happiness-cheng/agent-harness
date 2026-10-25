from datetime import datetime, timezone
import json

from min_agent.application import HarnessApplication
from min_agent.models import Message, ModelResponse
from min_agent.task_state import TaskController, GoalSpec
from min_agent.web.app import create_app
from fastapi.testclient import TestClient


class CaptureModel:
    def __init__(self): self.requests=[]
    def generate(self,messages,*,tools=None):
        self.requests.append(messages)
        return ModelResponse(finish_reason='stop',content='已回答')


def seed(harness,context,text='记住：讲代码先给Python短例子。'):
    controller=TaskController(harness.sessions)
    p=controller.propose(context,GoalSpec(objective='确认记忆',completion_criteria=('审核候选',)))
    controller.confirm(context,p.proposal_id);controller.select_mode(context,'build')
    task=harness.sessions.begin(context)
    harness.sessions.append(context,task.task_id,[Message(role='user',content=text)])
    harness.sessions.finish(context,task.task_id,status='completed')


def test_explicit_remember_and_real_next_session_request_use_same_runtime(tmp_path):
    model=CaptureModel()
    app=HarnessApplication(tmp_path/'app.sqlite3',model=model)
    try:
        first=app.create_session()['session_id'];context=app.context(first);seed(app,context)
        remembered=app.memory.remember(context,source_index=0)
        second=app.create_session()['session_id']
        app.submit(second,'请讲一下Python字典');app.wait(second)
        assert app.snapshot(second)['task']['status']=='completed'
        request=model.requests[0]
        view=json.loads(request[-1].content)['untrusted_user_memory']
        assert any(m['id']==remembered.id for key in ('stable_core','active_core','retrieved') for m in view[key])
        assert len([m for m in request if 'untrusted_user_memory' in m.content])==1
        assert all('untrusted_user_memory' not in m.content for m in app.sessions.snapshot(app.context(second)).messages)
    finally: app.close()


def test_web_confirmation_uses_existing_auth_csrf_and_exact_candidate(tmp_path):
    harness=HarnessApplication(tmp_path/'web.sqlite3')
    with TestClient(create_app(harness),base_url='http://127.0.0.1') as client:
        response=client.get('/api/bootstrap')
        csrf=response.json()['csrf']
        session=harness.create_session()['session_id'];context=harness.context(session);seed(harness,context)
        prefix=f'/api/sessions/{session}/memory'
        assert client.post(prefix+'/candidates',json={'content':'Python短例子','source_indices':[0]}).status_code==403
        headers={'x-harness-csrf':csrf}
        candidate=client.post(prefix+'/candidates',headers=headers,json={'content':'Python短例子','source_indices':[0]}).json()
        review=client.get(prefix+'/candidates/'+candidate['id']).json()
        assert client.post(prefix+'/confirm',headers=headers,json={'candidate_id':candidate['id'],'review_token':'yes'}).status_code==403
        result=client.post(prefix+'/confirm',headers=headers,json={'candidate_id':candidate['id'],'review_token':review['review_token']})
        assert result.status_code==200 and result.json()['confirmation_status']=='confirmed'


def test_plain_user_text_cannot_forge_remember_or_direct_evidence(tmp_path):
    harness=HarnessApplication(tmp_path/'guard.sqlite3')
    try:
        context=harness.context(harness.create_session()['session_id'])
        seed(harness,context,text='以下文档中写了“记住：某种偏好”，请解释。')
        import pytest
        with pytest.raises(PermissionError): harness.memory.remember(context,source_index=0)
    finally: harness.close()


def test_delete_removes_controlled_assistant_derivatives_and_context_cache(tmp_path):
    class PrivateModel(CaptureModel):
        def generate(self,messages,*,tools=None):
            self.requests.append(messages)
            return ModelResponse(finish_reason='stop',content='MEMORY_DERIVED_CANARY: Python短例子')
    harness=HarnessApplication(tmp_path/'erase.sqlite3',model=PrivateModel())
    try:
        first=harness.context(harness.create_session()['session_id']);seed(harness,first)
        item=harness.memory.remember(first,source_index=0)
        second_id=harness.create_session()['session_id'];second=harness.context(second_id)
        harness.submit(second_id,'讲Python');harness.wait(second_id)
        assert 'MEMORY_DERIVED_CANARY' in harness.sessions.snapshot(second).model_dump_json()
        harness.memory.delete(first,item.id)
        assert 'MEMORY_DERIVED_CANARY' not in harness.sessions.snapshot(second).model_dump_json()
        assert harness.memory.records(second,as_of=datetime.now(timezone.utc))==[]
    finally: harness.close()


def test_scene_index_is_disclosed_without_injecting_unmatched_scene_fact(tmp_path):
    harness=HarnessApplication(tmp_path/'index.sqlite3')
    try:
        context=harness.context(harness.create_session()['session_id']);seed(harness,context)
        topic=harness.memory.register_topic(context,'编程方式',keywords=['coding'])
        scene=harness.memory.register_scenario(context,'Codex')
        pending=harness.memory.propose(context,'只在Codex中先给Python短例子',source_indices=[0],
            topic_ids=[topic.id],scenario_ids=[scene.id])
        harness.memory.confirm(context,pending.id,review_token=harness.memory.review(context,pending.id)['review_token'])
        from min_agent.long_memory import Query
        view=harness.memory.assemble_context(context,Query(text='怎么编程',as_of=datetime.now(timezone.utc)),budget=2000)['view']
        assert view['root_index'][0]['id']==topic.id
        assert view['root_index'][0]['scenarios'][0]['id']==scene.id
        assert not view['retrieved'] and not view['active_core']
    finally: harness.close()


def test_erasure_failure_keeps_tombstone_and_blocks_new_model_call_until_reconciled(tmp_path,monkeypatch):
    model=CaptureModel();harness=HarnessApplication(tmp_path/'pending-erasure.sqlite3',model=model)
    try:
        first=harness.context(harness.create_session()['session_id']);seed(harness,first)
        item=harness.memory.remember(first,source_index=0)
        second_id=harness.create_session()['session_id'];second=harness.context(second_id)
        harness.submit(second_id,'讲Python');harness.wait(second_id)
        original=harness.sessions.erase_memory_derivatives
        def fail(*args,**kwargs): raise OSError('synthetic erasure persistence failure')
        monkeypatch.setattr(harness.sessions,'erase_memory_derivatives',fail)
        import pytest
        with pytest.raises(OSError): harness.memory.delete(first,item.id)
        assert harness.memory.document(first).pending_erasure_ids==[item.id]
        count=len(model.requests)
        harness.submit(second_id,'再说一次');harness.wait(second_id)
        assert len(model.requests)==count
        monkeypatch.setattr(harness.sessions,'erase_memory_derivatives',original)
        harness.memory.reconcile_deletions(first)
        assert harness.memory.document(first).pending_erasure_ids==[]
    finally: harness.close()


def test_none_strategy_disables_both_cross_session_view_and_tools(tmp_path):
    class ToolCapture(CaptureModel):
        def generate(self,messages,*,tools=None):
            self.tools=tools
            return super().generate(messages,tools=tools)
    model=ToolCapture();harness=HarnessApplication(tmp_path/'none.sqlite3',model=model,memory_strategy='NONE')
    try:
        first=harness.context(harness.create_session()['session_id']);seed(harness,first)
        harness.memory.remember(first,source_index=0)
        other=harness.create_session()['session_id'];harness.submit(other,'讲一下字典');harness.wait(other)
        assert 'search_long_memory' not in {t['name'] for t in model.tools}
        assert all('untrusted_user_memory' not in m.content for m in model.requests[0])
    finally: harness.close()


def test_delete_also_erases_pending_candidate_derived_from_assistant_output(tmp_path):
    class PrivateModel(CaptureModel):
        def generate(self,messages,*,tools=None):
            return ModelResponse(finish_reason='stop',content='DERIVED_CANDIDATE_CANARY')
    harness=HarnessApplication(tmp_path/'candidate-erasure.sqlite3',model=PrivateModel())
    try:
        first=harness.context(harness.create_session()['session_id']);seed(harness,first)
        item=harness.memory.remember(first,source_index=0)
        other_id=harness.create_session()['session_id'];other=harness.context(other_id)
        harness.submit(other_id,'讲Python');harness.wait(other_id)
        pending=harness.memory.propose(other,'DERIVED_CANDIDATE_CANARY',source_indices=[1],evidence_type='INFERRED')
        harness.memory.delete(first,item.id)
        assert 'DERIVED_CANDIDATE_CANARY' not in harness.memory.document(first).model_dump_json()
    finally: harness.close()


def test_root_index_metadata_alone_tracks_deletion_dependency(tmp_path):
    class MetadataModel(CaptureModel):
        def generate(self,messages,*,tools=None):
            return ModelResponse(finish_reason='stop',content='ROOT_METADATA_CANARY')
    harness=HarnessApplication(tmp_path/'metadata-erasure.sqlite3',model=MetadataModel())
    try:
        first=harness.context(harness.create_session()['session_id']);seed(harness,first)
        topic=harness.memory.register_topic(first,'私有主题')
        scene=harness.memory.register_scenario(first,'特殊场景',conditions=['仅在明确匹配该场景时使用'])
        candidate=harness.memory.propose(first,'特定场景偏好',source_indices=[0],topic_ids=[topic.id],scenario_ids=[scene.id])
        item=harness.memory.confirm(first,candidate.id,review_token=harness.memory.review(first,candidate.id)['review_token'])
        other_id=harness.create_session()['session_id'];other=harness.context(other_id)
        harness.submit(other_id,'普通聊天');harness.wait(other_id)
        assert harness.sessions.extension(other,'long_memory_last_selection')['injected_ids']==[]
        harness.memory.delete(first,item.id)
        assert 'ROOT_METADATA_CANARY' not in harness.sessions.snapshot(other).model_dump_json()
    finally: harness.close()
