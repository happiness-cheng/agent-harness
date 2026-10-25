# v0.4 剩余问题与失败证据

本报告不修改Gold、权重、已冻结结果。所有事实来源/上下文/字符范围/绑定/Oracle见context-fixed.json.gz，可由检查CLI逐项还原。

## 核心发现

1. 固定预算内的排序效用仍然不是事实价值。多银行四事实有621字节可行解，P2却继续选择前排低价值材料；Oracle与实际之差证明选择器有改进空间，而非应一律提高预算。
2. 摘录不是无条件省字节。来源、时间、情景和多条字符范围会增加成本；一些原句本就很短。P1在多个已见分组降低可见覆盖，不能只用删除了寒暄证明有效。
3. P2丢掉新药物题的一个原本可见事实：效用只按倒数排名、有限编号绑定和查询词覆盖，无法识别抽象“血压药全部细节”中的完整剂量及家属归属。
4. 局部绑定在已见租车/酒店各恢复两个锚点，但新三题未增加主覆盖。安装服务码、药物剂量、照护收益等属性未知，未知并不算正确绑定；不能用0/2已审错误推断普遍准确。
5. 完整证据不仅是取值。一些药物/保单需要另一个来源证明具体人物、状态或限定，原Top-K并未同时包含这些来源。装配不能凭空补齐，来源范围名CVS/layer3也不等于病人或投保人。
6. 完整性评测仍有边界：部分已审span过粗，含非必要寒暄，缺失它们不必然造成语义损失；P0没有显式观察时间被“完整证据”规则扣分，也不代表其全部文字事实错误。保留原分并明确该代理指标限制，不依据结果改标注。

## 新测试逐事实去向（R0_P0 / P1 / P2）

| 案例/事实 | 候选 | Top-K | P0可见 | P1可见 | P2可见 | 定位 |
|---|---|---|---|---|---|---|
| layer1_09_home_security / ADT-7789234 | True | True | True | True | True | 可见/完整字段需另查 |
| layer1_09_home_security / SVC-44821 | True | True | True | True | True | 可见/完整字段需另查 |
| layer2_07_multiple_medications / Lisinopril 20mg | False | False | False | False | False | 候选缺口 |
| layer2_07_multiple_medications / Amlodipine 5mg | True | True | True | True | False | P2选择退化 |
| layer2_07_multiple_medications / Losartan 50mg | False | False | False | False | False | 候选缺口 |
| layer3_07_eldercare_coordination / $5,000 | False | False | False | False | False | 候选缺口 |
| layer3_07_eldercare_coordination / $2,000 | True | True | True | True | True | 可见/完整字段需另查 |
| layer3_07_eldercare_coordination / $168 | False | False | False | False | False | 候选缺口 |
| layer3_07_eldercare_coordination / 5-7 business days | True | False | False | False | False | 排序截断 |

## 证据保留与启用条件

原始单来源指标、批准的等价取值、严格全字段/显式来源时间保留分别报告。拒绝表保留仅可用房间、回声问句、旧日额、异属性月租；这些不能自动算作等价证据。没有因为看到结果去扩大正例或删除失败。

机械限定句检查通过只说明规则要求的句子没被截断；不能覆盖所有隐含指代、完整实体、时间推理或临床/保险正确性。当前是有界离线原文摘录实验，不建议生产默认启用。

下一步只建议先评审更精确的必要字段span与多来源事实单元，再决定是否优化任务相关性或打包效用。需要新保留样本；本轮到此停止。
