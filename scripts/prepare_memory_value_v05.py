"""准备可复核公开请求包，不导入在线客户端，不读取凭据、不执行模型。"""
import argparse
from datetime import datetime,timezone
import gzip
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from eval_memory_quality_v03 import load_inputs
from min_agent.long_memory.answer_eval import prepare_answer_case,encoded


def synthetic():
    return [
        dict(test_id='v05_current_instruction',category='synthetic',user_question='只用一句中文告诉我，记录中已经确认的项目代号是什么？不要按以前的偏好写长篇英文。',
            conversation_histories=[dict(conversation_id='preferences',timestamp='2024-10-01T00:00:00Z',messages=[
                dict(role='user',content='I prefer detailed answers in English.'),dict(role='user',content='项目代号已经确认是 ORCHID-17。')])]),
        dict(test_id='v05_temporal_state',category='synthetic',user_question='截至2024年10月5日，设备D7当前是什么状态？',
            conversation_histories=[dict(conversation_id='old',timestamp='2024-10-01T00:00:00Z',messages=[dict(role='user',content='设备 D7 的状态是待审核。')]),
                dict(conversation_id='cancel',timestamp='2024-10-03T00:00:00Z',messages=[dict(role='user',content='确认：设备D7已取消，不再是待审核状态。')]),
                dict(conversation_id='future',timestamp='2024-10-10T00:00:00Z',messages=[dict(role='user',content='设备D7已经恢复，状态为处理中。')])]),
        dict(test_id='v05_missing_memory',category='synthetic',user_question='我之前记录过的门禁密码是什么？如果没有记录请直接说明，不要猜。',
            conversation_histories=[dict(conversation_id='project',timestamp='2024-10-01T00:00:00Z',messages=[dict(role='user',content='项目代号是 ORCHID-17。')])]),
    ]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--book-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():parser.error('不覆盖已有请求包')
    base=args.book_root/'chapter3/user-memory-evaluation'
    if not base.is_dir():base=args.book_root
    inherited,cases,splits=load_inputs(base)
    gold={identity:value for _,_,values in splits for identity,value in values.items() if identity!='_comment'}
    recipes={
        'layer1_01_bank_account':[(0,61)],
        'layer1_08_hotel_reservation':[(0,69),(0,41),(0,81)],
        'layer2_01_multiple_vehicles':[(0,5),(0,6),(1,4),(1,65),(1,83),(1,89)],
        'layer2_03_multiple_credit_cards':[(0,9),(0,11),(0,21),(1,1),(1,7),(1,9),(1,16),(1,35),(2,8),(2,17)],
        'layer3_01_travel_coordination':[(0,5),(0,6),(1,9),(1,11),(1,51),(2,27),(2,31)],
        'layer3_03_home_purchase_coordination':[(0,57),(1,19),(1,30),(1,32),(1,13),(1,15),(2,46),(2,47),(2,49),(2,87)],
    }
    rows=[]
    for identity,positions in recipes.items():
        case=cases[identity]
        spec=[dict(conversation_id=case['conversation_histories'][h]['conversation_id'],message_index=i) for h,i in positions]
        prepared=prepare_answer_case(case,oracle_spec=spec)
        prepared['origin']='public_book';prepared['gold']=gold[identity]
        prepared['book_evaluation_criteria']=case['evaluation_criteria']
        prepared['book_expected_behavior']=case.get('expected_behavior')
        prepared['oracle_adequacy']='selected source facts only; full original rubric requires source review'
        prepared['evaluation_audit']=[]
        if identity=='layer2_03_multiple_credit_cards':
            prepared['evaluation_audit']=['Amex Gold is cross-sentence composition; literal-only matching is insufficient.',
                'Original rubric asserts zero Amex foreign fees but the selected historical call has no foreign-fee statement; do not treat unsupported external rules as user history.']
        if identity=='layer2_01_multiple_vehicles':
            prepared['evaluation_audit']=['Record says Friday November 24 while case year is 2024; calendar inconsistency must be reviewed, not silently corrected.']
        if identity=='layer3_01_travel_coordination':
            prepared['evaluation_audit']=['Original rubric passport-validity/entry rules are external knowledge, not established by these source facts; do not infer country law from generic advice.']
        if identity=='layer3_03_home_purchase_coordination':
            prepared['evaluation_audit']=['As-of is 2024-11-18; November 20 preapproval has NOT yet expired. Original rubric saying already expired conflicts with legal task time.']
        rows.append(prepared)
    synth=synthetic()
    synth_specs=[[(0,0),(0,1)],[(0,0),(1,0)],[]]
    synth_gold=[['ORCHID-17'],['已取消'],[]]
    for case,positions,facts in zip(synth,synth_specs,synth_gold):
        at=datetime(2024,10,5,tzinfo=timezone.utc) if case['test_id']=='v05_temporal_state' else None
        spec=[dict(conversation_id=case['conversation_histories'][h]['conversation_id'],message_index=i) for h,i in positions]
        prepared=prepare_answer_case(case,oracle_spec=spec,as_of=at)
        prepared.update(origin='approved_synthetic',gold=dict(required_facts=facts),
            book_evaluation_criteria=None,oracle_adequacy='source-complete controlled synthetic',evaluation_audit=[])
        rows.append(prepared)
    requests=sum(len(row['groups']) for row in rows)
    peak=sum(g['input_upper_bound']*0.30/1_000_000+g['max_output_tokens']*1.20/1_000_000 for row in rows for g in row['groups'].values())
    source_files=sorted((ROOT/'src/min_agent').rglob('*.py'))+[Path(__file__).resolve()]
    result=dict(protocol='v05 controlled first-response A/B/C; no tools executed',
        base_commit='5b9e8401810d0fc8d8ee39db333dd334ead72ff7',
        head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        source_hashes={p.relative_to(ROOT).as_posix():sha256(p.read_bytes()).hexdigest() for p in source_files},
        original_book_manifest=inherited['book_manifest'],original_gold_sha256=inherited['original_gold_sha256'],
        cases=rows,request_count=requests,model='deepseek-flash',reasoning_effort='high',
        max_output_tokens=4096,total_budget=32768,reference_budget=4096,tools=[],
        official_price_source='https://api-docs.deepseek.com/quick_start/pricing/#model-details',
        pricing_checked='2026-10-25',peak_usd_per_million=dict(input=0.30,output=1.20),
        worst_case_estimate_usd=round(peak,6),proposed_fee_cap_usd=1.0,
        proposed_credentials_file='C:/Users/陈独秀/agent-harness-credentials.json',proposed_proxy='http://127.0.0.1:7890',
        external_judge=False,live_model_calls=0,answers='NOT_RUN',scores='NOT_RUN',
        scope_note='raw Episode approved_fixture storage; production automatic first view; not autonomous multi-turn Agent or real extraction quality')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('xb') as f:f.write(gzip.compress((json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode(),mtime=0))
    print(json.dumps(dict(cases=len(rows),requests=requests,estimated_worst_case_usd=round(peak,6),
        pack_sha256=sha256(args.output.read_bytes()).hexdigest(),reference_bytes={r['case_id']:{g:x['reference_bytes'] for g,x in r['groups'].items()} for r in rows},
        credentials_read=False,model_calls=0),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
