# Adrian Memory v0.2 检索漏召归因

这是原八题的离线开发诊断，不是模型任务失败分析。原始完整候选池、来源 Episode 和卡片保存在 `reports/memory_v02/abc-fixed.json.gz`。Gold 仅在检索后匹配；原 60 题和 Gold 均未修改。

逐事实的全部来源、消息位置、字符范围、哈希、时间、Topic、情景、初始/合并候选、路径/分数/排名、Top-K/预算去向在原始 JSON 的 `raw_fact_attribution`。下表显示最靠前的一个来源；其他重复来源不隐藏，可用检查命令完整导出。

注意：旧字面指标不能识别跨句合成事实。`source_exists_in_legal_history` 在原始运行中实际表示“连续表面字样存在”，两处例外以本报告与 `gold-audit-notes.md` 的原文审查为准。

```powershell
& C:/Users/陈独秀/Documents/agent-harness/.venv/Scripts/python.exe -X utf8 scripts/inspect_memory_retrieval_v02.py --report reports/memory_v02/abc-fixed.json.gz --case layer2_03_multiple_credit_cards --facts
& C:/Users/陈独秀/Documents/agent-harness/.venv/Scripts/python.exe -X utf8 scripts/inspect_memory_retrieval_v02.py --report reports/memory_v02/abc-fixed.json.gz --case layer1_03_medical_appointment --pool pre-cap
```

所有原书历史在本轮最终截点均已可用；没有当前 directive 或请求级 Scenario 限制。真实过滤器仍先执行。原文权限/时间过滤未排除下列合法来源；缺失项不能一概归咎于时间或预算。

## layer1_01_bank_account

问题：What was my checking account number again? I need it to set up my direct deposit at work.

A：Top-8 事实覆盖 50.00%；预算可见 50.00%；as_of=2024-11-15T10:30:00.000001+00:00。

| 必要事实组 | 证据位置（消息从 0 起） | 最佳合并排名 / 分数 | 检索去向 |
|---|---|---|---|
| 4429853327 | bank_setup_001 / 87 / 2024-11-15 10:30:00 | 5 / 0.401005 | lexical_no_match, top_k_cutoff, visible |
| 123006800 | bank_setup_001 / 61 / 2024-11-15 10:30:00 | 10 / 0.299674 | top_k_cutoff |

实际 cap 前融合候选 69，cap 后 69；路由 Topic=[]，fallback=True。

归因：账号已有第 5 名命中；routing number 已在候选池但第 10 名，被 Top-8 截断。独立的纯数字回答不含问题词，FTS 不命中；重复陈述中的完整事实仍能进池。

## layer1_02_insurance_claim

问题：I need to follow up on my accident claim. What was my claim number and when is the adjuster supposed to call me?

A：Top-8 事实覆盖 100.00%；预算可见 66.67%；as_of=2024-11-18T14:15:00.000001+00:00。

| 必要事实组 | 证据位置（消息从 0 起） | 最佳合并排名 / 分数 | 检索去向 |
|---|---|---|---|
| CLM-2024-894327 | insurance_claim_001 / 95 / 2024-11-18 14:15:00 | 8 / 0.351401 | context_budget, top_k_cutoff |
| Patricia Wong | insurance_claim_001 / 61 / 2024-11-18 14:15:00 | 1 / 0.426691 | top_k_cutoff, visible |
| 24-48；24 to 48 | insurance_claim_001 / 61 / 2024-11-18 14:15:00 | 1 / 0.426691 | visible |

实际 cap 前融合候选 96，cap 后 96；路由 Topic=[]，fallback=True。

归因：这是第八个没有完整检索漏召的对照例：三组事实都在 Top-8，问题出在有限预算没有装入第 8 名的完整 claim 条目。不能把 Core 或上下文遗漏说成检索器未找到。

## layer1_03_medical_appointment

问题：I need to confirm my upcoming doctor's appointment. What day and time was it scheduled for, and which doctor am I seeing?

A：Top-8 事实覆盖 0.00%；预算可见 0.00%；as_of=2024-11-12T09:00:00.000001+00:00。

| 必要事实组 | 证据位置（消息从 0 起） | 最佳合并排名 / 分数 | 检索去向 |
|---|---|---|---|
| November 21；Nov 21；Nov. 21 | medical_intake_001 / 123 / 2024-11-12 09:00:00 | 22 / 0.190832 | top_k_cutoff |
| 2:30 | medical_intake_001 / 108 / 2024-11-12 09:00:00 | 21 / 0.195178 | top_k_cutoff |
| Robert Chen | medical_intake_001 / 95 / 2024-11-12 09:00:00 | 9 / 0.261454 | top_k_cutoff |
| MV-789234 | medical_intake_001 / 117 / 2024-11-12 09:00:00 | 12 / 0.255210 | lexical_no_match, top_k_cutoff |

实际 cap 前融合候选 82，cap 后 82；路由 Topic=[]，fallback=True。

归因：四个事实的最佳排名分别是 22、21、9、12，均进了候选池；主要是排序/Top-K，而不是历史不存在。短编号还存在完全无词重叠的独立来源。

## layer1_04_airline_booking

问题：Can you remind me of my flight confirmation number and what seats I selected? I need to add it to my calendar.

A：Top-8 事实覆盖 0.00%；预算可见 0.00%；as_of=2024-11-10T16:30:00.000001+00:00。

| 必要事实组 | 证据位置（消息从 0 起） | 最佳合并排名 / 分数 | 检索去向 |
|---|---|---|---|
| PAC-778K4M | airline_booking_001 / 73 / 2024-11-10 16:30:00 | 21 / 0.241810 | top_k_cutoff |
| 12C | airline_booking_001 / 109 / 2024-11-10 16:30:00 | 27 / 0.217382 | top_k_cutoff |
| 14C | airline_booking_001 / 109 / 2024-11-10 16:30:00 | 27 / 0.217382 | top_k_cutoff |

实际 cap 前融合候选 88，cap 后 88；路由 Topic=[]，fallback=True。

归因：确认码及两个座位均有原文，最佳排名 21/27/27；问题主要是大量通用对话词命中后的排序，未被权限、时间或预算过滤。

## layer2_01_multiple_vehicles

问题：I need to schedule service for my car. Can you tell me what services I have scheduled?

A：Top-8 事实覆盖 33.33%；预算可见 33.33%；as_of=2024-11-20T09:30:00.000001+00:00。

| 必要事实组 | 证据位置（消息从 0 起） | 最佳合并排名 / 分数 | 检索去向 |
|---|---|---|---|
| Honda Accord | auto_insurance_001 / 5 / 2024-10-15 11:00:00 | 4 / 0.365551 | candidate_cap, visible |
| Tesla Model 3 | auto_insurance_001 / 6 / 2024-10-15 11:00:00 | 100 / 0.067027 | top_k_cutoff |
| FS-447291 | auto_service_001 / 90 / 2024-11-20 09:30:00 | 70 / 0.083400 | candidate_cap, top_k_cutoff |

实际 cap 前融合候选 150，cap 后 100；路由 Topic=[]，fallback=True。

归因：Honda 在第 4 名；Tesla 在第 100 名；保单号最佳第 70 名，其他来源被 cap=100 截掉。这里同时有候选 cap 和 Top-K 两个损失点；不能只看最终八条。

## layer2_03_multiple_credit_cards

问题：I want to book a flight and wondering which of my cards I should use to get the best rewards?

A：Top-8 事实覆盖 0.00%；预算可见 0.00%；as_of=2024-11-18T09:15:00.000001+00:00。

| 必要事实组 | 证据位置（消息从 0 起） | 最佳合并排名 / 分数 | 检索去向 |
|---|---|---|---|
| Chase Freedom；Freedom Unlimited | fraud_alert_003 / 8 / 2024-11-18 09:15:00 | 110 / 0.085659 | candidate_cap, lexical_no_match |
| Amex Gold；Amex Gold Card | 见 Gold 审查注记 | 无字面候选 | 原历史跨句可组合；连续表面不匹配 |
| 3x | amex_application_002 / 7 / 2024-11-10 10:30:00 | 87 / 0.135491 | top_k_cutoff |

实际 cap 前融合候选 194，cap 后 100；路由 Topic=[]，fallback=True。

归因：Chase 的最佳来源排第 110 名，先被 cap=100 排除；3x 在第 87 名，被 Top-8 排除。Amex Gold 在原文由 American Express / Gold Card / Amex 多句建立，不符合旧连续字符串匹配，不能说模型/记忆忘记了这张卡。

## layer3_01_travel_coordination

问题：I'm finalizing my trip to Tokyo in January. Is there anything I need to take care of before I go?

A：Top-8 事实覆盖 25.00%；预算可见 25.00%；as_of=2024-11-22T10:00:00.000001+00:00。

| 必要事实组 | 证据位置（消息从 0 起） | 最佳合并排名 / 分数 | 检索去向 |
|---|---|---|---|
| passport | passport_renewal_001 / 11 / 2024-06-15 14:00:00 | 2 / 0.373047 | candidate_cap, context_budget, lexical_no_match, top_k_cutoff, visible |
| February 18；Feb 18 | passport_renewal_001 / 6 / 2024-06-15 14:00:00 | 不在实际融合池 | lexical_no_match |
| DELTA-JMK892 | flight_booking_002 / 51 / 2024-11-22 10:00:00 | 82 / 0.080863 | lexical_no_match, top_k_cutoff |
| Priority Pass | credit_card_003 / 31 / 2024-10-05 15:30:00 | 48 / 0.142476 | lexical_no_match, top_k_cutoff |

实际 cap 前融合候选 142，cap 后 100；路由 Topic=[]，fallback=True。

归因：passport 是宽泛词，第 2 名已有命中；February 18 的独立来源与最终问题无词重叠，因此没有进入 FTS 初始池。航班码第 82 名、Priority Pass 第 48 名则属于排序截断。护照单词命中不等于理解到期风险。

## layer3_04_warranty_coordination

问题：My laptop screen just cracked. What are all my options for getting it repaired or replaced based on my coverage?

A：Top-8 事实覆盖 0.00%；预算可见 0.00%；as_of=2024-11-20T10:00:00.000001+00:00。

| 必要事实组 | 证据位置（消息从 0 起） | 最佳合并排名 / 分数 | 检索去向 |
|---|---|---|---|
| MacBook | laptop_purchase_001 / 6 / 2024-03-15 14:00:00 | 30 / 0.181367 | candidate_cap, top_k_cutoff |
| Chase Sapphire Reserve；Chase | phone_damage_001 / 17 / 2024-10-30 15:00:00 | 22 / 0.191527 | candidate_cap, lexical_no_match, top_k_cutoff |
| March 2026；March 15, 2026 | laptop_purchase_001 / 66 / 2024-03-15 14:00:00 | 82 / 0.105368 | top_k_cutoff |
| 600-800；600 | 见 Gold 审查注记 | 无字面候选 | 原历史无此金额；仅评分要求存在 |

实际 cap 前融合候选 174，cap 后 100；路由 Topic=[]，fallback=True。

归因：MacBook 最佳第 30 名、Chase 第 22 名、延保日期第 82 名，均在池中但未进八条。600–800 美元维修费不在原历史，是评分要求中的外部估计；不能复制答案补记忆。此结论只谈给定证据，不核实现实保修/价格。

## 已证实与待验证

- 已证实：上述阶段去向由同一次运行的候选 ID、路径和排名确定；14 题 A 的最终 ID 与 v0.1 逐项一致。
- 假设：通用对话词导致相关证据稀释。这与候选规模和词重叠相符，但本轮未单独做停用词或 reranker 消融，不能认定为唯一原因。
- 假设：更精炼、带实体和属性关联的卡片可解决跨句实体名称问题；本轮只做邻接上下文化，未验证一般语义提炼。
- 没有证据支持把所有七题不完整召回归为一种原因；也没有证据支持改 Goal-Value 权重。
