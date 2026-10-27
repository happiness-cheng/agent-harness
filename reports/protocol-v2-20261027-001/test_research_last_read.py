"""历史失败启发的最后一次读取回归；固定策略只检验工程机制。"""
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

import pytest

import min_agent.application as application
from min_agent.application import HarnessApplication
from min_agent.model_context import ModelContextLimits
from min_agent.models import ModelResponse, ToolCall


CASES = [
    {'id':'psf-archived-excerpt', 'goal':'核对归档片段中的PSF使命短句；引用实际读到的文字，不补全未保存的全文。',
     'query':'Python Software Foundation mission statement official', 'url':'https://www.python.org/',
     'text':'The mission of the Python Software Foundation is to promote, protect',
     'classification':'historical_public_excerpt'},
    {'id':'warranty-exclusion', 'goal':'核对合成保修期与进水损坏例外，并引用原文。',
     'query':'合成产品 保修政策 进水例外', 'url':'https://fixtures.example/warranty',
     'text':'合成保修政策：自购买之日起18个月；进水损坏不在保修范围内。',
     'classification':'synthetic'},
    {'id':'api-timeout', 'goal':'核对合成API的超时和重试限制，保留副作用不确定性并引用原文。',
     'query':'合成API 超时 重试 规则', 'url':'https://fixtures.example/api',
     'text':'合成API规则：单次请求超时为12秒；仅允许重试一次，超时不代表服务端操作已撤销。',
     'classification':'synthetic'},
]


def decode(value):
    return json.loads(value) if isinstance(value, str) else value


class ResearchPolicy:
    """固定本地策略，遵守可见工具；不是新真实模型响应，也不参与评分。"""
    def __init__(self, case):
        self.case, self.calls = case, []

    def context_limits(self):
        return ModelContextLimits(model='fixed-local-research-policy', context_window=65536,
                                  max_output_tokens=4096, source='synthetic-test-capability')

    def generate(self, messages, *, tools=None):
        names = {tool['name'] for tool in tools or []}
        research = bool(messages and messages[0].content.startswith('你是当前任务中的只读研究助手。'))
        self.calls.append({'phase':'research' if research else 'main', 'tools':sorted(names),
                           'messages':[m.model_dump(mode='json', exclude={'provider_state'}) for m in messages]})
        if not research:
            returned = next((m for m in messages if m.role=='tool' and m.tool_call_id=='delegate'), None)
            if returned is not None:
                return ModelResponse(finish_reason='stop', content=json.dumps(decode(returned.content), ensure_ascii=False))
            return ModelResponse(finish_reason='tool_calls', tool_calls=[ToolCall(
                id='delegate', name='research', arguments={'question':self.case['goal']})])
        results = [decode(m.content) for m in messages if m.role=='tool']
        read = next((decode(row['result']) for row in results if row.get('ok') and
                     isinstance(decode(row.get('result')), dict) and 'artifact_id' in decode(row['result'])), None)
        if read is not None:
            evidence = {'conclusion':read['text'], 'citations':[{
                'url':read['source'], 'artifact_id':read['artifact_id'], 'start':read['start'],
                'end':read['end'], 'quote':read['text']}], 'limitations':['离线仅使用冻结片段，未重新抓取网页。'],
                'conflicts':[], 'uncertainties':[], 'next_queries':[]}
            return ModelResponse(finish_reason='stop', content=json.dumps(evidence, ensure_ascii=False))
        if not results and 'search' in names:
            return ModelResponse(finish_reason='tool_calls', tool_calls=[ToolCall(
                id='search-source', name='search', arguments={'query':self.case['query']})])
        if results and 'read_webpage' in names:
            data = decode(results[-1]['result'])
            return ModelResponse(finish_reason='tool_calls', tool_calls=[ToolCall(
                id='read-source', name='read_webpage', arguments={'url':data['results'][0]['url']})])
        return ModelResponse(finish_reason='stop', content=json.dumps({
            'conclusion':'尚未读取正文，不能确认。', 'citations':[],
            'limitations':['当前没有可调用的读取工具，未取证。'],
            'conflicts':[], 'uncertainties':[], 'next_queries':[]}, ensure_ascii=False))


def run_case(directory, case, monkeypatch, *, tools=3, models=7, allow_web=True):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    audit = {'searches':[], 'reads':[], 'fetches':0}
    model = ResearchPolicy(case)
    owner = uuid4()

    class Fetcher:
        def fetch(self, *args, **kwargs):
            audit['fetches'] += 1
            raise AssertionError('本实验禁止真实网络')
        def close(self):
            pass

    class Search:
        def __init__(self, **kwargs):
            pass
        def search(self, query):
            audit['searches'].append(query)
            return {'status':'ok', 'results':[{'url':case['url'], 'title':case['id'],
                                              'summary':'只提供定位入口，需实际读取。'}]}

    class Reader:
        def __init__(self, fetcher, artifacts):
            self.artifacts = artifacts
        def read(self, url, **kwargs):
            assert url==case['url'], '只能访问冻结来源'
            identity = self.artifacts.save(case['text'], source=url)
            value = self.artifacts.read(identity, limit=4000)
            audit['reads'].append(value)
            return value
        def read_artifact(self, identity, start=0, limit=4000):
            return self.artifacts.read(identity, start=start, limit=limit)

    with monkeypatch.context() as boundary:
        boundary.setattr(application, 'PublicFetcher', Fetcher)
        boundary.setattr(application, 'SearchService', Search)
        boundary.setattr(application, 'WebReader', Reader)
        app = HarnessApplication(directory/'workspace.sqlite3', model=model, memory_mode='off',
            owner_id=owner, total_models=models, segment_models=models, total_tools=tools, segment_tools=tools)
        try:
            identity = app.create_session('V1.1 '+case['id'])['session_id']
            if allow_web:
                app.allow_web(identity, True)
            app.submit(identity, case['goal']+'\nSYNTHETIC_MAIN_ONLY_MARKER：无关的合成主会话标记。')
            app.wait(identity, timeout=30)
            state = app.snapshot(identity)
        finally:
            app.close()

    # 关闭应用后重新只读打开实际业务库；评分不依赖模型自评或本地策略内部计数。
    with sqlite3.connect((directory/'workspace.sqlite3').as_uri()+'?mode=ro', uri=True) as connection:
        row = connection.execute('SELECT owner_id,snapshot_json FROM sessions WHERE session_id=?', (identity,)).fetchone()
        saved = json.loads(row[1])
        todos = connection.execute('SELECT COUNT(*) FROM todos').fetchone()[0]
    records = saved['messages']
    outcome = next((decode(m['content']) for m in records if m['role']=='tool' and m['tool_call_id']=='delegate'), {})
    packet = decode(outcome.get('result', {}))
    evidence = packet.get('evidence') or {}
    artifacts = saved['extensions'].get('artifacts', {})
    trace = saved['extensions'].get('last_trace', [])
    read_trace = [e for e in trace if e.get('tool_ref')==sha256(b'read_webpage').hexdigest()
                  and e['status']=='completed' and e['execution_state']=='returned']
    citations = evidence.get('citations', [])
    valid = bool(citations)
    for citation in citations:
        artifact = artifacts.get(citation['artifact_id'])
        valid = valid and artifact is not None and artifact['source']==case['url']
        valid = valid and artifact['content'][citation['start']:citation['end']]==citation['quote']==case['text']
    budget = saved['budgets'][saved['active_budget_id']]['state']
    remaining_read_visible = any(call['phase']=='research' and 'read_webpage' in call['tools'] and
        any(m['role']=='tool' for m in call['messages']) for call in model.calls)
    checks = {'task_completed':saved['task']['status']=='completed',
        'actual_read_and_artifact':bool(read_trace) and bool(artifacts),
        'citation_matches_frozen_original':bool(valid),
        'same_owner':row[0]==str(owner), 'no_business_writes':todos==0,
        'budget_within_limits':budget['usage']['model_calls']<=models and budget['usage']['tool_calls']<=tools,
        'no_real_network':audit['fetches']==0,
        'research_did_not_receive_main_history':all('SYNTHETIC_MAIN_ONLY_MARKER' not in json.dumps(call['messages'])
            for call in model.calls if call['phase']=='research')}
    result = {'case_id':case['id'], 'classification':case['classification'],
        'verdict':'PASS' if all(checks.values()) else 'FAIL', 'checks':checks,
        'read_available_after_search':remaining_read_visible,
        'execution_status':saved['task']['status'], 'limits':budget['limits'], 'usage':budget['usage'],
        'audit':audit, 'evidence':evidence, 'trace':trace, 'session_id':identity,
        'owner_id':str(owner), 'fixed_policy_calls':model.calls, 'external_generation_requests':0}
    (directory/'case.json').write_text(json.dumps(case, ensure_ascii=False, indent=2), encoding='utf-8')
    (directory/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


@pytest.mark.parametrize('case', CASES, ids=lambda case:case['id'])
def test_last_available_tool_can_read_source_before_final_evidence(tmp_path, monkeypatch, case):
    result = run_case(tmp_path/'run', case, monkeypatch)
    assert result['verdict']=='PASS', result['checks']
    assert result['read_available_after_search']
    assert result['usage']['tool_calls']==result['limits']['tool_calls']==3


@pytest.mark.parametrize(('options', 'expected'), [
    ({'allow_web':False}, 'waiting_user'),
    ({'tools':0}, 'partial'),
    ({'models':2}, 'partial'),
])
def test_insufficient_resources_or_missing_authorization_never_read(tmp_path, monkeypatch, options, expected):
    result = run_case(tmp_path/'run', CASES[1], monkeypatch, **options)
    assert result['execution_status']==expected
    assert result['verdict']=='FAIL' and not result['audit']['reads']
    assert result['checks']['budget_within_limits'] and result['checks']['no_business_writes']
    assert result['checks']['no_real_network']
