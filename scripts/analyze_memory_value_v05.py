"""仅对冻结的v05真实回答做本地来源审查汇总；不调用模型、不更改Gold。"""
from collections import Counter
import argparse
import gzip
from hashlib import sha256
import json
from pathlib import Path
from statistics import median
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from min_agent.long_memory.answer_scoring import validate_review

OUT = ROOT / 'reports/memory_v05'
INPUT = OUT

# 本表是执行代理读过真实输出和原始来源后的主观审查，不是独立Judge成绩。
# 四元组依次为原书precision、recall、reasoning、proactivity；合成题只迁移量表。
ASSESSMENTS = {
    'layer1_01_bank_account': {
        'A': ((4,1,1,2), '没有历史，正确承认缺失，但未完成原书号码回忆目标。', []),
        'B': ((3,1,1,2), '未获账号和路由号，未误把4827当账号；把当前视图缺失扩大成全部历史没有号码，措辞过强。', ['RANKING']),
        'C': ((4,4,4,4), '正确给出账号及路由号，明确历史assistant陈述和实时银行核验的区别。', []),
    },
    'layer1_08_hotel_reservation': {
        'A': ((4,1,1,2), '无历史正确弃答；一般性的入住分配房号建议不代表该用户预订事实。', []),
        'B': ((4,4,4,3), '号码、房型、日期及套餐均有原句；一个引用ID拼写错误，引用维度另记失败。', []),
        'C': ((4,4,4,3), '正确给出预订号和1912；合理指出历史周末日期矛盾，不伪称实时核实。', ['EVALUATION']),
    },
    'layer2_01_multiple_vehicles': {
        'A': ((4,1,1,2), '没有历史，无法回答已预约内容。', []),
        'B': ((4,1,1,2), '缺少预约和第二辆车关键证据；虽有Honda原句，答案也没有利用它。不能据此声称完整Oracle下推理失败。', ['RANKING','RETRIEVAL']),
        'C': ((4,2,2,3), '准确回忆Honda时间和确认号并指出日期矛盾；Tesla仅放在uncertainties，没有在主回答呈现双车并询问哪辆。服务套餐/价格/门店未纳入Oracle，不将其遗漏全部归于模型。', ['REASONING','EVALUATION']),
    },
    'layer2_03_multiple_credit_cards': {
        'A': ((4,1,1,2), 'Amex Gold只是举例所需卡名，不是记起用户持卡，词面命中不能算真实召回。', []),
        'C': ((2,3,3,3), '完成两卡实体与3x/1.5%及换卡状态综合；但凭空断言Gold免外币费、仅航空公司直订才获3x。前者连原始历史也没有，原书标准本身包含这项无来源事实。', ['REASONING','EVALUATION']),
    },
    'layer3_01_travel_coordination': {
        'A': ((4,1,1,2), '只给通用出行检查项，提passport不等于回忆用户护照事实。', []),
        'B': ((1,2,2,3), '利用护照月份和行程提示风险，但把2月到期相对1月22日的间隔说成约一周；日值、预订号、Priority Pass未进视图。历史assistant提到的入境规则被当成已核实规则。', ['RETRIEVAL','RANKING','REASONING','EVALUATION']),
        'C': ((2,2,3,3), '正确关联到期日、行程、预订号和Priority Pass；入境及航司政策未经本轮核验却被确定陈述。Oracle缺原书要求的保险/付款/加急时长证据，原书六个月规则也与原历史assistant说法冲突，不按未照抄Gold判错。', ['EVALUATION']),
    },
    'layer3_03_home_purchase_coordination': {
        'A': ((4,1,1,2), '无记录不能判断是否按期推进，正确请求背景。', []),
        'B': ((3,2,2,3), '通过Core的等价来源正确发现11月20日即将到期；缺保险/赠与及搬家确认，部分话术从看不到预订推到似乎没订，缺证据影响状态判断。', ['RANKING','PACKING','EVALUATION']),
        'C': ((4,2,3,4), '五个冻结核心事实均表达正确，按11月18日判断两天后到期正确；原书要求已过期有时点错误。缺未提供的搬家时间/钢琴等细节，不能当作完整Oracle。', ['EVALUATION']),
    },
    'v05_current_instruction': {
        'A': ((4,1,1,4), '一句中文说明没有代号证据，未编造。', []),
        'B': ((4,4,4,4), '一句中文给出ORCHID-17，没有遵循旧英文长回答偏好。', []),
        'C': ((4,4,4,4), '一句中文给出ORCHID-17，没有遵循旧英文长回答偏好。', []),
    },
    'v05_temporal_state': {
        'A': ((4,1,1,3), '无状态证据正确弃答，未猜测。', []),
        'B': ((4,4,4,3), '正确以10月3日取消覆盖10月1日待审核；没有读到10月10日未来恢复。说明过长但结论准确。', []),
        'C': ((4,4,4,3), '正确解释时间更新，未使用未来恢复；额外实时核验声明不改变截至时点结论。', []),
    },
    'v05_missing_memory': {
        'A': ((4,4,4,4), '正确拒绝编造不存在的密码。', []),
        'B': ((4,4,4,4), '正确区分项目代号与门禁密码，没有把ORCHID-17当密码。', []),
        'C': ((4,4,4,4), 'Oracle本来为空，正确说明缺失。', []),
    },
}

# 原Gold不变。这是逐项来源审查后的独立语义覆盖列，不替换词面指标。
# 主answer字段是评分对象；uncertainties中提到Tesla单列说明，不偷偷合并。
SEMANTIC_MATCHES = {
    'layer1_01_bank_account': {'A':[0,0], 'B':[0,0], 'C':[1,1]},
    'layer1_08_hotel_reservation': {'A':[0,0], 'B':[1,1], 'C':[1,1]},
    'layer2_01_multiple_vehicles': {'A':[0,0,0], 'B':[0,0,0], 'C':[1,0,1]},
    'layer2_03_multiple_credit_cards': {'A':[0,0,0], 'B':None, 'C':[1,1,1]},
    'layer3_01_travel_coordination': {'A':[0,0,0,0], 'B':[1,0,0,0], 'C':[1,1,1,1]},
    'layer3_03_home_purchase_coordination': {'A':[0,0,0,0,0], 'B':[1,0,0,0,0], 'C':[1,1,1,1,1]},
    'v05_current_instruction': {'A':[0], 'B':[1], 'C':[1]},
    'v05_temporal_state': {'A':[0], 'B':[1], 'C':[1]},
    'v05_missing_memory': {'A':[], 'B':[], 'C':[]},
}

# 已列核心断言的证据审查；不是对全文所有一般常识的自动事实核查。
# quote必须逐字出现在真实answer；supported仅指可见历史/明确计算支撑。
CLAIMS = {
    ('layer1_01_bank_account','C'): [('123006800',True),('4429853327',True)],
    ('layer1_08_hotel_reservation','B'): [('HSD-445782',True),('1912',True)],
    ('layer1_08_hotel_reservation','C'): [('HSD-445782',True),('1912',True)],
    ('layer2_01_multiple_vehicles','C'): [('2019 Honda Accord',True),('Friday, November 24th at 8 AM',True),('FS-447291',True)],
    ('layer2_03_multiple_credit_cards','C'): [('3x points on flights',True),('1.5% cash back',True),('3% foreign transaction fee',True),('is being cancelled with a replacement sent',True),('the Gold avoids the 3% foreign transaction fee',False),('Book **directly with the airline** (not a third-party site) so the charge codes as an airline purchase and earns the 3x',False)],
    ('layer3_01_travel_coordination','B'): [('your passport expires in February 2025',True),('January 15–22, 2025',True),('only about a week of margin after you return',False),('Japan requires your passport to be valid for the duration of your stay',False)],
    ('layer3_01_travel_coordination','C'): [('February 18, 2025',True),('January 22, 2025',True),('DELTA-JMK892',True),('Priority Pass',True),("which is what Japan's visa-free entry requires",False),('airlines sometimes apply stricter validity checks at check-in',False)],
    ('layer3_03_home_purchase_coordination','B'): [('expires Nov 20',True),("two days from today (Nov 18)",True)],
    ('layer3_03_home_purchase_coordination','C'): [('expiring November 20, 2024',True),("That's two days from today",True),('December 13',True),("Gift letter for your parents' $30,000",True),('contingent on closing',True),('ALV-122124',True)],
    ('v05_current_instruction','B'): [('ORCHID-17',True)],
    ('v05_current_instruction','C'): [('ORCHID-17',True)],
    ('v05_temporal_state','B'): [('「已取消」',True)],
    ('v05_temporal_state','C'): [('「已取消」',True)],
}


def write(name, value):
    path = OUT / name
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        if isinstance(value,str): stream.write(value)
        else: json.dump(value, stream, ensure_ascii=False, indent=2)


def main():
    global OUT
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    OUT=args.output_dir
    OUT.mkdir(parents=True,exist_ok=True)
    pack = json.loads(gzip.decompress((INPUT/'prepared-pilot.json.gz').read_bytes()))
    results = json.loads((INPUT/'live-results.json').read_text(encoding='utf-8'))
    assert sha256((INPUT/'prepared-pilot.json.gz').read_bytes()).hexdigest() == results['pack_sha256']
    actual = {(r['case_id'],r['group']):r for r in results['responses']}
    reviews=[]; diagnostics=[]; case_summary=[]
    for case in pack['cases']:
        identity=case['case_id']; diag=case['search']['diagnostic']
        for entry in case['oracle_evidence']:
            mid=entry['id']
            ranks={key:next((i+1 for i,x in enumerate(diag[key]) if x['memory_id']==mid),None)
                   for key in ('all_pool_lexical_hits','initial_candidates','merged_before_cap','merged_candidates')}
            visible=mid in case['groups']['B']['visible_ids']
            top=mid in case['search']['trace']['final_ids']
            if visible: tag='VISIBLE'
            elif top: tag='PACKING'
            elif ranks['merged_candidates'] is not None: tag='RANKING'
            elif ranks['all_pool_lexical_hits'] is not None: tag='RETRIEVAL_CANDIDATE_CAP'
            else: tag='RETRIEVAL_LEXICAL_MISMATCH'
            diagnostics.append(dict(case_id=identity,memory_id=mid,source=case['source_positions'][mid],
                quote=entry['text'],ranks=ranks,top8=top,visible=visible,stage=tag))
        for group in 'ABC':
            response=actual[(identity,group)]
            if response['status']!='ANSWERED':
                case_summary.append(dict(case_id=identity,group=group,status=response['status'],semantic_facts='NOT_RUN'))
                continue
            scores,note,failures=ASSESSMENTS[identity][group]
            text=response['parsed']['answer']; ids=response['parsed']['used_evidence_ids']
            claims=[dict(quote=q,supported_by_visible_evidence=ok) for q,ok in CLAIMS.get((identity,group),[])]
            assert all(c['quote'] in text for c in claims)
            good=sum(c['supported_by_visible_evidence'] for c in claims)
            all_claims=len(claims)
            matches=SEMANTIC_MATCHES[identity][group]
            visible=set(case['groups'][group]['visible_ids'])
            extra={
                'fact_accuracy': dict(scope='explicitly listed core assertions; not exhaustive full-answer accuracy',
                    supported=good,audited=all_claims,ratio=good/all_claims if all_claims else 'N/A_ABSTENTION_OR_NO_CORE_FACT',claims=claims),
                'entity_scenario': ('NO_UNSUPPORTED_ENTITY_MERGE' if not (identity=='layer2_01_multiple_vehicles' and group=='C') else 'TESLA_ONLY_IN_UNCERTAINTIES_NO_MAIN_ANSWER_DISAMBIGUATION'),
                'temporal_state': ('WRONG_MARGIN_ABOUT_ONE_WEEK' if identity=='layer3_01_travel_coordination' and group=='B' else 'NO_OBSERVED_TEMPORAL_CONTRADICTION_OR_NOT_APPLICABLE'),
                'current_conflict': ('PASS_CURRENT_CHINESE_ONE_SENTENCE' if identity=='v05_current_instruction' else 'NOT_APPLICABLE'),
                'unsupported_memory': dict(invented_visible_ids=sorted(set(ids)-visible),
                    unsupported_core_assertions=all_claims-good,note='unsupported by supplied evidence does not establish a real-world policy false'),
                'evidence_use': dict(declared=len(ids),visible=len(set(ids)&visible),
                    note='self-report, not causal proof; provenance does not by itself verify real-world truth'),
            }
            # A是人为移除材料的对照，不归因成生产存储/检索故障。
            adequate = bool({'REASONING','CONFLICT'} & set(failures))
            refs=[dict(memory_id=e['id'],quote=e['text']) for e in case['oracle_evidence']]
            if group=='B':
                refs += [dict(memory_id=e['id'],quote=e['content'])
                         for e in case['source_episodes'] if e['id'] in visible]
            review=dict(case_id=identity,group=group,answer_sha256=response['answer_sha256'],
                reviewer='executing Codex agent, unblinded source audit, no external Judge',verified=True,
                book_scores=dict(zip(('precision','recall','reasoning','proactivity'),scores)),dimensions=extra,
                answer_excerpt=text[:min(120,len(text))],source_evidence=refs,
                adequate_visible_evidence=adequate,
                adequacy_scope='local diagnosed issue only; not a claim that Oracle covers every original criterion',
                failure_categories=failures,rationale=note,
                original_book_scores_applicable=case['origin']=='public_book',
                semantic_required_fact_matches=matches,
                semantic_review_note='separate evidence-based audit, original Gold and literal proxy untouched')
            validate_review(case,group,response,review)
            reviews.append(review)
            case_summary.append(dict(case_id=identity,group=group,status='REVIEWED',
                semantic_facts=dict(hit=sum(matches),total=len(matches)),book_scores=review['book_scores'],
                failure_categories=failures,rationale=note))
    write('source-reviews.json',reviews)
    write('source-stage-diagnostics.json',diagnostics)
    groups={}
    for group in 'ABC':
        rows=[r for r in results['responses'] if r['group']==group]
        book=[x for x in case_summary if x['group']==group and x['case_id'].startswith('layer')]
        valid=[x for x in book if x['status']=='REVIEWED']
        groups[group]=dict(requests=len(rows),format_valid=sum(r['status']=='ANSWERED' for r in rows),
            public_semantic_fact_hits=sum(x['semantic_facts']['hit'] for x in valid),
            public_scored_fact_denominator=sum(x['semantic_facts']['total'] for x in valid),
            public_frozen_total_facts=19,public_unscored_facts=19-sum(x['semantic_facts']['total'] for x in valid),
            input_tokens=sum(r['usage']['input_tokens'] for r in rows),output_tokens=sum(r['usage']['output_tokens'] for r in rows),
            peak_usage_cost_usd=sum(r['peak_usage_cost_usd'] for r in rows),
            total_latency_ms=sum(r['latency_ms'] for r in rows),median_latency_ms=median(r['latency_ms'] for r in rows),
            total_reference_bytes=sum(c['groups'][group]['reference_bytes'] for c in pack['cases']))
    write('paired-analysis.json',dict(groups=groups,cases=case_summary,
        diagnostic_source_counts=dict(Counter(d['stage'] for d in diagnostics)),
        scope='pilot first automatic read-only memory view; not full Agent loop; not held-out generalization',
        reviewer='unblinded executing agent; independent human review NOT_RUN',
        conclusion='The first-view evidence selection/packing path is a demonstrated bottleneck, not the only bottleneck. Oracle improves factual coverage but does not remove format, evidence-boundary, ambiguity, or evaluation failures.'))
    system=pack['cases'][0]['groups']['A']['messages'][0]['content']
    assert all(c['groups'][g]['messages'][0]['content']==system for c in pack['cases'] for g in 'ABC')
    write('prompt-and-protocol.md','# 完整共用提示词与输入差异\n\n三组完全相同的system内容如下。每题只变参考材料；问题、as_of、模型、high、4096输出上限和总预算相同。\n\n```text\n'+system+'\n```\n\n```diff\n A: application_state，无长期材料\n-B: 无\n+B: 生产默认自动stable_core/active_core/retrieved/root_index视图\n-C: 无\n+C: 经合法时点校验的原始证据片段Oracle；不传评分标准\n```\n\n每道题的完整动态输入和模型原始回答见reviewed-live-report.md；精确三条消息在prepared-pilot.json.gz。Gold、book_evaluation_criteria和本审查结果只在本地评分，不进入任一provider请求。\n')
    print(json.dumps(dict(groups=groups,reviewed=len(reviews),stages=dict(Counter(d['stage'] for d in diagnostics))),ensure_ascii=False))


if __name__=='__main__': main()
