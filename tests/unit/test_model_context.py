"""模型容量来自能力接口；全程MockTransport，不读取凭据或联网。"""
import json

import httpx
import pytest

from min_agent.llm.deepseek import DeepSeekResponsesModel, DeepSeekSettings
from min_agent.models import Message
from min_agent.context import request_bound
from tests.unit.test_deepseek_adapter import ds_response, ds_text


def metadata(window=1048576, output=16384, identity='deepseek-flash'):
    return {'object': 'list', 'data': [{'id': identity, 'context_window': window, 'max_output_tokens': output}]}


def test_provider_reads_model_limits_once_without_generation_or_chat_payload():
    captured=[]
    with DeepSeekResponsesModel(DeepSeekSettings(api_key='synthetic-unit-token'), transport=httpx.MockTransport(
            lambda request: captured.append(request) or httpx.Response(200, json=metadata(window=345678)))) as model:
        limits=model.context_limits()
        assert limits.context_window==345678
        assert model.context_limits()==limits and len(captured)==1
        assert model.request_count==0
    request=captured[0]
    assert request.method=='GET' and request.url.path=='/models' and not request.content
    assert str(request.url)=='https://api.deepseek.com/models'


@pytest.mark.parametrize('body', [metadata(window=0), metadata(window=True), metadata(window='1000000'),
    metadata(identity='other-model'), metadata(output=0), metadata(window=100, output=101), {'data': []}])
def test_missing_or_invalid_metadata_never_silently_defaults(body):
    with DeepSeekResponsesModel(DeepSeekSettings(api_key='synthetic-unit-token'),
            transport=httpx.MockTransport(lambda request: httpx.Response(200,json=body))) as model:
        with pytest.raises(ValueError): model.context_limits()
        assert model.request_count==0


def test_metadata_transport_failure_does_not_generate_or_fallback():
    def unavailable(request): raise httpx.ConnectError('synthetic unavailable')
    with DeepSeekResponsesModel(DeepSeekSettings(api_key='synthetic-unit-token'),
            transport=httpx.MockTransport(unavailable)) as model:
        with pytest.raises(ValueError): model.context_limits()
        assert model.request_count==0


def test_actual_request_size_does_not_double_count_native_protocol_state():
    from min_agent.llm.openai_protocol import parse_response
    reasoning={'type':'reasoning','content':[{'type':'reasoning_text','text':'synthetic-state '*600}]}
    response=parse_response(ds_response(reasoning,ds_text('synthetic answer '*300)),
        model='deepseek-flash',provider='deepseek.responses')
    messages=[Message(role='user',content='问题'),Message(role='assistant',content=response.content,provider_state=response.provider_state)]
    with DeepSeekResponsesModel(DeepSeekSettings(api_key='synthetic-unit-token'),
            transport=httpx.MockTransport(lambda request: pytest.fail('计量不得联网'))) as model:
        body=model._build_body(messages,[])
        assert request_bound(messages,[],model=model)==len(json.dumps(body,ensure_ascii=False).encode())+1024
        assert messages[1].provider_state is not None


def test_metadata_window_and_output_configuration_are_independent():
    from min_agent.model_context import context_window, output_reserve
    with DeepSeekResponsesModel(DeepSeekSettings(api_key='synthetic-unit-token',max_output_tokens=8192),
            transport=httpx.MockTransport(lambda request:httpx.Response(200,json=metadata(window=220000)))) as model:
        assert context_window(model)==220000 and output_reserve(model)==8192
        assert context_window(model,override=50000)==50000
        with pytest.raises(ValueError):context_window(model,override=300000)


def test_unknown_real_provider_requires_explicit_capability_instead_of_32k():
    from min_agent.model_context import context_window
    class Unknown:
        _provider='unknown.remote'
    with pytest.raises(ValueError):context_window(Unknown())


def test_configured_output_cannot_exceed_discovered_model_output_limit():
    from min_agent.model_context import context_window
    with DeepSeekResponsesModel(DeepSeekSettings(api_key='synthetic-unit-token',max_output_tokens=8192),
            transport=httpx.MockTransport(lambda request:httpx.Response(200,json=metadata(window=220000,output=4000)))) as model:
        with pytest.raises(ValueError):context_window(model)


def test_identical_input_admission_follows_discovered_capacity():
    from uuid import uuid4
    from min_agent.artifacts import ArtifactStore
    from min_agent.context import ContextManager, ContextLimit
    from min_agent.sessions import SessionStore
    text=Message(role='user',content='必须保留的当前原文'*1500)
    for window, allowed in [(220000,True),(20000,False)]:
        with DeepSeekResponsesModel(DeepSeekSettings(api_key='synthetic-unit-token'),
                transport=httpx.MockTransport(lambda request:httpx.Response(200,json=metadata(window=window)))) as model:
            store=SessionStore();context=store.create(uuid4())
            manager=ContextManager(store,context,ArtifactStore(store,context),model=model)
            if allowed:
                assert manager.prepare([text])==[text]
            else:
                with pytest.raises(ContextLimit):manager.prepare([text])
            assert model.request_count==0


def test_existing_subloops_share_model_capacity_and_configured_output_reserve():
    from uuid import uuid4
    from min_agent.artifacts import ArtifactStore
    from min_agent.context import ContextManager
    from min_agent.compaction import Compactor
    from min_agent.compression_selector import GroundedSelector
    from min_agent.compression_summary import SemanticCompressor
    from min_agent.skill_retention import SkillRetentionSelector
    from min_agent.research import ResearchService
    from min_agent.sessions import SessionStore
    requests=[]
    with DeepSeekResponsesModel(DeepSeekSettings(api_key='synthetic-unit-token',max_output_tokens=8192),
            transport=httpx.MockTransport(lambda request: requests.append(request) or httpx.Response(200,json=metadata(window=743210)))) as model:
        store=SessionStore();context=store.create(uuid4());artifacts=ArtifactStore(store,context)
        components=[Compactor(model,store,context,None),GroundedSelector(model,None),
            SemanticCompressor(model,None),SkillRetentionSelector(model,None),ResearchService(model,None,None,artifacts)]
        assert all(c.input_window==743210 for c in components)
        manager=ContextManager(store,context,artifacts,model=model)
        assert manager.window==743210 and manager.output_reserve==8192
        assert len(requests)==1 and model.request_count==0
