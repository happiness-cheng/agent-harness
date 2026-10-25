"""只从原始公共对话制作小批源审标注；不读取检索输出，不被检索/装配导入。"""
from pathlib import Path
from hashlib import sha256
import json
import yaml

ROOT=Path(__file__).resolve().parents[1]
BASE=Path('C:/Users/陈独秀/.codex/tmp/ai-agent-book-memory-reference/chapter3/user-memory-evaluation')


def main():
    selection=json.loads((ROOT/'reports/memory_v04/test-selection.json').read_text(encoding='utf-8'))
    old=json.loads((ROOT/'reports/memory_v03/baseline.json').read_text(encoding='utf-8'))
    cases={m['case_id']:yaml.safe_load((BASE/m['path']).read_text(encoding='utf-8')) for m in old['book_manifest']}
    recipes={
        'layer1_09_home_security':[(0,50,'ADT-7789234','ADT account identifier'),(0,52,'SVC-44821','Installation service code')],
        'layer2_07_multiple_medications':[(0,5,'Lisinopril 20mg','James current recorded Lisinopril dose'),(0,9,'Amlodipine 5mg','James Amlodipine dose with grapefruit warning'),(1,17,'Losartan 50mg','Susan Losartan dose and reported fatigue')],
        'layer3_07_eldercare_coordination':[(2,117,'$5,000','Modification benefit without elimination period'),(2,23,'$2,000','Yearly equipment benefit'),(2,107,'$168','Updated daily benefit, not old $150'),(2,43,'5-7 business days','Care assessment scheduling interval')],
    }
    def span(case,h,i,needle=None,field='value'):
        history=case['conversation_histories'][h];message=history['messages'][i];text=message['content']
        needle=text if needle is None else needle;start=text.index(needle)
        return dict(conversation_id=history['conversation_id'],message_index=i,role=message['role'],
            timestamp=str(history['timestamp']),start=start,end=start+len(needle),quote=needle,
            message_sha256=sha256(text.encode()).hexdigest(),field=field)
    gold=dict(protocol='v04 frozen source anchors; original old Gold unchanged',annotator='executing agent source review; no external human blind review',frozen_before_algorithm_evaluation=True,cases={})
    for item in selection['cases']:
        identity=item['case_id'];case=cases[identity];facts=[]
        for number,(h,i,value,label) in enumerate(recipes[identity],1):
            facts.append(dict(fact_id=f'{identity}:f{number}',label=label,variants=[value],sources=[span(case,h,i,value)]))
        gold['cases'][identity]=dict(required_facts=facts,source_path=item['path'],source_sha256=item['sha256'])
    review=dict(protocol='independent post-source-reading adjudication; no retrieval outputs used',reviewer='executing agent, not user confirmation',scope='new three cases plus old rental/hotel; complete fields for this small batch only',facts=[],rejected=[])
    def add(identity,n,entity,attribute,groups):
        case=cases[identity]
        # 每一组显式列出取值来源和全部必要字段，不因同值自动扩展。
        alternatives=[]
        for anchor,fields in groups:
            h,i,needle=anchor
            alternatives.append(dict(anchor=span(case,h,i,needle),required=[span(case,*spec) for spec in fields],
                decision='approved_source_equivalence',checks=dict(entity=entity,attribute=attribute,
                value=needle,time='same source event timestamp; no later replacement used',
                qualifiers='all listed required spans must accompany the value for complete evidence')))
        review['facts'].append(dict(case_id=identity,fact_id=f'{identity}:f{n}',entity=entity,attribute=attribute,
            alternatives=alternatives,require_scene_and_time=True))
    # 已见开发题：精确确认号可由最终汇总重复证明；问句/仅可用房间不算已确认。
    c='layer1_07_car_rental'
    add(c,1,'Enterprise rental in car_rental_001','reservation_id',[
        ((0,67,'E7739482M'),[(0,67,None,'entity_attribute_value')]),
        ((0,95,'E7739482M'),[(0,95,None,'entity_attribute_value_time')])])
    add(c,2,'TechCorp discount for Enterprise rental','discount_code',[
        ((0,87,'TC-8847'),[(0,87,None,'entity_attribute_value')])])
    c='layer1_08_hotel_reservation'
    add(c,1,'Hilton booked anniversary stay','reservation_id',[
        ((0,69,'HSD-445782'),[(0,69,None,'entity_attribute_value')]),
        ((0,81,'HSD-445782'),[(0,81,None,'entity_attribute_value_time')])])
    add(c,2,'Hilton reserved room, not an availability offer','room_number',[
        ((0,41,'1912'),[(0,41,'Room 1912 is now reserved for you.','entity_attribute_value')]),
        ((0,81,'1912'),[(0,81,None,'entity_attribute_value_time')])])
    c='layer1_09_home_security'
    add(c,1,'ADT account in security_install_001','account_id',[
        ((0,50,'ADT-7789234'),[(0,50,'Your ADT account number will be ADT-7789234.','entity_attribute_value')]),
        ((0,92,'ADT-7789234'),[(0,92,None,'entity_attribute_value_time')])])
    add(c,2,'ADT installation, not ordinary account reference','installation_service_code',[
        ((0,52,'SVC-44821'),[(0,50,None,'entity'),(0,52,None,'attribute_value_purpose')]),
        ((0,92,'SVC-44821'),[(0,92,None,'entity_attribute_value_time')])])
    c='layer2_07_multiple_medications'
    father=(0,3,'Thank you Mr. James Wilson.','entity')
    unchanged=(0,34,None,'negation_current_dose')
    add(c,1,'James Wilson, historical pharmacy record, not medical advice','medication_dose',[
        ((0,5,'Lisinopril 20mg'),[father,(0,5,None,'attribute_value_frequency'),unchanged]),
        ((0,73,'Lisinopril 20mg'),[father,(0,73,None,'attribute_value'),unchanged])])
    add(c,2,'James Wilson, with explicit Amlodipine warning','medication_dose',[
        ((0,9,'Amlodipine 5mg'),[father,(0,9,'You also take Amlodipine 5mg for blood pressure, do you need that?','attribute_value'),(0,43,None,'negative_constraint')])])
    add(c,3,'Susan Wilson, not James or Emma','medication_dose',[
        ((1,17,'Losartan 50mg'),[(1,0,None,'entity'),(1,17,None,'attribute_value_frequency_time'),(1,26,None,'reported_effect')])])
    c='layer3_07_eldercare_coordination'
    mother=[(2,0,None,'entity_relation'),(2,3,None,'entity_policy')]
    add(c,1,'Margaret Thompson long-term care policy','modification_benefit',[
        ((2,117,'$5,000'),mother+[(2,117,None,'attribute_value_no_elimination')]),
        ((2,31,'$5,000'),mother+[(2,31,None,'attribute_value_one_time'),(2,117,None,'no_elimination')])])
    add(c,2,'Margaret Thompson long-term care policy','equipment_yearly_limit',[
        ((2,23,'$2,000'),mother+[(2,23,None,'attribute_value_yearly')])])
    add(c,3,'Margaret Thompson in-home care, updated quote','daily_benefit',[
        ((2,107,'$168'),mother+[(2,107,None,'attribute_value_time_negation')])])
    add(c,4,'Margaret Thompson care assessment','assessment_schedule',[
        ((2,43,'5-7 business days'),mother+[(2,41,None,'attribute'),(2,43,'We can schedule within 5-7 business days.','value_time')])])
    for identity,h,i,reason in [
        ('layer1_07_car_rental',0,68,'echo question does not independently confirm booking'),
        ('layer1_08_hotel_reservation',0,37,'room available is not yet room reserved'),
        ('layer1_09_home_security',0,53,'echo question does not independently confirm account/code roles'),
        ('layer3_07_eldercare_coordination',2,15,'old $150 daily quote superseded by later $168'),
        ('layer3_07_eldercare_coordination',2,28,'$150 monthly hospital bed is a different attribute/time unit')]:
        review['rejected'].append(dict(case_id=identity,source=span(cases[identity],h,i),decision='rejected',reason=reason))
    for path,value in [('tests/fixtures/memory_v04_holdout.json',gold),('tests/fixtures/memory_v04_evidence_review.json',review)]:
        with (ROOT/path).open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')
        print(path,sha256((ROOT/path).read_bytes()).hexdigest())


if __name__=='__main__':main()
