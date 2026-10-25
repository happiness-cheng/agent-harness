# Adrian Memory v0.4：证据装配与局部事实绑定

## 本轮结论

完成23题、同候选池下2种排序×3种装配的固定实验，另做小批等价来源审核和精确离线Oracle。**不建议默认启用新装配或绑定排序。** 新三题P0/P1均4/9标注来源事实可见，P2降为3/9。绑定仅在已见题显示收益，未证明泛化。真实模型/Judge/任务成功率均NOT_RUN。

所有新增能力只在离线实验入口使用；生产Runtime、ContextManager、默认检索、Goal-Value、SQLite/schema、长期写入确认与删除机制不改。无外部模型、私人导入或远端推送。

## P0：独立、可复现的版本

- 工作区：`C:/Users/陈独秀/.codex/tmp/adrian-memory-v04`，分支 `feat/adrian-memory-v04-local`。原v0.2/v0.3未提交工作区原样保留。
- 长原文回查修复独立提交 `cd3f17b`。
- 实际v0.3精确基线：`5a962bd46835a21048f4568e84372f849c1cefcf`。75份继承文件的工作树与提交blob逐字节SHA256相同；基线完整测试1200 passed、3 live deselected。
- 实现、规则、新Gold/源审在评测前冻结为 `6e9f1a4787c53ee4c6d8956d86468e0a2cf6e114`。原始输出记录此提交及全部67个Python模块/运行脚本hash；不能以b90f104代表这套实现。
- Git默认换行转换会破坏旧hash，使用路径级 `.gitattributes -text` 固化原始字节，索引重新规范后验证。没有修改旧文件或改写历史；历史patch的上下文空白只作为格式例外。
- 完整差异审计、继承清单、提交记录与基线输出见 `reports/memory_v04/baseline-audit.md` / `inherited-manifest.json` / `baseline-commits.json`。

参考资料：[原书第三章 / user-memory-evaluation](https://github.com/bojieli/ai-agent-book/tree/dbc046eb896ac4e39aa19c7774c8bf49583b89a6/chapter3/user-memory-evaluation)。原60题和Gold不改，本轮不冒称完成60题模型任务测试。

## 职责与调用顺序

```text
原Memory权威层 -> owner/时效/场景/撤回过滤 -> 原候选池100
    -> R0：v0.3实验排序B
    -> R1：同池局部属性绑定稳定分层
        -> Top-8固定
            -> P0：旧整条原文
            -> P1：逐字证据句包
            -> P2：同样的句包做预算子集选择
                -> 2048 UTF-8字节内的离线参考材料
完成以上决策之后 -> 原Gold评分 / 小批等价源审 / Gold Oracle
```

Gold、源审判断及Oracle结果均不进入候选、排序或装配。更换Gold不改变六份实际上下文的测试已通过。这里只模拟可见参考材料，没有发送真实LLM。

## P1：提取与预算装配

`evidence_context.py` 每次从原权威层重新读取合法来源，按ID去重，拒绝外部/过期/其他场景/撤回ID。来源provider也按合法时点读取，不泄露未来Topic元数据。

P0复用旧JSON结构：完整内容、memory_id、evidence_type、evidence_refs。与旧20题v0.3 B的最终ID、字节数和主分逐项相同。

P1把候选拆成带原始字符范围的完整句/分号子句，选查询相关原句；否定、条件、时间及显式身份句强制保留，指代只能补同一原消息的前句。没有相关句时保留原消息，不拼出新事实、不跨消息认领号码。每包还保留source ID、scene、entity_scope、观察/有效时间、证据类型与引用。

```python
selected = relevant_sentence_ids
selected |= qualifier_and_identity_sentence_ids
quotes = [original_sentences[i] for i in sorted(selected)]
assert all(body[q['start']:q['end']] == q['text'] for q in quotes)
if serialized_bytes(current + [complete_package]) <= budget:
    current.append(complete_package)
```

P1按Top-8原顺序放整包，放不下就跳过；P2只在这8个包的256个子集内选择。固定效用为倒数排名之和、匹配请求的不同局部绑定、查询词覆盖；完整性先于效用，平分选更小字节。冗余仅保守地在同情景/时点、完全相同摘录上处理，不合并正式Memory或不同场景。超过8个包时拒绝无界穷举。

取舍：保持原句可以审查，但保留来源/时间/范围有额外字节成本。句切分与关键词规则不能证明开放域的语义完整；主体不明时只写来源范围/unknown，不将其变成已确认主体。P1不是自动语义摘要或长期事实写入。

## P2：局部属性绑定

`fact_binding.py` 只在当前原句子句中识别显式标签和值，区分预订/确认号、会员号、订单号、房号、账号、routing、电话号、PIN、折扣码、座位号。绑定保存label/value/clause的原文范围与asserted/question/negated/conditional状态，实体不从邻句继承。

R1不调整v0.3任何数值权重，只做稳定分层：明确匹配请求属性且来源范围相容的陈述优先；未知/条件次之；其余保持R0次序。一个消息后半句的订餐提问不再让前半句已陈述房号整体失去优先级。

原文provider只是 `entity_scope`，不是病人/投保人/具体业务主体。未识别的安装服务码、剂量、保障金额仍是未知，不靠相邻数字猜测。它也不解决所有预订状态变更/否定范围/跨句实体解析。

实际：已见租车和酒店各恢复2个原标注事实，旧网络账号也恢复1个；新三题未增加主覆盖。类型、条件和实体确认不充分，故不默认启用。

## P3：原始Gold与独立源审

- 原20题均是已见开发数据，保留全部旧锚点与原成绩。
- 新3题从其余40题每层按文件名取第一题：安防、家属药物、养老照护，共9个来源事实。先制标注和hash，冻结实现后才运行。
- 源审覆盖这9项加旧租车/酒店4项，共13项。每个批准替代来源都有实体、属性、值、时间及限定的审查记录，以及必须保留的原句span；回声问句、可用房间、旧150日额、150月租明确拒绝。
- 单独报告“批准取值出现”“必要文本span齐全”“再加显式场景/观察时间齐全”。不把仅同值当等价，不把它们回填原Gold。
- 当前源审由同一执行代理完成，非独立专家盲评；部分span偏粗还含寒暄。因此完整保留率是保守span/来源代理，不是语义正确性证明。新测试仅2个已审绑定预测、错误0，不能声称普遍零错误。

## P4：Oracle与真实边界

`context_evaluation.py` 在实际输出固定后，用fact-mask/条数的最低字节DP求精确上界：候选并集、最多8条覆盖、候选预算上界、实际Top-8内整条/提取包预算上界。Oracle只使用真实已有证据，不构造答案；它仅对冻结来源事实评分精确，不能替代复杂多来源语义证明。

多银行案例的4个Top-8事实仅需2条原文、621字节即可装入，但三种实际策略均只保留2个。说明当前效用选择错误，不是预算硬限制。提取包的新元数据也可能令短原句变贵；新药物题P2丢掉一个剂量来源，属于保留的退化。

所有指标和逐题结果、完整性/绑定审计分母、字节与延迟、Oracle损失链见 `reports/memory_v04/evaluation_v04.md`；剩余问题见 `failure_analysis_v04.md`。P0测时也包含诊断用句切分，单次本地耗时不能证明生产加速。精确Token、Judge和真实任务成功率均NOT_RUN。

## 验收及未解决问题

新增11项定向测试及旧完整回归：**1211 passed、3 live deselected、1已有警告**。覆盖原owner/确认/时效/撤回/去重/目标生命周期/Runtime窗口边界，并增加片段逐字引用、混合问句、限定保留、未来元数据、Gold隔离和Oracle边界。

离线wheel、源码对齐、compileall、pip check、差异和历史hash检查均执行；没有类型/lint工具则NOT_RUN。源码和报告均为本地提交，未推送、未迁移数据库或启用新默认。公开Fixture实验日志不属于生产私人记忆的物理删除链路；原删除机制本轮不变。

建议下一轮先完善字段级证据审核与任务相关性，再选一个装配缺口做新冻结实验。当前不继续增加功能。
