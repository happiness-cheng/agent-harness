"""后验报告和证据完整性审计；不修改原 Gold，不重跑或调参。"""
from collections import Counter
import gzip
from hashlib import sha256
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]


def save(path,text):
    with (ROOT/path).open('x',encoding='utf-8',newline='\n') as f:f.write(text.rstrip()+'\n')


def main():
    artifact=ROOT/'reports/memory_v03/abcd-fixed.json.gz'
    data=json.loads(gzip.decompress(artifact.read_bytes()))
    # 同场景替代证据只登记审计，不计入冻结指标；相同金额可能表达不同属性。
    alternatives=[]
    for case in data['cases']:
        if case['split']=='original_dev':continue
        pool={m['id']:m for m in case['source_episodes']}
        for fact in case['fact_analysis']:
            expected_positions={(s['conversation_id'],s['message_index']) for s in fact['literal_history_support']}
            expected_scenes={s[0] for s in expected_positions}
            for group,row in case['groups'].items():
                if fact['groups'][group]['visible_ids']:continue
                for identity in row['visible_ids']:
                    item=pool[identity]
                    source=case['source_positions'][item['evidence_refs'][0]]
                    position=(source['conversation_id'],source['message_index'])
                    text=item['content'].split('] ',1)[-1]
                    if position in expected_positions or position[0] not in expected_scenes:continue
                    if any(v.casefold() in text.casefold() for v in fact['variants']):
                        alternatives.append(dict(case_id=case['case_id'],group=group,fact_id=fact['fact_id'],
                            variants=fact['variants'],episode_id=identity,source=source,quote=text,
                            evidence_refs=item['evidence_refs'],observed_at=item['observed_at'],
                            status='AUDIT_ONLY_same_scene_surface_not_automatically_equivalent',
                            changes_primary_score=False))
    save('reports/memory_v03/alternate-evidence-audit.json',json.dumps(dict(
        protocol='posthoc evidence completeness audit, NOT a revised scoring set',
        source_result_sha256=sha256(artifact.read_bytes()).hexdigest(),items=alternatives),ensure_ascii=False,indent=2))
    lines=['# Adrian Memory v0.3 固定 A/B/C/D 结果','',
        '结论：改进召回扩大了新测试的正确来源候选覆盖；新排序在已见开发题中明显提高，但未在新测试中证明稳定的同预算收益。B、C、D 均保留为实验，不默认启用。原子卡片单独处理，所有卡均 pending，不进入生产或四组指标。','',
        '## 先说明评分的证据边界','',
        '原八题沿用原表面匹配 Gold；v0.2/v0.3 保留题沿用固定“片段匹配且命中标注消息位置”的规则。本轮发现同一事实在原会话重复出现，而 Gold 只标了一处。因此下面的“事实命中”严格说是冻结来源锚点覆盖，不是所有等价证据的完整事实覆盖。','',
        '本轮不根据输出补改 Gold 或主分数。另存 alternate-evidence-audit.json，登记可见的同场景同表面值；同值也可能不同属性，不能全部自动加分。不据此生成更好看的修订得分。','',
        '具体例子：租车确认码 E7739482M 在 B/D 的可见汇总中出现，Gold 只认可原消息 67，故主分仍未命中。折扣码 TC-8847 则从 A 第1名退到 B 第12名 / D 第13名，属于真实可见性损失。酒店会员号 HH294857301 被新排序放到第1名，而任务需要的是预订号/房号，说明“实体+数字”无法替代属性绑定。','']
    for split,groups in data['summaries'].items():
        lines += ['## '+split,'','| 组 | 候选命中/事实总数 | Top-8命中 | 可见命中 | 候选宏召回 | Recall@8宏 | 可见宏覆盖 |',
                  '|---|---:|---:|---:|---:|---:|---:|']
        for g,v in groups.items():
            lines.append(f"| {g} | {v['candidate_facts']}/{v['facts']} | {v['topk_facts']}/{v['facts']} | {v['visible_facts']}/{v['facts']} | {v['macro_candidate_recall']:.2%} | {v['macro_recall_at_8']:.2%} | {v['macro_visible_recall']:.2%} |")
        lines += ['','| 组 | cap前数量/题 | cap后去重候选/题 | 检索毫秒/题 | 上下文字节/题 |','|---|---:|---:|---:|---:|']
        for g,v in groups.items():
            lines.append(f"| {g} | {v['mean_pre_cap_count']:.2f} | {v['mean_candidate_count']:.2f} | {v['mean_search_ms']:.2f} | {v['mean_context_bytes']:.2f} |")
        lines += ['']
    lines += ['## 逐题对照','', '每格为“候选 / Top-8 / 固定预算可见”的事实组数量；各组 Top-8=8、候选 cap=100、预算2048紧凑 UTF-8 字节。','',
              '| 案例 | 分组 | 事实数 | A | B | C | D |','|---|---|---:|---:|---:|---:|---:|']
    for c in data['cases']:
        values=[f"{c['groups'][g]['candidate_facts']}/{c['groups'][g]['topk_facts']}/{c['groups'][g]['visible_facts']}" for g in 'ABCD']
        lines.append(f"| {c['case_id']} | {c['split']} | {c['fact_count']} | "+' | '.join(values)+' |')
    lines += ['','## 哪些方法有效、哪些没有证据支持','',
        '- B 保持原候选集不变：原八题可见来源从5/26到14/26，上一轮六题从2/23到6/23；说明候选内部确有可挖掘的信息。新六题仅从3/30到4/30，且宏可见覆盖25.00%降到10.32%，租车/酒店退化，不能据开发集提升就默认启用。',
        '- C 保持原排序：新测试候选命中10/30到16/30，候选上限仍100，平均去重数量91.33到93.50；收益不是直接提高Top-K。但模型可见仍3/30，证明新增候选没有自动变成有效上下文。',
        '- D：新测试Top-8命中6/30，但可见仅3/30；并未超过A，排序和召回组合也未产生稳定增益。多银行题中3个标注事实已经进Top-8，却被整条打包预算排除。',
        '- 新测试 A/B/C/D 微平均可见覆盖分别为10.00% / 13.33% / 10.00% / 10.00%；宏平均分别为25.00% / 10.32% / 25.00% / 7.54%。结论会随统计口径变化，必须同时展示原始计数与宏/微。',
        '- 扩展/相邻路径在这20题确实触发，但显式括号别名不保证自然题有足够覆盖。原子卡6个受控检查通过，只证明逐字引用/隔离/推断标记机制，不证明自动提炼质量。',
        '- 延迟为同进程每题三次调用中位数的跨题均值，含各组共用的完整池诊断；不是生产延迟，也不能与v0.2的绝对毫秒直接比较。没有计入LLM费用或精确Token。','',
        '## 有效性与合并建议','',
        '20题共79个冻结事实组；新六题30个原文事实按预定文件顺序从未用于v0.2的46题选出，标注先于新算法输出。标注者与执行者为同一代理，不是外部独立人工盲评；新Gold只覆盖原题的来源事实子集，不含完整业务推理和现实规则验证。','',
        '不建议合并为生产默认。可以保留离线排序/召回和诊断工具供评审；目前所有新增模块均未接入Runtime。原记忆卡片默认关闭，Goal-Value、确认、SQLite、系统提示和上下文窗口不变。付费模型、Judge、用户私有记录、正式长期写入均未执行。','',
        '下一轮只建议：(1) 在新冻结数据上补全等价证据并审查实体/属性限定；(2) 研究具体标识符类型、子句级问句/否定范围，避免邻居实体误传；(3) 单独设计预算内的事实级证据选择。上述项目本轮不实施。','',
        '## 版本、原始输出与复现','',
        f"基线 HEAD `{data['base_head']}` 加原 v0.2 实际未提交源码；逐文件版本冻结在 baseline.json / 交付收据，不冒称b90f104已包含本轮实现。新Gold SHA256 `{data['test_gold_sha256']}`。原始输出 SHA256 `{sha256(artifact.read_bytes()).hexdigest()}`。所有20题A的候选命中、最终ID与预算可见ID均与实施前基线相同。",'',
        '工作目录：`C:/Users/陈独秀/.codex/tmp/adrian-memory-v02`；PowerShell先设置 `$env:PYTHONPATH="src"`。','',
        '```powershell',
        '& C:/Users/陈独秀/Documents/agent-harness/.venv/Scripts/python.exe -X utf8 scripts/eval_memory_quality_v03.py --book-root C:/Users/陈独秀/.codex/tmp/ai-agent-book-memory-reference --report reports/memory_v03/abcd-reproduction.json.gz',
        '& C:/Users/陈独秀/Documents/agent-harness/.venv/Scripts/python.exe -X utf8 scripts/inspect_memory_quality_v03.py --report reports/memory_v03/abcd-fixed.json.gz --case layer1_07_car_rental --group D --view ranking',
        '& C:/Users/陈独秀/Documents/agent-harness/.venv/Scripts/python.exe -X utf8 -m pytest -q --tb=short',
        '& C:/Users/陈独秀/Documents/agent-harness/.venv/Scripts/python.exe -X utf8 -m pip wheel . --no-deps --no-build-isolation --no-index --wheel-dir dist/memory-v03-reproduction',
        '```','',
        '实际测试：full-regression.txt 中 1200 passed、3 live deselected、1已有警告。定向13个新测试通过，基线相关64个通过；RED/GREEN日志保留。构建、compileall、pip check和补丁检查见交付收据；无类型检查器/linter则NOT_RUN。未提交、未推送。']
    save('reports/memory_v03/evaluation_v03.md','\n'.join(lines))
    failures=['# Adrian Memory v0.3 遗漏与退化归因','',
        '只依据冻结结果作后验分析，不回写排序/Gold。下文的“遗漏”按原评分的唯一来源锚点判定；同场景替代原句单独审计，不自动视为同一事实。完整候选、路径、特征贡献、原文/位置/时间/hash在 abcd-fixed.json.gz。','',
        '## 可复核的主要问题','',
        '1. 候选外：新测试D的14个来源事实不在cap后候选。税务7项均未覆盖；现有词族/局部邻接无法把抽象“税务项目”稳定连接到利息、捐赠、资产成本等不同会话。邻接的有限锚点还会漏掉与查询不直接重叠的纯数值回答。','',
        '2. 排序：新测试D有10个来源事实在候选池却不在前八。实体匹配和“有数值”粗信号可能把会员号、地址等排在预订号/折扣码前；它们不是同一种编号。租车折扣码旧分0.4105第1名，新分2.3056却退至B第12/D第13；这是相对分数被其他片段超过，不是权限过滤。','',
        '3. 问句范围：酒店房号的原消息还带“需要订晚餐吗？”；整段问句惩罚作用到已陈述的房号，B/D名次27/33。角色/子句范围不足，不能把整段结尾问号当作所有内容不确定。','',
        '4. 预算：多银行题D的信用卡余额和两项投资余额已经进Top-8，但三个标注事实都没有装入2048字节。固定整条打包不按事实价值/字节成本选择，本轮保持打包器不变，不能把它算作召回失败。','',
        '5. 候选竞争：RRF cap仍100，却可能换掉原有证据。如上一轮订阅题A/B尚有1个来源候选，C/D变0；多车Tesla也可能被替换。增加路径不保证集合严格超集。','',
        '6. 评测遗漏：租车确认码存在于可见汇总，却因只标了另一条来源判未命中；税务收入也有同场景重复表面值。见 alternate-evidence-audit.json，需单独审查属性与限定。原八题Amex Gold为跨句组合、600–800估价无历史依据，保留v0.2审计，不补造事实。','',
        '以上阶段去向和名次有直接结果证据。不同特征各自造成多少损失仍是待验证假设：本轮只做排序/召回2×2，没有逐项权重消融，不把这些相关现象称为唯一因果。','']
    for split in data['summaries']:
        counts=Counter(f['groups']['D']['reason'] for c in data['cases'] if c['split']==split for f in c['fact_analysis'])
        failures+=['## '+split,'', 'D组去向：'+', '.join(f'{k}={v}' for k,v in sorted(counts.items())),'']
        for c in data['cases']:
            if c['split']!=split:continue
            missed=[f for f in c['fact_analysis'] if f['groups']['D']['reason']!='visible']
            if not missed:continue
            failures+=['### '+c['case_id'],'','| 未覆盖来源事实 | D去向 | 最佳D排名 | 原始来源位置/时间 |','|---|---|---:|---|']
            for f in missed:
                supports=f['groups']['D']['sources']
                best=min((s['rank'] for s in supports if s['rank'] is not None),default=None)
                source=f['literal_history_support'][0] if f['literal_history_support'] else None
                location=f"{source['conversation_id']} / {source['message_index']} / {source['observed_at']}" if source else '见v0.2争议事实审计'
                failures.append('| '+' / '.join(f['variants']).replace('|','/')+f" | {f['groups']['D']['reason']} | {best} | {location} |")
            failures+=['']
    failures+=['## 证据审计与停止边界','',
        f'后验替代证据提示共 {len(alternatives)} 条（按组/来源计数，不是新增正确事实数）。其中原文、位置和时间均保存；既不修改Gold，也不将同值不同属性全算命中。',
        '语义任务正确率、具体例外是否被模型理解、模型误用号码的比例均NOT_RUN。没有调用模型，不能把检索命中数称为任务完成率。','',
        '所有原v0.1/v0.2数据、报告和源码在本轮保留。当前未证明稳定的生产收益，不默认启用；本轮到此停止，等待设计评审。']
    save('reports/memory_v03/failure_analysis_v03.md','\n'.join(failures))


if __name__=='__main__':main()
