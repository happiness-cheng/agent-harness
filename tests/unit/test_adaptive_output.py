import json
import httpx
import pytest

from min_agent.llm.deepseek import DeepSeekResponsesModel,DeepSeekSettings
from min_agent.models import Message


def model_fixture(window=1048576,output=393216):
    return DeepSeekResponsesModel(DeepSeekSettings(api_key='synthetic-adaptive-token',max_output_tokens=None),
        transport=httpx.MockTransport(lambda request:httpx.Response(200,json={'data':[{
            'id':'deepseek-flash','context_window':window,'max_output_tokens':output}]})))


def test_adaptive_output_uses_model_cap_instead_of_4096():
    from min_agent.model_context import output_reserve
    with model_fixture() as model:
        body=model._build_body([Message(role='user',content='简短问题')],None)
        assert body['max_output_tokens']==393216 and output_reserve(model)==0
        assert model.request_count==0


def test_adaptive_output_shrinks_only_for_actual_context_headroom():
    with model_fixture(window=50000,output=40000) as model:
        body=model._build_body([Message(role='user',content='x'*20000)],None)
        assert 0<body['max_output_tokens']<40000
        assert len(json.dumps(body,ensure_ascii=False).encode())+body['max_output_tokens']<=50000


def test_over_window_does_not_send_generation_and_explicit_output_is_respected():
    with model_fixture(window=20000,output=10000) as model:
        with pytest.raises(ValueError):model._build_body([Message(role='user',content='x'*30000)],None)
        assert model.request_count==0
    with DeepSeekResponsesModel(DeepSeekSettings(api_key='synthetic-fixed-token',max_output_tokens=1234),
            transport=httpx.MockTransport(lambda _:pytest.fail('显式输出构造不应发现模型'))) as model:
        assert model._build_body([Message(role='user',content='小问题')],None)['max_output_tokens']==1234
