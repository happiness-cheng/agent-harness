"""通过真实应用公开接口检查入口与隔离，不修改注册表私有成员。"""
import json
from uuid import uuid4
import pytest
from min_agent.application import HarnessApplication
from min_agent.artifacts import ArtifactStore

def collection():
    from min_agent.research_resources import ResearchCollection, ResearchDocument
    return ResearchCollection('controlled-v2', '受控测试文档库', (
        ResearchDocument('doc', '受控文档标题', 'https://fixtures.example/doc', '不可提前泄漏的正文',
                         'synthetic_document', '完整合成测试文档'),))

def test_public_mount_reports_actual_resources_without_body(tmp_path):
    app = HarnessApplication(tmp_path/'test.sqlite3', memory_mode='off')
    try:
        sid = app.create_session()['session_id']
        app.mount_research_collection(sid, collection())
        value = app.research_resources(sid)
        assert value['controlled_collection']['documents'][0]['title']=='受控文档标题'
        assert value['session_artifacts']==[] and not value['allow_public_web']
        assert '不可提前泄漏的正文' not in json.dumps(value, ensure_ascii=False)
        aid = ArtifactStore(app.sessions, app.context(sid)).save('Session正文', source='attachment:sample')
        assert app.research_resources(sid)['session_artifacts'][0]['artifact_id']==aid
        other = app.create_session()['session_id']
        assert app.research_resources(other)['controlled_collection'] is None
        assert app.research_resources(other)['session_artifacts']==[]
        with pytest.raises(PermissionError):
            ArtifactStore(app.sessions, app.context(other)).get(aid)
    finally:
        app.close()

def test_collection_search_and_read_are_closed_world(tmp_path):
    app = HarnessApplication(tmp_path/'test.sqlite3')
    try:
        sid = app.create_session()['session_id']
        corpus = collection()
        assert corpus.search('受控文档标题')['results'][0]['url']=='https://fixtures.example/doc'
        assert corpus.search('没有登记的内部资料')['results']==[]
        reader = corpus.reader(ArtifactStore(app.sessions, app.context(sid)))
        assert reader.read('https://fixtures.example/doc')['text']=='不可提前泄漏的正文'
        with pytest.raises(PermissionError):
            reader.read('https://private.example/unmounted')
    finally:
        app.close()

def test_duplicate_locators_and_invalid_mount_rejected(tmp_path):
    from min_agent.research_resources import ResearchCollection
    corpus = collection()
    with pytest.raises(ValueError):
        ResearchCollection('duplicate', '错误集合', (corpus.documents[0], corpus.documents[0]))
    app = HarnessApplication(tmp_path/'test.sqlite3')
    try:
        sid = app.create_session()['session_id']
        with pytest.raises(TypeError):
            app.mount_research_collection(sid, {'fake':'not a registered collection'})
        with pytest.raises(PermissionError):
            app.mount_research_collection(str(uuid4()), corpus)
    finally:
        app.close()
