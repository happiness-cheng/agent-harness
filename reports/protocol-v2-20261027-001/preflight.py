"""仅本地固定参考策略，经真实应用公开入口和真实Runtime；没有live模式。"""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4
from protocol import CASES, DOCUMENTS, CONTRACT, CONTRACT_HASH, TASK_HASHES

HERE = Path(__file__).resolve().parent

def save(path, value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

def decode(value):
    return json.loads(value) if isinstance(value,str) else value

def evidence(read=None):
    return {'conclusion':read['text'] if read else '未读取原文，不能确认。',
            'citations':[{'url':read['source'],'artifact_id':read['artifact_id'],
                          'start':read['start'],'end':read['end'],'quote':read['text']}] if read else [],
            'limitations':['仅依据受控资料，历史片段不代表全文或最新版本。'],
            'conflicts':[],'uncertainties':[],'next_queries':[]}

class ReferencePolicy:
    """策略只拿mode，不能访问case/document/Gold；从实际可见Schema与工具结果选资源。"""
    def __init__(self, mode):
        self.mode = mode

    def generate(self, messages, *, tools=None):
        from min_agent.models import ModelResponse, ToolCall
        def stop(value):
            return ModelResponse(finish_reason='stop',content=json.dumps(value,ensure_ascii=False))
        def call(identity,name,arguments):
            return ModelResponse(finish_reason='tool_calls',tool_calls=[ToolCall(id=identity,name=name,arguments=arguments)])
        names = {tool['name'] for tool in tools or []}
        inner = messages[0].content.startswith('你是当前任务中的只读研究助手。')
        results = [decode(message.content) for message in messages if message.role=='tool']
        if not inner:
            if results:
                return stop(decode(results[-1].get('result',results[-1])))
            schema = next((tool for tool in tools or [] if tool['name']=='research'),{})
            description = schema.get('description','')
            catalog = json.loads(description.split('RESOURCE_CATALOG_JSON:',1)[1]) if 'RESOURCE_CATALOG_JSON:' in description else {}
            attached = catalog.get('session_artifacts',[])
            if attached:
                return call('read-session','read_artifact',{'artifact_id':attached[0]['artifact_id']})
            docs = (catalog.get('controlled_collection') or {}).get('documents',[])
            if not docs:
                return stop({'status':'materials_unavailable','message':'当前未提供所需材料，请提供原文或可访问的定位。'})
            if not catalog.get('allow_public_web') and self.mode!='permission_probe':
                return stop({'status':'permission_required','message':'当前研究入口尚未授权。'})
            doc = docs[0]
            objective = '请核对并引用资料：'+doc['title']+'。来源定位：'+doc['locator']+'。覆盖范围：'+doc['coverage']
            return call('delegate','research',{'question':objective})
        decoded = [decode(row.get('result',{})) for row in results if row.get('ok')]
        read = next((row for row in decoded if isinstance(row,dict) and 'artifact_id' in row),None)
        if read:
            return stop(evidence(read))
        objective = next((decode(m.content)['objective'] for m in messages if m.role=='user' and m.content.startswith('{') and 'objective' in decode(m.content)), '')
        if not decoded and self.mode=='direct_read' and 'read_webpage' in names:
            locator = objective.split('来源定位：',1)[1].split('。',1)[0]
            return call('read-source','read_webpage',{'url':locator})
        if not decoded and 'search' in names:
            return call('search-source','search',{'query':objective})
        search = next((row for row in decoded if isinstance(row,dict) and row.get('results')),None)
        if search and self.mode!='stop_after_search' and 'read_webpage' in names:
            return call('read-source','read_webpage',{'url':search['results'][0]['url']})
        return stop(evidence())

class Recorder:
    def __init__(self, mode):
        self.policy,self.calls = ReferencePolicy(mode),[]
        self.app,self.session_id = None,None

    def context_limits(self):
        from min_agent.model_context import ModelContextLimits
        return ModelContextLimits(model='offline-visible-input-reference',context_window=65536,
                                  max_output_tokens=4096,source='synthetic-local-reference-only')

    def generate(self, messages, *, tools=None):
        from min_agent.context import request_bound
        snapshot = self.app.sessions.snapshot(self.app.context(self.session_id))
        record = snapshot.budgets[snapshot.active_budget_id]
        entry = {'phase':'research' if messages[0].content.startswith('你是当前任务中的只读研究助手。') else 'main',
            'messages':[m.model_dump(mode='json',exclude={'provider_state'}) for m in messages],
            'schemas':tools or [],'budget_record':record.model_dump(mode='json'),
            'request_bound':request_bound(messages,tools,model=self), 'reserve':4096,
            'owner_id':str(snapshot.owner_id) if hasattr(snapshot,'owner_id') else str(self.app.owner_id),
            'session_id':self.session_id}
        self.calls.append(entry)
        response = self.policy.generate(messages,tools=tools)
        entry['response'] = response.model_dump(mode='json',exclude={'provider_state'})
        return response

def reachability(calls):
    unknown = False
    for index,call in enumerate(calls):
        if call.get('phase')!='research':
            continue
        record = call.get('budget_record',{})
        if not record or record.get('owner_id')!=call.get('owner_id') or record.get('session_id')!=call.get('session_id'):
            unknown=True
            continue
        state = record['state']
        children = [(key,node) for key,node in state['segments'].items() if node['purpose']=='research' and node['parent_id']]
        if len(children)!=1:
            unknown=True
            continue
        nodes = [state]
        node = children[0][1]
        while node:
            nodes.append(node)
            node = state['segments'].get(node['parent_id']) if node.get('parent_id') else None
        remaining = {key:min(node['limits'][key]-node['usage'][key] for node in nodes)
                     for key in ('tool_calls','model_calls')}
        decoded = [decode(m['content']) for m in call['messages'] if m['role']=='tool']
        successful = [decode(row.get('result',{})) for row in decoded if row.get('ok')]
        searched = any(isinstance(row,dict) and row.get('status')=='ok' and row.get('results') for row in successful)
        read = any(isinstance(row,dict) and 'artifact_id' in row for row in successful)
        if searched and not read and remaining['tool_calls']==1 and remaining['model_calls']>1:
            return {'value':'REACHED','dispatch':index,'remaining':remaining,
                    'read_tool_exposed':any(t['name']=='read_webpage' for t in call['schemas']),
                    'basis':'actual bound BudgetRecord after current dispatch debit, successful search, no read'}
    return {'value':'UNKNOWN' if unknown else 'NOT_REACHED'}

def run_case(variant, case, out):
    from min_agent.application import HarnessApplication
    from min_agent.artifacts import ArtifactStore
    from min_agent.research_resources import ResearchCollection,ResearchDocument
    directory = out/case['id']
    directory.mkdir(parents=True,exist_ok=False)
    model,owner = Recorder(case['mode']),uuid4()
    models,tools = case.get('models',7),case.get('tools',3)
    app = HarnessApplication(directory/'workspace.sqlite3',model=model,owner_id=owner,
        memory_mode='off',total_models=models,segment_models=models,total_tools=tools,segment_tools=tools)
    model.app = app
    try:
        sid = app.create_session(case['id'])['session_id']
        model.session_id = sid
        doc = DOCUMENTS[case['document']] if case['document'] is not None else None
        docs = () if doc is None or case['mode']=='session_attachment' else (ResearchDocument(**doc),)
        if case['mode']=='session_attachment':
            ArtifactStore(app.sessions,app.context(sid)).save(doc['content'],source=doc['locator'],kind='attachment')
        app.mount_research_collection(sid,ResearchCollection('v2-controlled-library','V2受控公开片段与合成文档库',docs))
        if case.get('allow_web',True):
            app.allow_web(sid,True)
        catalog = app.research_resources(sid)
        app.submit(sid,case['goal'])
        app.wait(sid,timeout=30)
        state = app.snapshot(sid)
    finally:
        app.close()
    # 独立验证只读重新打开实际数据库。策略自己的回答/计数不能决定分数。
    with sqlite3.connect((directory/'workspace.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        row = db.execute('SELECT owner_id,snapshot_json FROM sessions WHERE session_id=?',(sid,)).fetchone()
        saved = json.loads(row[1])
        todos = db.execute('SELECT COUNT(*) FROM todos').fetchone()[0]
    messages = saved['messages']
    trace = saved['extensions'].get('last_trace',[])
    artifacts = saved['extensions'].get('artifacts',{})
    outputs = [decode(m['content']) for m in messages if m['role']=='tool']
    packet = next((decode(m.get('result',{})) for m in outputs if isinstance(decode(m.get('result',{})),dict)
                   and 'evidence' in decode(m.get('result',{}))),{})
    citations = (packet.get('evidence') or {}).get('citations',[])
    final = next((m['content'] for m in reversed(messages) if m['role']=='assistant' and not m.get('tool_calls')), '')
    read_completed = any(e.get('tool_ref')==sha256(b'read_webpage').hexdigest() and
                         e.get('status')=='completed' and e.get('execution_state')=='returned' for e in trace)
    valid = bool(citations) and read_completed
    for citation in citations:
        artifact = artifacts.get(citation['artifact_id'],{})
        valid = bool(valid and artifact.get('source')==doc['locator'] and citation['url']==doc['locator']
            and artifact.get('content','')[citation['start']:citation['end']]==citation['quote']==doc['content'])
    if case['mode']=='session_attachment':
        valid = bool(outputs and outputs[0].get('ok') and decode(outputs[0].get('result',{})).get('text')==doc['content']
                     and doc['content'] in final)
    if doc is None:
        valid = 'materials_unavailable' in final and not outputs and not artifacts
    budget = saved['budgets'][saved['active_budget_id']]['state']
    safe = {'correct_owner':row[0]==str(owner),'no_business_writes':todos==0,
            'budget_within_limits':all(budget['usage'][key]<=budget['limits'][key] for key in ('model_calls','tool_calls')),
            'scoped_artifacts':all(a['source']==doc['locator'] for a in artifacts.values()) if doc else not artifacts,
            'bounded_context':all(call['request_bound']+call['reserve']+1024<=65536 for call in model.calls)}
    verdict = 'PASS' if valid and saved['task']['status']=='completed' and all(safe.values()) else 'FAIL'
    reach = reachability(model.calls)
    result = {'variant':variant,'task_id':case['id'],'group':case['group'],'contract_hash':CONTRACT_HASH,
        'task_hash':TASK_HASHES[case['id']],'task_outcome':verdict,'hypothesis_reachability':reach,
        'repair_outcome':'INCONCLUSIVE','execution_status':saved['task']['status'],
        'catalog':catalog,'checks':safe,'limits':budget['limits'],'usage':budget['usage'],
        'actual_read':read_completed,'evidence':packet.get('evidence'),'final':final,
        'owner_id':str(owner),'session_id':sid,'artifact_count':len(artifacts),
        'external_generation_requests':0,'local_reference_dispatches':len(model.calls),
        'no_live_backend':'ResearchCollection is mounted via public API; no PublicFetcher/SearchService fallback'}
    save(directory/'model-visible.json',model.calls)
    save(directory/'trace.json',trace)
    save(directory/'result.json',result)
    return result

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker',choices=['baseline','candidate'])
    parser.add_argument('--out',type=Path,required=True)
    args = parser.parse_args()
    args.out = args.out.resolve()
    if args.worker:
        sys.path.insert(0,str(HERE/args.worker/'workspace/src'))
        args.out.mkdir(parents=True,exist_ok=False)
        results = [run_case(args.worker,case,args.out) for case in CASES]
        save(args.out/'results.json',results)
        print(json.dumps([{'task':r['task_id'],'task_outcome':r['task_outcome'],'reach':r['hypothesis_reachability']} for r in results],ensure_ascii=False))
        return
    sys.path.insert(0,str(HERE/'baseline/workspace/src'))
    from min_agent.evaluation.processes import run_owned
    args.out.mkdir(parents=True,exist_ok=False)
    save(args.out/'contract.json',dict(CONTRACT,contract_hash=CONTRACT_HASH,task_hashes=TASK_HASHES))
    for variant in ('baseline','candidate'):
        result = run_owned([sys.executable,'-X','utf8',str(Path(__file__).resolve()),'--worker',variant,
                            '--out',str(args.out/variant)],capture_output=True,timeout=180,env=dict(os.environ))
        (args.out/(variant+'-worker.txt')).write_bytes(result.stdout+result.stderr)
        if result.returncode:
            raise RuntimeError(f'{variant} preflight failed: see worker output')
    print('zero-generation preflight completed; compare saved independent outcomes')

if __name__=='__main__':
    main()
