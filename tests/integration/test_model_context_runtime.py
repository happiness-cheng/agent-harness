"""完整Harness重放真实协议状态累积；不调用外部模型。"""
import json

import httpx

from min_agent.application import HarnessApplication
from min_agent.llm.deepseek import DeepSeekResponsesModel, DeepSeekSettings
from tests.unit.test_deepseek_adapter import ds_response, ds_text
from tests.unit.test_model_context import metadata


def test_native_responses_search_empty_memory_reaches_final_answer_without_32k_gate(tmp_path):
    posts=[];gets=[]
    synthetic_reasoning={'type':'reasoning','content':[{'type':'reasoning_text','text':'synthetic opaque continuation '*150}]}
    def handle(request):
        if request.method=='GET':
            gets.append(request)
            return httpx.Response(200,json=metadata())
        body=json.loads(request.content);posts.append(body)
        if len(posts)==1:return httpx.Response(200,json=ds_response(synthetic_reasoning,ds_text('我是本地助理，按已启用的工具协助你。')))
        if len(posts)==2:return httpx.Response(200,json=ds_response(synthetic_reasoning,dict(
            type='function_call',call_id='search-context-regression',name='search_long_memory',arguments='{"question":"用户背景和偏好"}')))
        assert len(posts)==3
        assert any(i.get('type')=='function_call_output' and 'memory_ids' in i['output'] for i in body['input'])
        assert any(i.get('type')=='reasoning' for i in body['input'])
        return httpx.Response(200,json=ds_response(ds_text('当前没有检索到已确认的长期记忆，不能据此断言完整历史不存在。')))
    with DeepSeekResponsesModel(DeepSeekSettings(api_key='synthetic-unit-token',max_requests=5),transport=httpx.MockTransport(handle)) as model:
        app=HarnessApplication(tmp_path/'actual-runtime.sqlite3',model=model,memory_mode='active')
        try:
            session=app.create_session()['session_id']
            app.submit(session,'你好，请介绍一下你自己');app.wait(session)
            assert app.snapshot(session)['task']['status']=='completed'
            app.submit(session,'你是什么模型？你对我了解多少？');app.wait(session)
            saved=app.snapshot(session)
            assert saved['task']['status']=='completed'
            assert '没有检索到' in saved['messages'][-1]['content']
            assert len(posts)==3 and len(gets)==1 and model.request_count==3
            assert saved['long_memory']['receipt']['status']=='delivered'
        finally:app.close()


def test_model_actual_small_window_still_blocks_oversize_input(tmp_path):
    posts=[]
    def handle(request):
        if request.method=='GET':return httpx.Response(200,json=metadata(window=20000,output=10000))
        posts.append(request)
        raise AssertionError('模型窗口不足不得生成')
    with DeepSeekResponsesModel(DeepSeekSettings(api_key='synthetic-unit-token'),transport=httpx.MockTransport(handle)) as model:
        app=HarnessApplication(tmp_path/'small-window.sqlite3',model=model,memory_mode='active')
        try:
            session=app.create_session()['session_id'];app.submit(session,'明确保留用户输入'*1500);app.wait(session)
            saved=app.snapshot(session)
            assert saved['task']['status']=='partial' and saved['task']['error_code']=='context_limit'
            assert not posts and model.request_count==0
        finally:app.close()
