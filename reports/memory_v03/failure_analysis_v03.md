# Adrian Memory v0.3 遗漏与退化归因

只依据冻结结果作后验分析，不回写排序/Gold。下文的“遗漏”按原评分的唯一来源锚点判定；同场景替代原句单独审计，不自动视为同一事实。完整候选、路径、特征贡献、原文/位置/时间/hash在 abcd-fixed.json.gz。

## 可复核的主要问题

1. 候选外：新测试D的14个来源事实不在cap后候选。税务7项均未覆盖；现有词族/局部邻接无法把抽象“税务项目”稳定连接到利息、捐赠、资产成本等不同会话。邻接的有限锚点还会漏掉与查询不直接重叠的纯数值回答。

2. 排序：新测试D有10个来源事实在候选池却不在前八。实体匹配和“有数值”粗信号可能把会员号、地址等排在预订号/折扣码前；它们不是同一种编号。租车折扣码旧分0.4105第1名，新分2.3056却退至B第12/D第13；这是相对分数被其他片段超过，不是权限过滤。

3. 问句范围：酒店房号的原消息还带“需要订晚餐吗？”；整段问句惩罚作用到已陈述的房号，B/D名次27/33。角色/子句范围不足，不能把整段结尾问号当作所有内容不确定。

4. 预算：多银行题D的信用卡余额和两项投资余额已经进Top-8，但三个标注事实都没有装入2048字节。固定整条打包不按事实价值/字节成本选择，本轮保持打包器不变，不能把它算作召回失败。

5. 候选竞争：RRF cap仍100，却可能换掉原有证据。如上一轮订阅题A/B尚有1个来源候选，C/D变0；多车Tesla也可能被替换。增加路径不保证集合严格超集。

6. 评测遗漏：租车确认码存在于可见汇总，却因只标了另一条来源判未命中；税务收入也有同场景重复表面值。见 alternate-evidence-audit.json，需单独审查属性与限定。原八题Amex Gold为跨句组合、600–800估价无历史依据，保留v0.2审计，不补造事实。

以上阶段去向和名次有直接结果证据。不同特征各自造成多少损失仍是待验证假设：本轮只做排序/召回2×2，没有逐项权重消融，不把这些相关现象称为唯一因果。

## original_dev

D组去向：candidate_gap=2, gold_surface_missing_or_external=2, ranking_cutoff=8, visible=14

### layer1_03_medical_appointment

| 未覆盖来源事实 | D去向 | 最佳D排名 | 原始来源位置/时间 |
|---|---|---:|---|
| Robert Chen | ranking_cutoff | 56 | medical_intake_001 / 95 / 2024-11-12T09:00:00+00:00 |

### layer2_01_multiple_vehicles

| 未覆盖来源事实 | D去向 | 最佳D排名 | 原始来源位置/时间 |
|---|---|---:|---|
| Honda Accord | ranking_cutoff | 36 | auto_insurance_001 / 5 / 2024-10-15T11:00:00+00:00 |
| Tesla Model 3 | candidate_gap | None | auto_insurance_001 / 6 / 2024-10-15T11:00:00+00:00 |
| FS-447291 | ranking_cutoff | 90 | auto_service_001 / 89 / 2024-11-20T09:30:00+00:00 |

### layer2_03_multiple_credit_cards

| 未覆盖来源事实 | D去向 | 最佳D排名 | 原始来源位置/时间 |
|---|---|---:|---|
| Chase Freedom / Freedom Unlimited | ranking_cutoff | 9 | chase_card_activation_001 / 4 / 2024-10-05T14:00:00+00:00 |
| Amex Gold / Amex Gold Card | gold_surface_missing_or_external | None | 见v0.2争议事实审计 |

### layer3_01_travel_coordination

| 未覆盖来源事实 | D去向 | 最佳D排名 | 原始来源位置/时间 |
|---|---|---:|---|
| February 18 / Feb 18 | candidate_gap | None | passport_renewal_001 / 6 / 2024-06-15T14:00:00+00:00 |
| DELTA-JMK892 | ranking_cutoff | 71 | flight_booking_002 / 51 / 2024-11-22T10:00:00+00:00 |
| Priority Pass | ranking_cutoff | 43 | credit_card_003 / 27 / 2024-10-05T15:30:00+00:00 |

### layer3_04_warranty_coordination

| 未覆盖来源事实 | D去向 | 最佳D排名 | 原始来源位置/时间 |
|---|---|---:|---|
| MacBook | ranking_cutoff | 87 | laptop_purchase_001 / 3 / 2024-03-15T14:00:00+00:00 |
| March 2026 / March 15, 2026 | ranking_cutoff | 31 | laptop_purchase_001 / 66 / 2024-03-15T14:00:00+00:00 |
| 600-800 / 600 | gold_surface_missing_or_external | None | 见v0.2争议事实审计 |

## previous_test_now_dev

D组去向：candidate_gap=12, ranking_cutoff=5, visible=6

### layer1_05_internet_service

| 未覆盖来源事实 | D去向 | 最佳D排名 | 原始来源位置/时间 |
|---|---|---:|---|
| VEL-89923476 | ranking_cutoff | 10 | internet_setup_001 / 77 / 2024-11-08T11:00:00+00:00 |

### layer2_02_multiple_properties

| 未覆盖来源事实 | D去向 | 最佳D排名 | 原始来源位置/时间 |
|---|---|---:|---|
| HO-558847291 | candidate_gap | None | primary_home_insurance_001 / 65 / 2024-09-12T11:00:00+00:00 |
| HO-558897435 | ranking_cutoff | 25 | rental_property_insurance_002 / 87 / 2024-10-28T15:30:00+00:00 |

### layer2_04_multiple_subscriptions

| 未覆盖来源事实 | D去向 | 最佳D排名 | 原始来源位置/时间 |
|---|---|---:|---|
| $22.99 | candidate_gap | None | netflix_support_001 / 3 / 2024-09-15T14:00:00+00:00 |
| $19.99 | candidate_gap | None | disney_support_001 / 5 / 2024-10-22T10:30:00+00:00 |
| $19.99 | candidate_gap | None | hbo_support_001 / 5 / 2024-11-10T16:15:00+00:00 |
| $15.49 | candidate_gap | None | netflix_support_001 / 7 / 2024-09-15T14:00:00+00:00 |
| usually covers Standard with ads | candidate_gap | None | netflix_support_001 / 69 / 2024-09-15T14:00:00+00:00 |
| Some Verizon unlimited plans include Max Standard | candidate_gap | None | hbo_support_001 / 43 / 2024-11-10T16:15:00+00:00 |

### layer3_02_medical_insurance_coordination

| 未覆盖来源事实 | D去向 | 最佳D排名 | 原始来源位置/时间 |
|---|---|---:|---|
| I don't see it yet | ranking_cutoff | 45 | orthopedic_consultation_002 / 25 / 2024-11-20T14:30:00+00:00 |
| November 28th at 2 PM | candidate_gap | None | orthopedic_consultation_002 / 67 / 2024-11-20T14:30:00+00:00 |
| $4,000 | candidate_gap | None | insurance_enrollment_001 / 11 / 2024-01-15T10:00:00+00:00 |
| in-network with BlueCross PPO | candidate_gap | None | anesthesia_billing_003 / 11 / 2024-11-22T16:00:00+00:00 |
| anti-nausea medication preventively | candidate_gap | None | anesthesia_billing_003 / 73 / 2024-11-22T16:00:00+00:00 |

### layer3_03_home_purchase_coordination

| 未覆盖来源事实 | D去向 | 最佳D排名 | 原始来源位置/时间 |
|---|---|---:|---|
| November 20th, 2024 | candidate_gap | None | mortgage_preapproval_001 / 57 / 2024-08-20T11:00:00+00:00 |
| gift letter | ranking_cutoff | 65 | mortgage_preapproval_001 / 9 / 2024-08-20T11:00:00+00:00 |
| December 13th | ranking_cutoff | 43 | moving_company_quote_003 / 49 / 2024-11-18T10:00:00+00:00 |

## frozen_test

D组去向：candidate_gap=14, context_budget=3, ranking_cutoff=10, visible=3

### layer1_07_car_rental

| 未覆盖来源事实 | D去向 | 最佳D排名 | 原始来源位置/时间 |
|---|---|---:|---|
| E7739482M | ranking_cutoff | 17 | car_rental_001 / 67 / 2024-11-14T10:15:00+00:00 |
| TC-8847 | ranking_cutoff | 13 | car_rental_001 / 87 / 2024-11-14T10:15:00+00:00 |

### layer1_08_hotel_reservation

| 未覆盖来源事实 | D去向 | 最佳D排名 | 原始来源位置/时间 |
|---|---|---:|---|
| HSD-445782 | ranking_cutoff | 11 | hotel_booking_001 / 69 / 2024-11-09T19:30:00+00:00 |
| 1912 | ranking_cutoff | 33 | hotel_booking_001 / 41 / 2024-11-09T19:30:00+00:00 |

### layer2_05_multiple_bank_accounts

| 未覆盖来源事实 | D去向 | 最佳D排名 | 原始来源位置/时间 |
|---|---|---:|---|
| $2,847.93 | context_budget | 8 | chase_banking_001 / 17 / 2024-08-20T09:30:00+00:00 |
| $45,680.00 | candidate_gap | None | wellsfargo_banking_001 / 5 / 2024-09-12T14:00:00+00:00 |
| $287,543.21 | ranking_cutoff | 14 | wellsfargo_banking_001 / 11 / 2024-09-12T14:00:00+00:00 |
| $156,892.45 | context_budget | 7 | fidelity_investment_001 / 5 / 2024-10-28T11:00:00+00:00 |
| $87,234.18 | context_budget | 7 | fidelity_investment_001 / 5 / 2024-10-28T11:00:00+00:00 |

### layer2_06_multiple_insurance_policies

| 未覆盖来源事实 | D去向 | 最佳D排名 | 原始来源位置/时间 |
|---|---|---:|---|
| $500 | candidate_gap | None | auto_insurance_001 / 7 / 2024-09-10T11:00:00+00:00 |
| $2,500 | ranking_cutoff | 12 | home_insurance_001 / 23 / 2024-10-05T14:30:00+00:00 |
| at renewal | ranking_cutoff | 45 | home_insurance_001 / 69 / 2024-10-05T14:30:00+00:00 |
| doesn't show flood coverage | ranking_cutoff | 54 | home_insurance_001 / 33 / 2024-10-05T14:30:00+00:00 |
| 10% | ranking_cutoff | 10 | home_insurance_001 / 39 / 2024-10-05T14:30:00+00:00 |

### layer3_05_tax_preparation_synthesis

| 未覆盖来源事实 | D去向 | 最佳D排名 | 原始来源位置/时间 |
|---|---|---:|---|
| $8,500 | candidate_gap | None | home_purchase_001 / 13 / 2024-02-15T10:00:00+00:00 |
| $31,000 | candidate_gap | None | home_purchase_001 / 19 / 2024-02-15T10:00:00+00:00 |
| $7,500 | candidate_gap | None | home_purchase_001 / 25 / 2024-02-15T10:00:00+00:00 |
| $22,500 | candidate_gap | None | stock_sale_001 / 29 / 2024-06-20T14:00:00+00:00 |
| $25,200 | candidate_gap | None | charity_donation_001 / 17 / 2024-08-15T11:00:00+00:00 |
| 8283 | candidate_gap | None | charity_donation_001 / 43 / 2024-08-15T11:00:00+00:00 |
| $18,000 | candidate_gap | None | side_business_001 / 6 / 2024-10-10T16:00:00+00:00 |

### layer3_06_business_expansion_coordination

| 未覆盖来源事实 | D去向 | 最佳D排名 | 原始来源位置/时间 |
|---|---|---:|---|
| 2-3 weeks | candidate_gap | None | business_loan_application / 45 / 2024-08-15T00:00:00+00:00 |
| November 15th | ranking_cutoff | 28 | commercial_lease_negotiation / 39 / 2024-09-22T00:00:00+00:00 |
| $18,000 | candidate_gap | None | commercial_lease_negotiation / 46 / 2024-09-22T00:00:00+00:00 |
| 8-12 weeks | candidate_gap | None | equipment_supplier_discussion / 49 / 2024-10-28T00:00:00+00:00 |
| 208V three-phase | candidate_gap | None | equipment_supplier_discussion / 79 / 2024-10-28T00:00:00+00:00 |
| mid-November | candidate_gap | None | equipment_supplier_discussion / 93 / 2024-10-28T00:00:00+00:00 |

## 证据审计与停止边界

后验替代证据提示共 14 条（按组/来源计数，不是新增正确事实数）。其中原文、位置和时间均保存；既不修改Gold，也不将同值不同属性全算命中。
语义任务正确率、具体例外是否被模型理解、模型误用号码的比例均NOT_RUN。没有调用模型，不能把检索命中数称为任务完成率。

所有原v0.1/v0.2数据、报告和源码在本轮保留。当前未证明稳定的生产收益，不默认启用；本轮到此停止，等待设计评审。
