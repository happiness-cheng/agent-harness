# Adrian Memory v0.1 评测协议（实现前固定）

## 比较条件

主对照NONE、RECENCY、FREQUENCY、GOAL_VALUE。每题同一候选事实池、approved_fixture审批策略、历史时点、问题、Core预算（UTF-8序列化字节上界）、最终Top-K及检索预算。只切换Core选择策略。模型、参数、Judge统一为NOT_RUN；不以关键词覆盖冒充模型回答质量。不改既有M02 v2或书中gold。

## 数据与资格

书中第三章60 YAML（L1/L2/L3各20）以本地文件为准，记录提交与每份SHA256。oracle_approval只在显式approved_fixture离线入口使用；将历史原句保留为有来源角色的episode，用户原话DIRECT、助手原话INFERRED，不将后者冒充用户画像。审批比例100%是模拟数据条件，不是用户真实批准或语义提取准确率。生产工具/API不接受oracle参数。

按会话timestamp排序回放，每段历史只在该段完成时加入，后续会话只可使用已经可用的Memory。原数据无逐消息可信时间时，将整段会话timestamp作为粗粒度完成时点并明确记录假设，不虚构逐消息时间。提供给选择器的输入不包含expected_behavior、evaluation_criteria或gold_facts。最终问题可参与相关性计算；gold只在输出后用于评分。

Book raw episode表示不是完整语义记忆生成器，不能据此宣布GoalValue的真实任务收益。没有明确历史Goal标签时不从gold/最终预期中制造目标；报告该项缺失。Core晋升/排序不改变原句范围、证据类型或授权。较长原文拆成有范围的多个episode，不悄悄截掉尾部。

## 指标及分母

- 60题均运行时间回放、选择、FTS/图检索、预算及ID机制检查。
- 仅gold_facts已标注的8题计算必要事实的core_fact_coverage、fact_recall_at_k、first_relevant_mrr；未标注题为NOT_RUN，不拿空集当满分。
- facts按可接受表面形式匹配，非回答准确率；MRR为第一个含任一gold事实的返回Memory的倒数排名。精确/语义同义词边界沿用固定gold，不调gold凑分。
- 选择trace记ID+版本、源路径、去重、可用候选数、排序细项、预算、耗时；不默认存原文或问题。
- 记录同预算下每题GoalValue减Recency/Frequency的事实覆盖差、胜/平/负；8例公开小样本只给描述性结果，不宣称总体显著性、任务质量因果提升或真实反事实价值。
- L1/L2/L3真实回答四维分数、总体任务成功率、主动关联、幻觉、真实推理/Judge token、费用：无授权模型时均NOT_RUN。

## 私有行为数据

仅本地读取授权的deepseek-export目录，核对索引/角色边界/文件范围与散列，按updated将整段会话保守地划分早段和留出段，避免将后段记录用于早段查询。updated不是逐消息发生时间，不将它冒充精细行为时间。报告只含聚合数量/散列/解析覆盖，不含标题、内容、账号、原始ID或候选事实。正式导入数0。

当前无人工真实任务gold，不生成伪金标准；私有语义提取、个人价值判定、后段任务收益NOT_RUN。准备本地去标识评审材料不等于授权发给外部模型。真实数据不提交Git，也不出现在公开测试日志。

## 安全与失败

未经确认长期写入、跨用户来源、已删/过期仍召回、优先级更新擅自暂停其他目标，均要求0；以实际自动测试计，不用平均质量抵消。保留失败案例与版本；修复评测器错误另记版本，不改旧题或gold美化结果。外部费用/私人外传/生产迁移/远端发布暂停该动作，其余离线阶段继续。

参考：任务书第5—8节；本地书中chapter3/user-memory-evaluation/metrics.py与evaluator.py；[官方60题指南](https://github.com/bojieli/ai-agent-book/blob/dbc046eb896ac4e39aa19c7774c8bf49583b89a6/chapter3/user-memory-evaluation/TEST_FRAMEWORK_GUIDE.md)。

## 验收补强记录（不改变gold或importance权重）

初次book-full报告保留。安全自审补齐推断disputed、不同会话情景ID、删除派生输出、具体例外优先；动态条目增加observed_at/effective_from，预算仍2048但真实元数据开销计入。组路由低置信门0.15用于防止弱组命中阻断全局兜底；冻结独立回归反例后实现，不按书中gold调阈值。最终book-final以完整上下文字段和新代码散列复跑；版本差异不得冒充策略改进。
