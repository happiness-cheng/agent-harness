# Adrian Memory v0.4 固定上下文实验

结论：不建议默认启用P1/P2或事实绑定排序。P1增加了显式来源/时间信息，但新测试主覆盖没有提高；P2退化；绑定排序的收益目前来自已见开发题。保留实现与负结果供评审。

基线已独立固化为5a962bd，不再以b90f104冒称可复现实际v0.3。实现、规则和新Gold冻结于提交 6e9f1a4787c53ee4c6d8956d86468e0a2cf6e114 后运行一次23题、六配置。原20题均为开发，新3题9事实只作小批保留测试，同代理制作/审查，不是专家盲评。

R0=原候选＋v0.3实验排序B；R1=同一候选以显式属性绑定稳定分层。P0=旧整条，P1=完整原句提取包，P2=同样的包做预算选择。Top-8=8、候选cap=100、预算2048紧凑UTF-8字节全部固定；R1不改旧权重。

## 主指标：原始来源锚点不变

### seen_original_dev

| 配置 | 候选命中 | Top-K命中 | 可见命中 | Candidate Recall | Top-K Recall | Visible Coverage | 平均字节 |
|---|---:|---:|---:|---:|---:|---:|---:|
| R0_P0 | 22/26 | 14/26 | 14/26 | 84.62% | 53.85% | 53.85% | 1934.9 |
| R0_P1 | 22/26 | 14/26 | 12/26 | 84.62% | 53.85% | 46.15% | 1899.8 |
| R0_P2 | 22/26 | 14/26 | 11/26 | 84.62% | 53.85% | 42.31% | 1920.2 |
| R1_P0 | 22/26 | 14/26 | 14/26 | 84.62% | 53.85% | 53.85% | 1934.9 |
| R1_P1 | 22/26 | 14/26 | 12/26 | 84.62% | 53.85% | 46.15% | 1899.8 |
| R1_P2 | 22/26 | 14/26 | 11/26 | 84.62% | 53.85% | 42.31% | 1920.2 |

### seen_previous_test_now_dev

| 配置 | 候选命中 | Top-K命中 | 可见命中 | Candidate Recall | Top-K Recall | Visible Coverage | 平均字节 |
|---|---:|---:|---:|---:|---:|---:|---:|
| R0_P0 | 11/23 | 6/23 | 6/23 | 47.83% | 26.09% | 26.09% | 1911.8 |
| R0_P1 | 11/23 | 6/23 | 5/23 | 47.83% | 26.09% | 21.74% | 1957.5 |
| R0_P2 | 11/23 | 6/23 | 5/23 | 47.83% | 26.09% | 21.74% | 1957.5 |
| R1_P0 | 11/23 | 7/23 | 7/23 | 47.83% | 30.43% | 30.43% | 1940.0 |
| R1_P1 | 11/23 | 7/23 | 6/23 | 47.83% | 30.43% | 26.09% | 1967.5 |
| R1_P2 | 11/23 | 7/23 | 6/23 | 47.83% | 30.43% | 26.09% | 1967.5 |

### seen_frozen_test

| 配置 | 候选命中 | Top-K命中 | 可见命中 | Candidate Recall | Top-K Recall | Visible Coverage | 平均字节 |
|---|---:|---:|---:|---:|---:|---:|---:|
| R0_P0 | 10/30 | 6/30 | 4/30 | 33.33% | 20.00% | 13.33% | 1939.2 |
| R0_P1 | 10/30 | 6/30 | 2/30 | 33.33% | 20.00% | 6.67% | 1940.2 |
| R0_P2 | 10/30 | 6/30 | 2/30 | 33.33% | 20.00% | 6.67% | 1940.2 |
| R1_P0 | 10/30 | 10/30 | 8/30 | 33.33% | 33.33% | 26.67% | 1928.8 |
| R1_P1 | 10/30 | 10/30 | 6/30 | 33.33% | 33.33% | 20.00% | 1914.5 |
| R1_P2 | 10/30 | 10/30 | 6/30 | 33.33% | 33.33% | 20.00% | 1914.5 |

### frozen_test

| 配置 | 候选命中 | Top-K命中 | 可见命中 | Candidate Recall | Top-K Recall | Visible Coverage | 平均字节 |
|---|---:|---:|---:|---:|---:|---:|---:|
| R0_P0 | 5/9 | 4/9 | 4/9 | 55.56% | 44.44% | 44.44% | 1952.7 |
| R0_P1 | 5/9 | 4/9 | 4/9 | 55.56% | 44.44% | 44.44% | 1862.7 |
| R0_P2 | 5/9 | 4/9 | 3/9 | 55.56% | 44.44% | 33.33% | 1958.3 |
| R1_P0 | 5/9 | 4/9 | 4/9 | 55.56% | 44.44% | 44.44% | 1952.7 |
| R1_P1 | 5/9 | 4/9 | 4/9 | 55.56% | 44.44% | 44.44% | 1862.7 |
| R1_P2 | 5/9 | 4/9 | 3/9 | 55.56% | 44.44% | 33.33% | 1958.3 |

## 独立源审指标（不能混入主分）

仅旧租车/酒店4项＋新三题9项，共13个事实做了等价来源审阅，批准项须实体/属性/值/时间/限定一致。问句回声、仅“可用”的房间、旧150美元日额及150美元月租均明确拒绝。原Gold、原成绩未回填。

“已审等价值可见”只说明批准的取值来源出现；“完整文本”要求所列主体/属性/值/否定/时间等必要原句全部可见；“完整证据”还要求这些来源的情景与观察时间显式可见。P0没有显式观察时间，因此其完整证据数为0并不等于原文事实全部错误。

审核span目前部分仍是整句/整消息，可能包含非必要礼貌语，属于保守的已审span保留代理，不是可靠的开放域语义完整性证明。这个缺口原样保留，不在看到分数后缩短标注来提高结果。机械限定句保留率另列，不能拿它代替语义审核。

| 分组/配置 | 已审事实数 | 已审值可见 | 完整文本 | 完整证据 | 完整保留率 | 错误属性/已审预测 | 已审预测/全部预测 |
|---|---:|---:|---:|---:|---:|---:|---:|
| seen_frozen_test/R0_P0 | 4 | 1 | 1 | 0 | 0.00% | 0/1 | 1/1 |
| seen_frozen_test/R0_P1 | 4 | 1 | 0 | 0 | 0.00% | 0/1 | 1/1 |
| seen_frozen_test/R0_P2 | 4 | 1 | 0 | 0 | 0.00% | 0/1 | 1/1 |
| seen_frozen_test/R1_P0 | 4 | 4 | 4 | 0 | 0.00% | 0/7 | 7/8 |
| seen_frozen_test/R1_P1 | 4 | 4 | 1 | 1 | 25.00% | 0/7 | 7/8 |
| seen_frozen_test/R1_P2 | 4 | 4 | 1 | 1 | 25.00% | 0/7 | 7/8 |
| frozen_test/R0_P0 | 9 | 4 | 2 | 0 | 0.00% | 0/2 | 2/2 |
| frozen_test/R0_P1 | 9 | 4 | 2 | 2 | 50.00% | 0/2 | 2/2 |
| frozen_test/R0_P2 | 9 | 3 | 2 | 2 | 66.67% | 0/2 | 2/2 |
| frozen_test/R1_P0 | 9 | 4 | 2 | 0 | 0.00% | 0/2 | 2/2 |
| frozen_test/R1_P1 | 9 | 4 | 2 | 2 | 50.00% | 0/2 | 2/2 |
| frozen_test/R1_P2 | 9 | 3 | 2 | 2 | 66.67% | 0/2 | 2/2 |

属性错误率的分母仅是已审锚点上实际产生的绑定，未知/未产出不算正确预测。新测试仅有2个可评价绑定、错误0；不能称为所有事实零错误。药物剂量/照护收益等不在本轮编号规则覆盖范围。所有配置机械限定句保留检查通过，也不能因此声称完整语义保留100%。

## Oracle：每段损失分开

Gold只在决策完成后进入精确mask/条数/最低字节DP；不上线、不参与P2效用。候选并集与最多8条覆盖忽略预算；预算Oracle使用真实的整条或提取包及相同JSON字节成本，不构造新事实。这里只给冻结来源/取值指标的上界，不是复杂多来源语义完整证明或模型成功率。

| 分组/排序 | 候选并集 | 最优Top8 | 候选预算最优 | 实际Top8 | 此Top8整条预算最优 | P0实际 | 提取后无限预算 | 提取包预算最优 | P1实际 | P2实际 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| seen_original_dev/R0 | 22 | 22 | 22 | 14 | 14 | 14 | 14 | 14 | 12 | 11 |
| seen_original_dev/R1 | 22 | 22 | 22 | 14 | 14 | 14 | 14 | 14 | 12 | 11 |
| seen_previous_test_now_dev/R0 | 11 | 11 | 11 | 6 | 6 | 6 | 5 | 5 | 5 | 5 |
| seen_previous_test_now_dev/R1 | 11 | 11 | 11 | 7 | 7 | 7 | 6 | 6 | 6 | 6 |
| seen_frozen_test/R0 | 10 | 10 | 10 | 6 | 6 | 4 | 6 | 6 | 2 | 2 |
| seen_frozen_test/R1 | 10 | 10 | 10 | 10 | 10 | 8 | 10 | 10 | 6 | 6 |
| frozen_test/R0 | 5 | 5 | 5 | 4 | 4 | 4 | 4 | 4 | 4 | 3 |
| frozen_test/R1 | 5 | 5 | 5 | 4 | 4 | 4 | 4 | 4 | 4 | 3 |

多银行题：四个来源事实已经在R0 Top-8，两个原始Episode合计621字节就可保留四个事实，但P0/P1/P2实际均只留两个。这里不是2048字节硬上限，而是选择效用偏向高排名无效材料。新药物题P2把原本可见的剂量来源丢掉，是明确退化。

## 检索与装配耗时

| 分组/配置 | 检索ms | 绑定分层ms（R1额外） | 装配ms |
|---|---:|---:|---:|
| seen_original_dev/R0_P0 | 96.51 | 0.00 | 31.92 |
| seen_original_dev/R0_P1 | 96.51 | 0.00 | 32.09 |
| seen_original_dev/R0_P2 | 96.51 | 0.00 | 36.09 |
| seen_original_dev/R1_P0 | 96.51 | 38.63 | 27.62 |
| seen_original_dev/R1_P1 | 96.51 | 38.63 | 28.39 |
| seen_original_dev/R1_P2 | 96.51 | 38.63 | 36.02 |
| seen_previous_test_now_dev/R0_P0 | 77.78 | 0.00 | 29.82 |
| seen_previous_test_now_dev/R0_P1 | 77.78 | 0.00 | 34.04 |
| seen_previous_test_now_dev/R0_P2 | 77.78 | 0.00 | 38.60 |
| seen_previous_test_now_dev/R1_P0 | 77.78 | 34.46 | 30.44 |
| seen_previous_test_now_dev/R1_P1 | 77.78 | 34.46 | 28.27 |
| seen_previous_test_now_dev/R1_P2 | 77.78 | 34.46 | 33.61 |
| seen_frozen_test/R0_P0 | 97.25 | 0.00 | 42.56 |
| seen_frozen_test/R0_P1 | 97.25 | 0.00 | 42.77 |
| seen_frozen_test/R0_P2 | 97.25 | 0.00 | 42.25 |
| seen_frozen_test/R1_P0 | 97.25 | 42.01 | 33.63 |
| seen_frozen_test/R1_P1 | 97.25 | 42.01 | 34.42 |
| seen_frozen_test/R1_P2 | 97.25 | 42.01 | 41.63 |
| frozen_test/R0_P0 | 96.17 | 0.00 | 41.87 |
| frozen_test/R0_P1 | 96.17 | 0.00 | 34.74 |
| frozen_test/R0_P2 | 96.17 | 0.00 | 46.30 |
| frozen_test/R1_P0 | 96.17 | 68.78 | 36.80 |
| frozen_test/R1_P1 | 96.17 | 68.78 | 35.67 |
| frozen_test/R1_P2 | 96.17 | 68.78 | 46.33 |

单次固定本地实验的跨题平均，包含权威状态读取、完整池诊断和共用证据包构建。P0也执行了诊断用句切分；存在顺序/缓存影响，不能据这些毫秒断言比生产旧打包更快，亦不是SLA或模型费用。Provider Token/Judge/真实模型成绩均NOT_RUN。

## 逐题结果

每格Top-K命中/可见命中，主指标沿用原来源锚点。

| 案例 | 事实数 | R0_P0 | R0_P1 | R0_P2 | R1_P0 | R1_P1 | R1_P2 |
|---|---:|---:|---:|---:|---:|---:|---:|
| layer1_01_bank_account | 2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 |
| layer1_02_insurance_claim | 3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| layer1_03_medical_appointment | 4 | 3/3 | 3/3 | 3/2 | 3/3 | 3/3 | 3/2 |
| layer1_04_airline_booking | 3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| layer2_01_multiple_vehicles | 3 | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 |
| layer2_03_multiple_credit_cards | 3 | 1/1 | 1/1 | 1/1 | 1/1 | 1/1 | 1/1 |
| layer3_01_travel_coordination | 4 | 1/1 | 1/0 | 1/0 | 1/1 | 1/0 | 1/0 |
| layer3_04_warranty_coordination | 4 | 1/1 | 1/0 | 1/0 | 1/1 | 1/0 | 1/0 |
| layer1_05_internet_service | 1 | 0/0 | 0/0 | 0/0 | 1/1 | 1/1 | 1/1 |
| layer1_06_credit_card_app | 1 | 1/1 | 1/1 | 1/1 | 1/1 | 1/1 | 1/1 |
| layer2_02_multiple_properties | 5 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |
| layer2_04_multiple_subscriptions | 6 | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 |
| layer3_02_medical_insurance_coordination | 5 | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 |
| layer3_03_home_purchase_coordination | 5 | 2/2 | 2/1 | 2/1 | 2/2 | 2/1 | 2/1 |
| layer1_07_car_rental | 2 | 0/0 | 0/0 | 0/0 | 2/2 | 2/2 | 2/2 |
| layer1_08_hotel_reservation | 2 | 0/0 | 0/0 | 0/0 | 2/2 | 2/2 | 2/2 |
| layer2_05_multiple_bank_accounts | 7 | 4/2 | 4/2 | 4/2 | 4/2 | 4/2 | 4/2 |
| layer2_06_multiple_insurance_policies | 6 | 2/2 | 2/0 | 2/0 | 2/2 | 2/0 | 2/0 |
| layer3_05_tax_preparation_synthesis | 7 | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 |
| layer3_06_business_expansion_coordination | 6 | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 |
| layer1_09_home_security | 2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 |
| layer2_07_multiple_medications | 3 | 1/1 | 1/1 | 1/0 | 1/1 | 1/1 | 1/0 |
| layer3_07_eldercare_coordination | 4 | 1/1 | 1/1 | 1/1 | 1/1 | 1/1 | 1/1 |

## 版本、验证与复现

工作目录 `C:/Users/陈独秀/.codex/tmp/adrian-memory-v04`。代码源版本 6e9f1a4787c53ee4c6d8956d86468e0a2cf6e114，数据和实现hash均在原始输出。基线为5a962bd；所有旧20题R0_P0与v0.3 B的候选、Top-K、可见ID及主分一致。

原始输出SHA256 `f7cf642408fb84cc817ba01a439a1513604540190a7b59ba4ea8965250f61b0b`；新Gold `bed32caaff82391bf57c9e4c818e0dd6d2e5e62cd1ac6c30f1b41b54e453a974`；源审 `2313b7dc424b01e1cfb3ec52cfd21a5b3522a19ddc8d392d83fd6fffd364c834`。所有旧数据、报告、文件hash保留。

```powershell
$env:PYTHONPATH="src"
& C:/Users/陈独秀/Documents/agent-harness/.venv/Scripts/python.exe -X utf8 scripts/eval_memory_context_v04.py --book-root C:/Users/陈独秀/.codex/tmp/ai-agent-book-memory-reference --report reports/memory_v04/context-reproduction.json.gz
& C:/Users/陈独秀/Documents/agent-harness/.venv/Scripts/python.exe -X utf8 scripts/inspect_memory_context_v04.py --report reports/memory_v04/context-fixed.json.gz --case layer2_05_multiple_bank_accounts --configuration R0_P2 --view oracle
& C:/Users/陈独秀/Documents/agent-harness/.venv/Scripts/python.exe -X utf8 -m pytest -q --tb=short
& C:/Users/陈独秀/Documents/agent-harness/.venv/Scripts/python.exe -X utf8 -m pip wheel . --no-deps --no-build-isolation --no-index --wheel-dir dist/memory-v04-reproduction
```

实际完整回归1211 passed、3 live deselected、1已有警告；定向11项通过，RED/GREEN输出保留。compileall、pip check、离线wheel通过；类型/lint工具不可用则NOT_RUN。无外部模型、私人导入、数据库迁移或远端推送。

建议：保留离线研究工具，不启用P1/P2或分层排序；先完善字段级证据审核和任务相关性，再选择一项做新冻结实验。本轮不继续扩大。
