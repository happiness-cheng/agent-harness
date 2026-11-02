"""本地评测不能自动回退云端，额度检查必须发生在创建运行目录前。"""
import pytest
from min_agent.evaluation.runner import run_task, run_suite
from min_agent.evaluation.suites import task_by_id


def test_local_and_cloud_cannot_be_mixed(tmp_path):
    with pytest.raises(ValueError):
        run_task(task_by_id('runtime.calc'), tmp_path/'run', live=True,
                 credentials=tmp_path/'unread.json', local_model={})
    assert not (tmp_path/'run').exists()


def test_local_suite_reserves_requests_before_start(tmp_path):
    with pytest.raises(ValueError):
        run_suite([task_by_id('runtime.calc')], tmp_path/'suite', local_model={}, request_ceiling=1)
    assert not (tmp_path/'suite').exists()


def test_local_rejects_remote_endpoint(tmp_path):
    with pytest.raises(ValueError):
        run_task(task_by_id('runtime.calc'), tmp_path/'run', local_model={'base_url':'https://example.com'})
    assert not (tmp_path/'run').exists()


def test_local_worker_uses_real_adapter_without_sending_gold(tmp_path, monkeypatch):
    import json
    import httpx
    from uuid import uuid4
    from min_agent.evaluation.contracts import TaskSpec, CheckSpec
    from min_agent.evaluation.worker import execute
    calls = []
    def handle(request):
        if request.method == 'GET':
            return httpx.Response(200,json={'models':[{'name':'qwen3.5:4b','digest':'fixture-digest'}]})
        calls.append(json.loads(request.content))
        return httpx.Response(200,json={'model':'qwen3.5:4b','done':True,'done_reason':'stop',
            'message':{'role':'assistant','content':'你好'},'prompt_eval_count':100,'eval_count':3})
    client = httpx.Client
    monkeypatch.setattr(httpx,'Client',lambda **kw:client(**{**kw,'transport':httpx.MockTransport(handle)}))
    task=TaskSpec(id='local-boundary',category='runtime',prompt='你好',
                  checks=[CheckSpec(id='secret',kind='final_contains',expected='GOLD_ONLY_NOT_SENT')])
    result=execute(task,tmp_path,owner_id=uuid4(),local_model={})
    assert result.execution_status=='completed'
    assert result.extra['backend']=='OLLAMA_LOCAL' and result.extra['local_model_digest']=='fixture-digest'
    assert len(calls)==1 and 'GOLD_ONLY_NOT_SENT' not in json.dumps(calls)
