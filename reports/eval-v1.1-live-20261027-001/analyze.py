"""只读汇总本批真实运行；只导出明文投影，不导出原生协议/隐藏推理。"""
from collections import Counter
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sqlite3


ROOT = Path(__file__).resolve().parent
OUT = ROOT/'analysis'
OUT.mkdir(exist_ok=False)
batch = json.loads((ROOT/'batch.json').read_text(encoding='utf-8'))
assert len(batch['completed'])==6 and not batch['stopped_on_error']
rows, usage, owners, sessions = [], [], set(), set()
answers = ['# 六项真实任务的实际回答', '', '仅含本批公开/合成任务，原生协议状态与隐藏推理未导出。', '']
spent = Decimal('0')
reserved = Decimal('0')
for variant in ('baseline','candidate'):
    for index in range(3):
        name = variant+'-'+str(index)
        directory = ROOT/name
        receipt = json.loads((directory/'live-receipt.json').read_text(encoding='utf-8'))
        observation = json.loads((directory/'result.json').read_text(encoding='utf-8'))
        ledger = json.loads((ROOT/(name+'-cost.json')).read_text(encoding='utf-8'))
        with sqlite3.connect((directory/'workspace.sqlite3').as_uri()+'?mode=ro', uri=True) as con:
            owner, snapshot = con.execute('SELECT owner_id,snapshot_json FROM sessions WHERE session_id=?',
                                          (observation['session_id'],)).fetchone()
            snapshot = json.loads(snapshot)
            todos = con.execute('SELECT COUNT(*) FROM todos').fetchone()[0]
        projection = [{key:message[key] for key in ('role','content','tool_call_id','tool_calls') if key in message}
                      for message in snapshot['messages']]
        actions = [call for message in projection for call in message.get('tool_calls',[])]
        names = Counter(call['name'] for call in actions)
        final = next((m['content'] for m in reversed(projection) if m['role']=='assistant' and not m.get('tool_calls')), '')
        checkpoint = snapshot.get('checkpoint') or {}
        checkpoint_info = {key:checkpoint.get(key) for key in ('reason','closing_status','closing_error','model_note')}
        actual = snapshot['budgets'][snapshot['active_budget_id']]['state']
        assert actual['usage']==observation['usage']
        assert actual['usage']['model_calls']==receipt['generation_requests']==len(receipt['usage'])
        assert receipt['backend']=='LIVE_PROVIDER_FROZEN_IO' and not receipt['provider_errors']
        assert owner==observation['owner_id'] and todos==0
        assert receipt['verdict']==observation['verdict']=='FAIL'
        assert not any(names.get(key) for key in ('research','search','read_webpage'))
        assert not snapshot['extensions'].get('artifacts')
        assert actual['usage']['model_calls']<=7 and actual['usage']['tool_calls']<=3
        assert ledger['limit']==.25 and not ledger['reservations'] and not ledger.get('breach', False)
        owners.add(owner)
        sessions.add(observation['session_id'])
        usage += receipt['usage']
        spent += Decimal(str(ledger['spent_upper']))
        reserved += sum((Decimal(str(v)) for v in ledger['reservations'].values()), Decimal('0'))
        item = {'run':name,'case':receipt['case'],'verdict':receipt['verdict'],
            'execution_status':snapshot['task']['status'],'task_error':snapshot['task']['error_code'],
            'generation_requests':receipt['generation_requests'],'tool_usage':actual['usage']['tool_calls'],
            'requested_actions':dict(names),'research_invocations':0,'reads':0,'artifacts':0,
            'spent_upper_usd':str(ledger['spent_upper']),'reserved_usd':0,
            'final_answer_present':bool(final),'checkpoint':checkpoint_info}
        rows.append(item)
        (OUT/(name+'-transcript.json')).write_text(json.dumps({'run':name,'messages':projection,
            'final_answer':final,'checkpoint':checkpoint_info,'provider_state_exported':False},
            ensure_ascii=False,indent=2),encoding='utf-8')
        answers += ['## '+name+' · '+receipt['case'], '',
            '执行状态：'+item['execution_status']+'；独立判定：FAIL；生成请求：'+str(receipt['generation_requests']), '',
            final if final else '没有保存普通最终答复。原记录为预算检查点，错误代码：'+str(item['task_error'])+
                '；收尾错误：'+str(checkpoint_info.get('closing_error')), '']
assert len(owners)==len(sessions)==6
inputs = sum(row['input_tokens'] for row in usage)
outputs = sum(row['output_tokens'] for row in usage)
cached = sum(row.get('cached_input_tokens',0) for row in usage)
assert len(usage)==14 and len(usage)<=42 and spent<=Decimal('1.5') and not reserved
summary = {'outcome':'NO_VERIFIED_GAIN','reason':'TREATMENT_NOT_REACHED_SOURCE_DISCOVERY_MISSING',
    'runs':rows,'pairs':3,'baseline_pass':0,'candidate_pass':0,'task_fail':6,'execution_completed':5,
    'execution_partial':1,'research_invocations':0,'searches':0,'reads':0,
    'generation_requests':len(usage),'input_tokens':inputs,'output_tokens':outputs,'cached_input_tokens':cached,
    'cost_ledger_spent_upper_usd':str(spent),'pending_reserved_usd':str(reserved),'invoice_usd':None,
    'unique_owners':len(owners),'unique_sessions':len(sessions),'business_writes':0,
    'budget_violations':0,'provider_errors':0,'auto_retries':0,'codex_cli_invocations':0,
    'source_code_regression_confirmed':False,'first_request_equivalence':'see independent-live-audit.json',
    'frozen_criteria_changed':False,'no_more_generation_authorized_by_unused_request_capacity':True,
    'interpretation':'授权六任务已完成；固定策略知道case query，但真实模型未得到资料入口。不得把该混杂归因为候选代码失效或真实能力退化。'}
(OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
(OUT/'answers.md').write_text('\n'.join(answers),encoding='utf-8')
print(json.dumps({'outcome':summary['outcome'],'runs':6,'generation_requests':len(usage),
    'verdicts':{'baseline':'0/3 PASS','candidate':'0/3 PASS'},'input_tokens':inputs,'output_tokens':outputs,
    'cached_input_tokens':cached,'cost_ledger_upper_usd':str(spent),'reserved_usd':str(reserved)},ensure_ascii=False))
