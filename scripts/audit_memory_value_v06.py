"""执行代理的非盲来源审查汇总；不调用Judge，不把词面代理冒充语义判断。"""
import argparse
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from eval_memory_value_v06 import load,surface
from report_memory_value_v06 import text_of


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():raise FileExistsError('不覆盖审查记录')
    out=ROOT/'reports/memory_v06'
    pack=load(out/'prepared-client-fix.json.gz')
    result=json.loads((out/'live-client-fix.json').read_text(encoding='utf-8'))
    decisions=json.loads((out/'source-audit-decisions.json').read_text(encoding='utf-8'))
    assert sha256((out/'prepared-client-fix.json.gz').read_bytes()).hexdigest()==result['pack_sha256']
    cases={c['case_id']:c for c in pack['cases']};reviewed=[]
    totals=defaultdict(lambda:dict(required_fact_hits=0,scored_required_facts=0,not_scored_facts=0,
        provided_required_facts=0,used_provided_facts=0,valid_final_answers=0,
        core_answers_audited=0,core_answers_without_flagged_unsupported_claim=0,flagged_unsupported_claims=0,
        disambiguation_findings=0))
    for cell in result['cells']:
        identity=cell['case_id'];group=cell['group'];case=cases[identity];facts=case['gold']['required_facts']
        split='public_dev' if identity.startswith('layer') else case['split']
        total=totals[split+'/'+group]
        record=dict(case_id=identity,group=group,reviewer=decisions['reviewer'],
            case_note=decisions['case_notes'][identity],answer_sha256=sha256(cell['raw_answer'].encode()).hexdigest() if cell.get('raw_answer') else None)
        if not cell['validation']['schema_valid']:
            total['not_scored_facts']+=len(facts)
            record.update(status='NOT_RUN_NO_VALID_FINAL',fact_matches='NOT_RUN',core_answer_support='NOT_RUN',
                reason=cell.get('error_code') or cell['validation']['status'])
            reviewed.append(record);continue
        answer=cell['validation']['parsed']['answer']
        used=[bool(x) for x in decisions['used_fact_matches'].get(identity,{}).get(group,[0]*len(facts))]
        assert len(used)==len(facts)
        available=[surface(text_of(cell['final_reference']),f) for f in facts]
        key=identity+'/'+group
        if key in decisions['available_fact_overrides']:
            available=[bool(x) for x in decisions['available_fact_overrides'][key]]
        assert all(not u or a for u,a in zip(used,available)),(key,used,available)
        unsupported=[dict(x) for x in decisions['extra_assertion_checks'] if x['case_id']==identity and x['group']==group]
        for claim in unsupported:
            start=answer.casefold().find(claim['needle'].casefold())
            assert start>=0,(key,claim['needle'])
            claim['answer_quote']=answer[start:start+len(claim['needle'])]
        citations=cell['validation']['parsed']['used_evidence_ids']
        legal={m['id']:m for m in case['source_episodes']}
        evidence=[]
        for mid,m in legal.items():
            if mid in citations or set(m['evidence_refs'])&set(citations):
                evidence.append(dict(memory_id=mid,source_position=case['source_positions'][mid],
                    observed_at=m['observed_at'],quote=m['content']))
        issues=[x for x in decisions['disambiguation_findings'] if x['case_id']==identity and x['group']==group]
        record.update(status='SOURCE_REVIEWED',fact_matches=used,provided_fact_matches=available,
            used_given_facts=sum(u and a for u,a in zip(used,available)),given_facts=sum(available),
            core_answer_support='N/A_NO_MEMORY_FACT_ASSERTIONS' if group=='A' else 'FAIL_FLAGGED_CORE_ASSERTION' if unsupported else 'NO_UNSUPPORTED_CORE_ASSERTION_FOUND',
            support_scope='answer-level source review of decision-relevant core assertions; not exhaustive world fact checking or task success',
            unsupported_assertions=unsupported,disambiguation_findings=issues,
            cited_source_quotes=evidence,citation_validation=cell['validation']['citation_valid'],
            communication_findings=[x for x in decisions['communication_constraint_findings'] if x['case_id']==identity and x['group']==group])
        total['valid_final_answers']+=1;total['required_fact_hits']+=sum(used);total['scored_required_facts']+=len(facts)
        total['provided_required_facts']+=sum(available);total['used_provided_facts']+=record['used_given_facts']
        total['flagged_unsupported_claims']+=len(unsupported);total['disambiguation_findings']+=len(issues)
        if group!='A':
            total['core_answers_audited']+=1
            total['core_answers_without_flagged_unsupported_claim']+=not unsupported
        reviewed.append(record)
    value=dict(reviewer=decisions['reviewer'],definition=decisions['definition'],rows=reviewed,summary=dict(totals),
        limitation='judgments are implementer source audit, not blind independent evaluation; no Gold/answer fed into decision algorithms',
        real_model_calls=result['model_calls'],cost_peak_usd=result['cost_peak_usd'])
    with args.output.open('x',encoding='utf-8',newline='\n') as f:json.dump(value,f,ensure_ascii=False,indent=2)
    print(json.dumps(value['summary'],ensure_ascii=False))


if __name__=='__main__':main()
