"""从固定原始结果生成可阅读报告；不重跑检索，不修改 Gold。"""
import gzip
from hashlib import sha256
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def save(path, text):
    with (ROOT/path).open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(text.rstrip()+'\n')


def main():
    artifact = ROOT/'reports/memory_v02/abc-fixed.json.gz'
    data = json.loads(gzip.decompress(artifact.read_bytes()))
    metrics = {}
    for split in data['summaries']:
        subset = [c for c in data['cases'] if c['split'] == split]
        metrics[split] = {}
        for group in ('A','B','C'):
            matches = [m for c in subset for m in c['groups'][group]['fact_matches']]
            metrics[split][group] = dict(facts=len(matches),
                topk_covered=sum(bool(m['hit_ids']) for m in matches),
                visible_covered=sum(bool(m['visible_ids']) for m in matches),
                micro_fact_recall=sum(bool(m['hit_ids']) for m in matches)/len(matches),
                micro_visible_recall=sum(bool(m['visible_ids']) for m in matches)/len(matches))
    save('reports/memory_v02/derived-metrics.json', json.dumps(dict(
        source_sha256=sha256(artifact.read_bytes()).hexdigest(),metrics=metrics),ensure_ascii=False,indent=2))
    lines = ['# Adrian Memory v0.2 检索漏召归因', '',
        '这是原八题的离线开发诊断，不是模型任务失败分析。原始完整候选池、来源 Episode 和卡片保存在 `reports/memory_v02/abc-fixed.json.gz`。Gold 仅在检索后匹配；原 60 题和 Gold 均未修改。', '',
        '逐事实的全部来源、消息位置、字符范围、哈希、时间、Topic、情景、初始/合并候选、路径/分数/排名、Top-K/预算去向在原始 JSON 的 `raw_fact_attribution`。下表显示最靠前的一个来源；其他重复来源不隐藏，可用检查命令完整导出。', '',
        '注意：旧字面指标不能识别跨句合成事实。`source_exists_in_legal_history` 在原始运行中实际表示“连续表面字样存在”，两处例外以本报告与 `gold-audit-notes.md` 的原文审查为准。', '',
        '```powershell',
        '& C:/Users/陈独秀/Documents/agent-harness/.venv/Scripts/python.exe -X utf8 scripts/inspect_memory_retrieval_v02.py --report reports/memory_v02/abc-fixed.json.gz --case layer2_03_multiple_credit_cards --facts',
        '& C:/Users/陈独秀/Documents/agent-harness/.venv/Scripts/python.exe -X utf8 scripts/inspect_memory_retrieval_v02.py --report reports/memory_v02/abc-fixed.json.gz --case layer1_03_medical_appointment --pool pre-cap',
        '```', '',
        '所有原书历史在本轮最终截点均已可用；没有当前 directive 或请求级 Scenario 限制。真实过滤器仍先执行。原文权限/时间过滤未排除下列合法来源；缺失项不能一概归咎于时间或预算。', '']
    for case in data['cases']:
        if case['split'] != 'diagnostic_dev':
            continue
        a = case['groups']['A']
        lines += ['## '+case['case_id'], '', '问题：'+case['question'], '',
                  f"A：Top-8 事实覆盖 {a['fact_recall_at_k']:.2%}；预算可见 {a['visible_fact_recall']:.2%}；as_of={case['as_of']}。", '',
                  '| 必要事实组 | 证据位置（消息从 0 起） | 最佳合并排名 / 分数 | 检索去向 |',
                  '|---|---|---|---|']
        for f in case['raw_fact_attribution']:
            ordered = sorted(f['sources'], key=lambda s:(s['merged_rank'] is None, s['merged_rank'] or 999999))
            if ordered:
                best = ordered[0]
                label = '；'.join(f['variants']).replace('|','/')
                where = f"{best['conversation_id']} / {best['message_index']} / {best['timestamp']}"
                rank = f"{best['merged_rank']} / {best['score']:.6f}" if best['score'] is not None else '不在实际融合池'
                stages = ', '.join(sorted({s['cause'] for s in ordered}))
                lines.append(f'| {label} | {where} | {rank} | {stages} |')
            else:
                label = '；'.join(f['variants'])
                note = '原历史跨句可组合；连续表面不匹配' if 'Amex Gold' in label else '原历史无此金额；仅评分要求存在'
                lines.append(f'| {label} | 见 Gold 审查注记 | 无字面候选 | {note} |')
        trace = a['retrieval']['trace']
        detail = a['retrieval']['diagnostic']
        lines += ['', f"实际 cap 前融合候选 {len(detail['merged_before_cap'])}，cap 后 {len(detail['merged_candidates'])}；路由 Topic={trace['routed_topics']}，fallback={trace['global_fallback']}。", '']
        explanation = {
            'layer1_01_bank_account':'账号已有第 5 名命中；routing number 已在候选池但第 10 名，被 Top-8 截断。独立的纯数字回答不含问题词，FTS 不命中；重复陈述中的完整事实仍能进池。',
            'layer1_02_insurance_claim':'这是第八个没有完整检索漏召的对照例：三组事实都在 Top-8，问题出在有限预算没有装入第 8 名的完整 claim 条目。不能把 Core 或上下文遗漏说成检索器未找到。',
            'layer1_03_medical_appointment':'四个事实的最佳排名分别是 22、21、9、12，均进了候选池；主要是排序/Top-K，而不是历史不存在。短编号还存在完全无词重叠的独立来源。',
            'layer1_04_airline_booking':'确认码及两个座位均有原文，最佳排名 21/27/27；问题主要是大量通用对话词命中后的排序，未被权限、时间或预算过滤。',
            'layer2_01_multiple_vehicles':'Honda 在第 4 名；Tesla 在第 100 名；保单号最佳第 70 名，其他来源被 cap=100 截掉。这里同时有候选 cap 和 Top-K 两个损失点；不能只看最终八条。',
            'layer2_03_multiple_credit_cards':'Chase 的最佳来源排第 110 名，先被 cap=100 排除；3x 在第 87 名，被 Top-8 排除。Amex Gold 在原文由 American Express / Gold Card / Amex 多句建立，不符合旧连续字符串匹配，不能说模型/记忆忘记了这张卡。',
            'layer3_01_travel_coordination':'passport 是宽泛词，第 2 名已有命中；February 18 的独立来源与最终问题无词重叠，因此没有进入 FTS 初始池。航班码第 82 名、Priority Pass 第 48 名则属于排序截断。护照单词命中不等于理解到期风险。',
            'layer3_04_warranty_coordination':'MacBook 最佳第 30 名、Chase 第 22 名、延保日期第 82 名，均在池中但未进八条。600–800 美元维修费不在原历史，是评分要求中的外部估计；不能复制答案补记忆。此结论只谈给定证据，不核实现实保修/价格。',
        }[case['case_id']]
        lines += ['归因：'+explanation, '']
    lines += ['## 已证实与待验证', '',
        '- 已证实：上述阶段去向由同一次运行的候选 ID、路径和排名确定；14 题 A 的最终 ID 与 v0.1 逐项一致。',
        '- 假设：通用对话词导致相关证据稀释。这与候选规模和词重叠相符，但本轮未单独做停用词或 reranker 消融，不能认定为唯一原因。',
        '- 假设：更精炼、带实体和属性关联的卡片可解决跨句实体名称问题；本轮只做邻接上下文化，未验证一般语义提炼。',
        '- 没有证据支持把所有七题不完整召回归为一种原因；也没有证据支持改 Goal-Value 权重。']
    save('reports/memory_v02/retrieval_failure_analysis.md', '\n'.join(lines))
    evaluation = ['# Adrian Memory v0.2 固定 A/B/C 实验', '',
        '结论：上下文化卡片提高了部分题目的候选覆盖和 Top-8 宏平均召回，但没有改善独立测试总事实命中数，也没有改善同预算可见信息。当前证据不足以用它替换 Runtime 的默认记忆表示。C 在 14 个自然问题中没有触发结构化路径，与 B 完全相同；不能宣称实体检索已被这些题验证有效。', '',
        '运行一次 8 题开发诊断 + 6 题独立测试；未依据本轮结果调整排序或 Gold。无外部调用、无私人导入；模型任务/Judge/实际 Token 均 NOT_RUN。指标是检索组件代理指标，不是第二大脑任务成功率。', '']
    for split, values in data['summaries'].items():
        evaluation += ['## '+split, '',
            '| 组 | Hit@8 | Fact Recall@8（宏） | MRR | 候选覆盖 cap 后 / 前 | 预算可见覆盖（宏） | 实际事实命中（微） |',
            '|---|---:|---:|---:|---:|---:|---:|']
        for g, v in values.items():
            m = metrics[split][g]
            evaluation.append(f"| {g} | {v['hit_at_k']:.2%} | {v['fact_recall_at_k']:.2%} | {v['mrr']:.4f} | {v['candidate_fact_coverage']:.2%} / {v['full_pre_cap_fact_coverage']:.2%} | {v['visible_fact_recall']:.2%} | {m['topk_covered']}/{m['facts']} = {m['micro_fact_recall']:.2%} |")
        evaluation += ['', '| 组 | 原始存储字节/题 | 额外卡片字节/题 | 索引输入字节/题 | 上下文字节/题 | 检索毫秒/题 | cap 前 / 后去重候选 | 重复路径数/题 |',
            '|---|---:|---:|---:|---:|---:|---:|---:|']
        for g, v in values.items():
            evaluation.append(f"| {g} | {v['storage_canonical_bytes']:.1f} | {v['storage_card_sidecar_bytes']:.1f} | {v['indexed_text_bytes']:.1f} | {v['context_bytes']:.1f} | {v['retrieval_median_ms']:.2f} | {v['unique_before_cap']:.2f} / {v['unique_before_topk']:.2f} | {v['duplicate_hits_before_topk']:.2f} |")
        evaluation += ['', '字节是逻辑序列化成本，不是磁盘/Token；毫秒是各题三次普通调用中位数的跨题均值，不含完整离线诊断，不是线上 SLA。', '']
    evaluation += ['## 每题结果与退化', '', '| 案例 | 事实数 | A Top-8 / 可见 | B Top-8 / 可见 | C Top-8 / 可见 |', '|---|---:|---:|---:|---:|']
    for c in data['cases']:
        scores = [f"{c['groups'][g]['fact_recall_at_k']:.2%} / {c['groups'][g]['visible_fact_recall']:.2%}" for g in ('A','B','C')]
        evaluation.append(f"| {c['case_id']} | {c['gold_fact_count']} | "+' | '.join(scores)+' |')
    evaluation += ['',
        '独立六题 A/B 都只命中 4/23 个事实。B 赢了单事实网络账号题和一个订阅事实，却丢掉房屋交割题的两个事实，因此宏平均上升 12.78 个百分点，微平均没有增加。不能只展示上升的一列。开发八题 A 为 6/26，B 为 7/26；有开发收益，但不代表泛化。', '',
        'B 把独立题 cap 前候选池覆盖从 75.56% 提高到 100%，但 cap 后只有 65.56%，Top-8 39.44%，预算可见 19.44%。这条损失链说明下一轮不能只继续扩大召回池。开发集的原 Gold 还有一项连续表面不匹配、一项非历史事实，合法历史表面覆盖只有 92.71%，见归因报告。', '',
        '错误情景：跨情景同表面值碰撞代理在这批 Top-8 中为 0；没有全面语义情景标注，真实错误率 NOT_RUN，不能称“零错误”。独立测试的 5 个已标注具体例外/条件原句，A 覆盖 2 个，B/C 只覆盖 1 个；是否理解这些条件仍 NOT_RUN。', '',
        'C 的结构化实体路径在 14 题触发 0 次；这些问题大多只有 account/card/property 等普通类别词，未出现提取出的完整名字或显式缩写。AP 合成 Fixture 已验证别名路径、ID 去重与权限，但不能替代自然检索质量证据。C 的额外扫描有真实延迟成本，本轮没有收益。', '',
        '## 评测有效性与已知限制', '',
        '- 独立六题依据固定文件顺序选择并在检索前标注，不是随机大样本；同一执行代理标注和审查，未经独立人工复核。它是保留测试，不是新的 60 题全面模型验收。',
        '- 独立 Gold 是 23 个来源事实，不覆盖原六题完整业务推理/澄清/主动提醒标准；历史 assistant 话语只是来源陈述，不保证现实事实正确。',
        '- 邻接卡片保留原文和角色，但不做原子事实抽取、跨句实体解析、时间关系推理；前一句最多 600 字符只是上下文片段，原始 Episode 和来源全文仍全部保存。',
        '- 生产 Runtime 尚未启用卡片，SQLite/schema/权限未变；私人导入、人工确认生产流程集成、实时模型和 Judge 未执行。',
        '- 明确分开原 Top-8、2048 字节打包和 v0.1 Core 策略得分；本轮不报告 Recency/Frequency/Goal-Value 重新排名。', '',
        '## 下一轮只建议三件事（本轮不实施）', '',
        '1. 建立小批人工审核的原子卡片与实体—属性—情景绑定；优先覆盖 Amex/Gold Card 这种跨句事实，同时加入错误实体拼接反例。',
        '2. 在固定候选上单独比较排序与预算打包：保护数字标识、具体例外及多业务覆盖，报告微/宏召回和同预算可见事实，而不是只扩大候选池。',
        '3. 扩充并独立审核事实/情景/例外 Gold；隔离“历史可召回”“需要推理”“需要外部知识”，然后再安排有预算的真实任务/Judge 验证。', '',
        '## 复现命令', '', '工作目录：`C:/Users/陈独秀/.codex/tmp/adrian-memory-v02`。先在 PowerShell 设置 `$env:PYTHONPATH="src"`。', '', '```powershell',
        '& C:/Users/陈独秀/Documents/agent-harness/.venv/Scripts/python.exe -X utf8 scripts/eval_memory_retrieval_v02.py --book-root C:/Users/陈独秀/.codex/tmp/ai-agent-book-memory-reference --report reports/memory_v02/abc-reproduction.json.gz',
        '& C:/Users/陈独秀/Documents/agent-harness/.venv/Scripts/python.exe -X utf8 -m pytest -q --tb=short',
        '& C:/Users/陈独秀/Documents/agent-harness/.venv/Scripts/python.exe -X utf8 -m pip wheel . --no-deps --no-build-isolation --no-index --wheel-dir dist/memory-v02-reproduction',
        '```', '',
        f"实际原始输出 SHA256：`{sha256(artifact.read_bytes()).hexdigest()}`；完整源码哈希在原始 JSON `source_hashes`，基线/data 哈希在 `baseline.json`，独立 Gold SHA256 为 `{data['independent_gold_sha256']}`。旧 v0.1 所有报告未覆盖。"]
    save('reports/memory_v02/evaluation_v02.md', '\n'.join(evaluation))


if __name__ == '__main__':
    main()
