import json
from uuid import uuid4
import httpx


def test_hidden_acceptance_checks_are_not_in_live_model_input(tmp_path,monkeypatch):
    from min_agent.evaluation.contracts import TaskSpec,CheckSpec
    from min_agent.evaluation.worker import execute
    from min_agent.llm.deepseek import DeepSeekResponsesModel,DeepSeekSettings
    import min_agent.llm.deepseek as provider
    from tests.unit.test_deepseek_adapter import ds_response,ds_text
    requests=[]
    def handle(request):
        if request.method=='GET':return httpx.Response(200,json={'data':[{'id':'deepseek-flash','context_window':1048576,'max_output_tokens':393216}]})
        requests.append(json.loads(request.content));return httpx.Response(200,json=ds_response(ds_text('没有未经证据确认的事实。')))
    actual=DeepSeekResponsesModel(DeepSeekSettings(api_key='synthetic-unit-token',max_output_tokens=None),transport=httpx.MockTransport(handle))
    monkeypatch.setattr(provider,'DeepSeekResponsesModel',lambda *a,**k:actual)
    credentials=tmp_path/'synthetic.json';credentials.write_text('{"deepseek_api_key":"synthetic-unit-token"}',encoding='utf-8')
    task=TaskSpec(id='gold-boundary',category='runtime',prompt='你好',live_eligible=True,
        checks=[CheckSpec(id='hidden',kind='final_contains',expected='GOLD_ONLY_NOT_SENT')])
    observation=execute(task,tmp_path,live=True,credentials=credentials,owner_id=uuid4())
    assert observation.execution_status=='completed' and len(requests)==1
    assert 'GOLD_ONLY_NOT_SENT' not in json.dumps(requests,ensure_ascii=False)
    assert requests[0]['max_output_tokens']>4096
