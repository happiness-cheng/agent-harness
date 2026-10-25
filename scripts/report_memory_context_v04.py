"""从固定原始输出生成报告；不重跑、不改Gold或评分。"""
from pathlib import Path
import gzip
from hashlib import sha256
import json

ROOT=Path(__file__).resolve().parents[1]


def save(path,text):
    with (ROOT/path).open('x',encoding='utf-8',newline='\n') as f:f.write(text.rstrip()+'\n')


def main():
    path=ROOT/'reports/memory_v04/context-fixed.json.gz'
    data=json.loads(gzip.decompress(path.read_bytes()))
    oracle={}
    for split in data['summaries']:
        cases=[c for c in data['cases'] if c['split']==split]
        oracle[split]={}
        for rank in ('R0','R1'):
            oracle[split][rank]=dict(
                candidate=sum(c['oracle']['candidate']['unbounded_facts'] for c in cases),
                rank_ceiling=sum(c['oracle']['candidate']['rank_ceiling'] for c in cases),
                candidate_budget_ceiling=sum(c['oracle']['candidate']['budget_ceiling'] for c in cases),
                actual_topk=sum(c['configurations'][rank+'_P0']['topk_facts'] for c in cases),
                full_topk_budget_ceiling=sum(c['oracle'][rank]['full_topk']['budget_ceiling'] for c in cases),
                actual_p0=sum(c['configurations'][rank+'_P0']['visible_facts'] for c in cases),
                extracted_unlimited=sum(c['oracle'][rank]['extractive_topk']['unbounded_facts'] for c in cases),
                extracted_budget_ceiling=sum(c['oracle'][rank]['extractive_topk']['budget_ceiling'] for c in cases),
                actual_p1=sum(c['configurations'][rank+'_P1']['visible_facts'] for c in cases),
                actual_p2=sum(c['configurations'][rank+'_P2']['visible_facts'] for c in cases))
    save('reports/memory_v04/oracle-summary.json',json.dumps(oracle,ensure_ascii=False,indent=2))
    lines=['# Adrian Memory v0.4 固定上下文实验','',
        '结论：不建议默认启用P1/P2或事实绑定排序。P1增加了显式来源/时间信息，但新测试主覆盖没有提高；P2退化；绑定排序的收益目前来自已见开发题。保留实现与负结果供评审。','',
        '基线已独立固化为5a962bd，不再以b90f104冒称可复现实际v0.3。实现、规则和新Gold冻结于提交 '+data['source_commit']+' 后运行一次23题、六配置。原20题均为开发，新3题9事实只作小批保留测试，同代理制作/审查，不是专家盲评。','',
        'R0=原候选＋v0.3实验排序B；R1=同一候选以显式属性绑定稳定分层。P0=旧整条，P1=完整原句提取包，P2=同样的包做预算选择。Top-8=8、候选cap=100、预算2048紧凑UTF-8字节全部固定；R1不改旧权重。','',
        '## 主指标：原始来源锚点不变','']
    for split,groups in data['summaries'].items():
        lines+=['### '+split,'','| 配置 | 候选命中 | Top-K命中 | 可见命中 | Candidate Recall | Top-K Recall | Visible Coverage | 平均字节 |','|---|---:|---:|---:|---:|---:|---:|---:|']
        for cfg,v in groups.items():
            lines.append(f"| {cfg} | {v['candidate_facts']}/{v['facts']} | {v['topk_facts']}/{v['facts']} | {v['visible_facts']}/{v['facts']} | {v['candidate_recall']:.2%} | {v['topk_recall']:.2%} | {v['visible_coverage']:.2%} | {v['context_bytes']:.1f} |")
        lines+=['']
    lines+=['## 独立源审指标（不能混入主分）','',
        '仅旧租车/酒店4项＋新三题9项，共13个事实做了等价来源审阅，批准项须实体/属性/值/时间/限定一致。问句回声、仅“可用”的房间、旧150美元日额及150美元月租均明确拒绝。原Gold、原成绩未回填。','',
        '“已审等价值可见”只说明批准的取值来源出现；“完整文本”要求所列主体/属性/值/否定/时间等必要原句全部可见；“完整证据”还要求这些来源的情景与观察时间显式可见。P0没有显式观察时间，因此其完整证据数为0并不等于原文事实全部错误。','',
        '审核span目前部分仍是整句/整消息，可能包含非必要礼貌语，属于保守的已审span保留代理，不是可靠的开放域语义完整性证明。这个缺口原样保留，不在看到分数后缩短标注来提高结果。机械限定句保留率另列，不能拿它代替语义审核。','',
        '| 分组/配置 | 已审事实数 | 已审值可见 | 完整文本 | 完整证据 | 完整保留率 | 错误属性/已审预测 | 已审预测/全部预测 |','|---|---:|---:|---:|---:|---:|---:|---:|']
    for split,groups in data['summaries'].items():
        for cfg,v in groups.items():
            if not v['audited_facts']:continue
            rate=f"{v['full_evidence_retention']:.2%}" if v['full_evidence_retention'] is not None else 'NOT_RUN'
            lines.append(f"| {split}/{cfg} | {v['audited_facts']} | {v['reviewed_value_facts']} | {v['complete_text_facts']} | {v['complete_evidence_facts']} | {rate} | {v['binding_wrong']}/{v['binding_checked']} | {v['binding_checked']}/{v['binding_predictions']} |")
    lines+=['','属性错误率的分母仅是已审锚点上实际产生的绑定，未知/未产出不算正确预测。新测试仅有2个可评价绑定、错误0；不能称为所有事实零错误。药物剂量/照护收益等不在本轮编号规则覆盖范围。所有配置机械限定句保留检查通过，也不能因此声称完整语义保留100%。','',
        '## Oracle：每段损失分开','',
        'Gold只在决策完成后进入精确mask/条数/最低字节DP；不上线、不参与P2效用。候选并集与最多8条覆盖忽略预算；预算Oracle使用真实的整条或提取包及相同JSON字节成本，不构造新事实。这里只给冻结来源/取值指标的上界，不是复杂多来源语义完整证明或模型成功率。','',
        '| 分组/排序 | 候选并集 | 最优Top8 | 候选预算最优 | 实际Top8 | 此Top8整条预算最优 | P0实际 | 提取后无限预算 | 提取包预算最优 | P1实际 | P2实际 |','|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for split,ranks in oracle.items():
        for rank,o in ranks.items():
            order=['candidate','rank_ceiling','candidate_budget_ceiling','actual_topk','full_topk_budget_ceiling','actual_p0','extracted_unlimited','extracted_budget_ceiling','actual_p1','actual_p2']
            lines.append('| '+split+'/'+rank+' | '+' | '.join(str(o[k]) for k in order)+' |')
    lines+=['','多银行题：四个来源事实已经在R0 Top-8，两个原始Episode合计621字节就可保留四个事实，但P0/P1/P2实际均只留两个。这里不是2048字节硬上限，而是选择效用偏向高排名无效材料。新药物题P2把原本可见的剂量来源丢掉，是明确退化。','',
        '## 检索与装配耗时','',
        '| 分组/配置 | 检索ms | 绑定分层ms（R1额外） | 装配ms |','|---|---:|---:|---:|']
    for split,groups in data['summaries'].items():
        for cfg,v in groups.items():
            lines.append(f"| {split}/{cfg} | {v['retrieval_ms']:.2f} | {v['binding_ms'] if cfg.startswith('R1') else 0:.2f} | {v['assembly_ms']:.2f} |")
    lines+=['','单次固定本地实验的跨题平均，包含权威状态读取、完整池诊断和共用证据包构建。P0也执行了诊断用句切分；存在顺序/缓存影响，不能据这些毫秒断言比生产旧打包更快，亦不是SLA或模型费用。Provider Token/Judge/真实模型成绩均NOT_RUN。','',
        '## 逐题结果','', '每格Top-K命中/可见命中，主指标沿用原来源锚点。','',
        '| 案例 | 事实数 | R0_P0 | R0_P1 | R0_P2 | R1_P0 | R1_P1 | R1_P2 |','|---|---:|---:|---:|---:|---:|---:|---:|']
    for c in data['cases']:
        vals=[f"{c['configurations'][k]['topk_facts']}/{c['configurations'][k]['visible_facts']}" for k in ['R0_P0','R0_P1','R0_P2','R1_P0','R1_P1','R1_P2']]
        lines.append(f"| {c['case_id']} | {c['fact_count']} | "+' | '.join(vals)+' |')
    lines+=['','## 版本、验证与复现','',
        '工作目录 `C:/Users/陈独秀/.codex/tmp/adrian-memory-v04`。代码源版本 '+data['source_commit']+'，数据和实现hash均在原始输出。基线为5a962bd；所有旧20题R0_P0与v0.3 B的候选、Top-K、可见ID及主分一致。','',
        f"原始输出SHA256 `{sha256(path.read_bytes()).hexdigest()}`；新Gold `{data['holdout_sha256']}`；源审 `{data['review_sha256']}`。所有旧数据、报告、文件hash保留。",'',
        '```powershell', '$env:PYTHONPATH="src"',
        '& C:/Users/陈独秀/Documents/agent-harness/.venv/Scripts/python.exe -X utf8 scripts/eval_memory_context_v04.py --book-root C:/Users/陈独秀/.codex/tmp/ai-agent-book-memory-reference --report reports/memory_v04/context-reproduction.json.gz',
        '& C:/Users/陈独秀/Documents/agent-harness/.venv/Scripts/python.exe -X utf8 scripts/inspect_memory_context_v04.py --report reports/memory_v04/context-fixed.json.gz --case layer2_05_multiple_bank_accounts --configuration R0_P2 --view oracle',
        '& C:/Users/陈独秀/Documents/agent-harness/.venv/Scripts/python.exe -X utf8 -m pytest -q --tb=short',
        '& C:/Users/陈独秀/Documents/agent-harness/.venv/Scripts/python.exe -X utf8 -m pip wheel . --no-deps --no-build-isolation --no-index --wheel-dir dist/memory-v04-reproduction',
        '```','',
        '实际完整回归1211 passed、3 live deselected、1已有警告；定向11项通过，RED/GREEN输出保留。compileall、pip check、离线wheel通过；类型/lint工具不可用则NOT_RUN。无外部模型、私人导入、数据库迁移或远端推送。', '',
        '建议：保留离线研究工具，不启用P1/P2或分层排序；先完善字段级证据审核和任务相关性，再选择一项做新冻结实验。本轮不继续扩大。']
    save('reports/memory_v04/evaluation_v04.md','\n'.join(lines))
    failures=['# v0.4 剩余问题与失败证据','',
        '本报告不修改Gold、权重、已冻结结果。所有事实来源/上下文/字符范围/绑定/Oracle见context-fixed.json.gz，可由检查CLI逐项还原。','',
        '## 核心发现','',
        '1. 固定预算内的排序效用仍然不是事实价值。多银行四事实有621字节可行解，P2却继续选择前排低价值材料；Oracle与实际之差证明选择器有改进空间，而非应一律提高预算。',
        '2. 摘录不是无条件省字节。来源、时间、情景和多条字符范围会增加成本；一些原句本就很短。P1在多个已见分组降低可见覆盖，不能只用删除了寒暄证明有效。',
        '3. P2丢掉新药物题的一个原本可见事实：效用只按倒数排名、有限编号绑定和查询词覆盖，无法识别抽象“血压药全部细节”中的完整剂量及家属归属。',
        '4. 局部绑定在已见租车/酒店各恢复两个锚点，但新三题未增加主覆盖。安装服务码、药物剂量、照护收益等属性未知，未知并不算正确绑定；不能用0/2已审错误推断普遍准确。',
        '5. 完整证据不仅是取值。一些药物/保单需要另一个来源证明具体人物、状态或限定，原Top-K并未同时包含这些来源。装配不能凭空补齐，来源范围名CVS/layer3也不等于病人或投保人。',
        '6. 完整性评测仍有边界：部分已审span过粗，含非必要寒暄，缺失它们不必然造成语义损失；P0没有显式观察时间被“完整证据”规则扣分，也不代表其全部文字事实错误。保留原分并明确该代理指标限制，不依据结果改标注。','',
        '## 新测试逐事实去向（R0_P0 / P1 / P2）','',
        '| 案例/事实 | 候选 | Top-K | P0可见 | P1可见 | P2可见 | 定位 |','|---|---|---|---|---|---|---|']
    for c in data['cases']:
        if c['split']!='frozen_test':continue
        base=c['configurations']['R0_P0']
        for i,m in enumerate(base['matches']):
            visible=[bool(c['configurations'][f'R0_P{k}']['matches'][i]['visible_ids']) for k in range(3)]
            reason='候选缺口' if not m['candidate_ids'] else '排序截断' if not m['top_ids'] else 'P2选择退化' if visible[0] and not visible[2] else '可见/完整字段需另查' if visible[0] else '预算/选择'
            failures.append('| '+c['case_id']+' / '+' / '.join(m['variants'])+f" | {bool(m['candidate_ids'])} | {bool(m['top_ids'])} | {visible[0]} | {visible[1]} | {visible[2]} | {reason} |")
    failures+=['','## 证据保留与启用条件','',
        '原始单来源指标、批准的等价取值、严格全字段/显式来源时间保留分别报告。拒绝表保留仅可用房间、回声问句、旧日额、异属性月租；这些不能自动算作等价证据。没有因为看到结果去扩大正例或删除失败。','',
        '机械限定句检查通过只说明规则要求的句子没被截断；不能覆盖所有隐含指代、完整实体、时间推理或临床/保险正确性。当前是有界离线原文摘录实验，不建议生产默认启用。','',
        '下一步只建议先评审更精确的必要字段span与多来源事实单元，再决定是否优化任务相关性或打包效用。需要新保留样本；本轮到此停止。']
    save('reports/memory_v04/failure_analysis_v04.md','\n'.join(failures))


if __name__=='__main__':main()
