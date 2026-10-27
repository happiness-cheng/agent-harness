"""重新检查落盘证据，产生目录/首轮输入差异和完整性证明。"""
from hashlib import sha256
import json
from pathlib import Path
import sys
from protocol import CASES,DOCUMENTS,CONTRACT_HASH

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0,str(HERE/'candidate/workspace/src'))
from min_agent.evaluation.worker import source_version
from preflight import reachability,save

def load(path):
    return json.loads(path.read_text(encoding='utf-8'))

def normalized(first):
    # Session附件ID由ArtifactStore随机创建，只归一化两份相对应的引用，保留其余字段逐字比较。
    value=json.loads(json.dumps(first))
    for schema in value['schemas']:
        if 'RESOURCE_CATALOG_JSON:' in schema.get('description',''):
            prefix,catalog=schema['description'].split('RESOURCE_CATALOG_JSON:',1)
            catalog=json.loads(catalog)
            for item in catalog['session_artifacts']:
                item['artifact_id']='<corresponding-session-artifact>'
            schema['description']=prefix+'RESOURCE_CATALOG_JSON:'+json.dumps(catalog,ensure_ascii=False)
    return {'messages':value['messages'],'schemas':value['schemas']}

def main():
    before=load(HERE/'main-worktree-before.json')
    old=load(HERE/'historical-hashes-before.json')
    protection={'main_source_unchanged':source_version(ROOT)==before['source_version'],
        'main_files_changed':[p for p,h in before['files'].items() if not (ROOT/p).is_file() or sha256((ROOT/p).read_bytes()).hexdigest()!=h],
        'old_evidence_changed':[p for p,h in old.items() if not (ROOT/p).is_file() or sha256((ROOT/p).read_bytes()).hexdigest()!=h],
        'old_evidence_files_checked':len(old),'external_generation_requests':0}
    save(HERE/'integrity.json',protection)
    pairs=[]
    run=HERE/'preflight-02'
    for case in CASES:
        a,b=[load(run/v/case['id']/'model-visible.json') for v in ('baseline','candidate')]
        results=[load(run/v/case['id']/'result.json') for v in ('baseline','candidate')]
        first=normalized(a[0])
        text=json.dumps(first,ensure_ascii=False)
        checks={'equal_first_input':first==normalized(b[0]),
                'no_body_or_gold_in_first_input':all(doc['content'] not in text for doc in DOCUMENTS)
                    and all(key not in text for key in ('task_criterion','reach_criterion','repair_criterion')),
                'same_limits':results[0]['limits']==results[1]['limits'],
                'no_provider_state':all('provider_state' not in json.dumps(call,ensure_ascii=False) for call in a+b),
                'no_generation':all(r['external_generation_requests']==0 for r in results),
                'safety':all(all(r['checks'].values()) for r in results)}
        if case['group']=='normal':
            checks['boundary_expected']=all(r['hypothesis_reachability']['value']=='REACHED' for r in results)
            checks['reference_expected']=[r['task_outcome'] for r in results]==['FAIL','PASS']
        if case['group']=='negative':
            checks['not_forced_research']=all(not any(c['phase']=='research' for c in calls) for calls in (a,b))
            checks['negative_expected']=all(r['task_outcome']=='PASS' for r in results)
        if case['mode']=='stop_after_search':
            checks['reach_is_not_success']=all(r['task_outcome']=='FAIL' and r['hypothesis_reachability']['value']=='REACHED' for r in results)
        if case['mode']=='direct_read':
            checks['success_does_not_imply_reach']=all(r['task_outcome']=='PASS' and r['hypothesis_reachability']['value']=='NOT_REACHED' for r in results)
        if case['mode']=='permission_probe':
            checks['permission_preserved']=all(r['execution_status']=='waiting_user' and not r['actual_read'] for r in results)
        if case['group']=='budget_control':
            checks['budget_preserved']=all(not r['actual_read'] and r['task_outcome']=='FAIL' for r in results)
        pairs.append({'task_id':case['id'],'group':case['group'],'checks':checks,
            'baseline':{'task':results[0]['task_outcome'],'reach':results[0]['hypothesis_reachability']},
            'candidate':{'task':results[1]['task_outcome'],'reach':results[1]['hypothesis_reachability']}})
        if case['group']=='normal':
            save(HERE/(case['id']+'-first-input.json'),first)
    calls=load(run/'candidate/v2.normal.warranty/model-visible.json')
    broken=json.loads(json.dumps(calls))
    for call in broken:
        if call['phase']=='research': call.pop('budget_record',None)
    unknown=reachability(broken)['value']=='UNKNOWN'
    summary={'contract_hash':CONTRACT_HASH,'pairs':pairs,'missing_observation_is_unknown':unknown,
        'protocol_preflight':'PASS' if all(all(p['checks'].values()) for p in pairs) and unknown and
            protection['main_source_unchanged'] and not protection['main_files_changed'] and not protection['old_evidence_changed'] else 'FAIL',
        'repair_outcome':'INCONCLUSIVE','previous_live_repair_outcome':'NO_VERIFIED_GAIN','external_generation_requests':0,
        'sources':{v:source_version(HERE/v/'workspace') for v in ('baseline','candidate')}}
    save(HERE/'preflight-summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    assert summary['protocol_preflight']=='PASS'

if __name__=='__main__':main()
