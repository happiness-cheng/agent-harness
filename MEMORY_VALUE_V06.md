# Adrian Memory v0.6：任务感知取证与证据使用验证

## 结论

**本轮不建议把B1或B2默认启用，也不建议直接进入生产试用。** 可以保留并合并评测设施及实验入口，生产仍沿用B0。

在B0/B1均有有效输出的5道公开开发题上，必要事实提及覆盖为B0 4/16、B1 7/16；规则冻结后首次运行的6个新合成样本上为B0 13/13、B1 12/13。B1改善了一些证据进入上下文的问题，同时出现账户用途冲突、未持有产品参与推荐等退化。它不是稳定收益。

B2实际消耗30次模型请求、48次只读工具调用，15题全部在第三次请求前因Runtime上下文门禁暂停，没有最终回答。工具后备好的证据不等于已送给模型：实际请求日志显示，30次B2请求均没有正文证据条目。不能把待发送投影计入模型可见事实，更不能将预算阻塞记为模型语义错误。

总计真实请求90次、52份符合输出Schema的最终回答；总用量峰值计价估算0.1197285美元，账单实付NOT_AVAILABLE。没有API请求重试、外部Judge、私人记录导入或远端推送。

## 基线、分组和冻结

- 起点：`a399c79a25326f0f83b9fb1fc72b35abb94de006`，保留v0.1～v0.5原始文件和失败案例。
- 工作区：`C:/Users/陈独秀/.codex/tmp/adrian-memory-v06`；分支`feat/adrian-memory-v06-local`。
- `f3b9ea9`冻结B1/B2决策代码；之后创建6个新合成样本。它们是实现者编写的首次验证样本，不是独立人工盲测，更不是用户私人记忆。
- `ae68102`冻结15题五组请求和工具范围。9题v05已见样本只作开发诊断；6个新样本另列。
- `864397c42dc115622ec6741944ef9e7a680016e2`为实际发送请求的代码版本。之后只作本地报告与来源审查，不改检索策略或重新发题。

| 组 | 实现 | 单元调用上限 | 参考上限 |
|---|---|---:|---:|
| A | 无长期Memory | 1 | 不提供记忆 |
| B0 | 原生产bind_runtime_memory / Core+FTS | 1 | 4096 UTF-8字节 |
| B1 | 任务感知候选、局部属性绑定、原句预算装配 | 1 | 4096 UTF-8字节 |
| B2 | 原Runtime + 三个只读Memory工具 | 3模型、4工具 | 每次投影4096字节 |
| C | 截至任务时点的合法原句Oracle | 1 | 4096 UTF-8字节 |

共同模型deepseek-flash、high、输出上限4096 Token。共同System和当前问题，相同单次32768保守总单位；B2额外累计65536保守单位，费用和轮次单列。单位按序列化UTF-8字节加预留估计，不是精确Tokenizer。真实用量采用API usage。

A/B0/B1/C每题一次，B2可主动决定是否搜索/读取；没有为了让流程好看而强制模型走某个Oracle ID。每题组序轮转，各组客户端和会话独立。新的共用提示词允许注册的只读工具，因此本轮结果不能与v05不同提示词的成绩直接作因果比较。

## 实现与取舍

没有新建Runtime、长期数据库、语义卡片生产写入或Goal-Value权重。新增模块均通过实验CLI进入，不注册到生产应用：

| 文件 | 责任 |
|---|---|
| `src/min_agent/long_memory/task_evidence.py` | 复用v03任务词族/实体/显式别名及邻消息候选、v04局部绑定与原句片段，在完整序列化预算内选证据，不预留无关Core空间 |
| `src/min_agent/long_memory/active_evidence.py` | 复用生产只读搜索/主题读取、补按已披露ID读取；实验上下文投影复用原Runtime工具循环、Session、预算和删除依赖 |
| `src/min_agent/long_memory/evidence_budget.py` | 发送前数量/Token上界/费用预留、真实用量核验、未知用量或错误即停止，无自动重试 |
| `scripts/eval_memory_value_v06.py` | 固定数据、五组制包、离线控制及明确授权的真实批次 |
| `scripts/report_memory_value_v06.py` | 完整消息/回答、实际送达与待发送投影分离、离线代理和真实输出展示 |
| `scripts/audit_memory_value_v06.py` | 对固定输出导入执行代理来源审查，绑定回答哈希，不调用Judge、不改Gold |

B1识别任务词项、属性、实体和显式别名，候选最多100、最终Top8；同一memory_id只取一次。沿用旧实验的候选/绑定/片段基础，改的是组合入口与完整元数据预算核算，不声称重新发明了语义检索。角色、时间、场景、证据类型及“历史陈述非实时核实”随原句提供；检查时发现旧片段器遗漏verify核实限定，新增测试复现后仅在新策略补留原句。

B2复用`search_long_memory`和`read_long_memory_topic`。窄接口`read_long_memory_evidence`只接受本次已披露ID，每次重新核验owner、合法时点、场景、有效性及撤回。工具历史仅记ID，正文从权威存储重新投影。注册在实验工具集中，不能执行写入、业务工具、跨owner查询或任意文件读取。沿用既有删除派生依赖；新增测试实际验证删除后受控助手派生内容被撤回。

简化核心接线如下（完整实现见上述模块）：

```python
# B1：只接当前任务、可信Memory与预算，没有Gold/答案/Oracle入口。
evidence = select_task_evidence(memory, context, query, budget=4096)
messages = group_messages(query.text, query.as_of, evidence['view'])

# B2：换实验上下文投影，循环和工具分发仍使用现有Runtime。
registry, state = active_registry(memory, context, query, budget=4096)
limits = BudgetLimits(model_calls=3, tool_calls=4)
ledger = BudgetLedger(limits)
runtime = ProjectedRuntime(
    model, registry, budget=ledger.start_segment(limits),
    query=query, view_builder=registry.memory_view,
)
result = runtime.run_session(query.text, store=memory.sessions, context=context)
```

这类似于准备材料和交付材料两个动作：B2已把材料备好，却在发送前被预算门禁拦下；不能说阅卷人已经看过。

## P4怎样判定

严格检查JSON语法、三字段Schema、字段类型、重复引用ID、引用是否属于**本次真正可见**的有效证据。解析失败单列，不修JSON、不补请求、不填0分冒充语义成绩。

语义判定另由执行Codex代理逐题读原始历史、实际提供材料与回答，保存回答哈希、引用原句、明确的不足和7条未支持核心断言。没有独立外部Judge或用户逐条审核，因此属于非盲来源审查，不是权威准确率。

原始Gold及词面代理保留。语义覆盖列允许日期简写、中文“已取消”、Gold Card与American Express场景的组合、Core等价到期来源；这些只用于离线审查。Oracle的passport原句依赖已提供的passport_renewal场景元数据，已显式说明，不能把词面缺词直接当漏召。

“必要事实提及覆盖”不等于完整任务成功或正确持有关系；“已提供事实使用率”必须同时看总覆盖，少提供证据可能让使用率虚高。来源支持率定义为本轮已审的决策相关核心断言是否发现未支持内容，不冒充全文所有常识的事实核查。

## 本地初始化失败及授权收据

原授权包SHA256：`928f30ec43c34455c1b5bb52f95f58900ecb311484853db2d67f4c1e8cd8f45c`。用户确认`call_5565b31d9db94375a440ed0c57df20c4`，最多105次、2美元、15个公开/合成场景、三个只读工具、无Judge/私人数据/重试。

首次启动漏查了现有客户端单实例最多100次的配置约束，传入105导致本地初始化失败，API请求0次、费用0。补了会实际构建75个假客户端的回归测试，验证每个单元客户端上限3次、总账本仍限105次、所有客户端关闭。

修复只涉及运行器的客户端生命周期，15题全部消息、材料、Gold、算法、工具范围及费用上限逐项一致；沿用用户已明确授权的同一业务范围，未重复申请或增加额度。原包、原授权和0请求失败收据均未改写。修复包SHA256：`8c2e782b7cbdceff2107fad1f9de64a65778fdcef1e347c2f315320642eadc22`；等价性证明在`client-fix-equivalence.json`，派生授权记录注明原授权哈希与依据，不声称用户另行确认过新哈希。

真实批次90次；旧启动0次；合计90≤105，峰值估算0.1197285≤2美元。未用完的15次不会自动用于新实验。

## 交付导航

- [实际结果、指标与成本](reports/memory_v06/evaluation_v06.md)
- [逐题失败归因](reports/memory_v06/failure_analysis_v06.md)
- [完整System、每题一次问题、五组原始输出/证据/审查](reports/memory_v06/delivery-report/complete-comparison.md)
- [真实请求/回答事件](reports/memory_v06/live-client-fix.jsonl)；[运行收据](reports/memory_v06/run-receipt.json)
- [来源审查](reports/memory_v06/source-audit.json)；[人工审查决策与限制](reports/memory_v06/source-audit-decisions.json)
- [复现命令与验证](reports/memory_v06/commands.md)

## 保留限制与停止点

完整60题、独立盲审、重复采样方差、私人记录、自动提炼与真实确认质量、生产长期任务：NOT_RUN。完整回归1257 passed、3 live deselected，不能用这个数字证明自然语言效果。

B1仍是词项/局部绑定方案，不能可靠识别持有关系、提问与断言、账户用途和跨句依赖；合成样本规模小且B0有天花板。B2的字节预留和原生提供商状态造成了实际可用性问题；当前错误码合并单次/累计上下文门禁，日志不能准确区分每例命中了哪一分支，不能说是模型服务端窗口耗尽。

本轮结束，不再调权重、加预算、扩大数据或补发请求。下一轮如继续，优先审查事实/问题及持有状态绑定、证据依赖闭包、能让搜索读取后仍留出回答空间的预算；这些是建议，不是已实现功能。下一轮应将本轮6个新样本转为已见开发集，并另设冻结验证样本。
