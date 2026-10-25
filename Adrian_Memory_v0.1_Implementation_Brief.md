Adrian Memory v0.1 — 编程 Agent 执行任务书
执行对象：本地 happiness-cheng/agent-harness 工作区中的 Codex / Claude Code / 其他 Coding Agent。
任务类型：在现有 Python Agent Runtime 中迭代实现、运行单测及离线/可用的真实模型评测，产出可审阅的结果；不是单独写概念设计。
截止定义：以验收证据为准，不以模型宣称完成为准。发布日期基线 2026-10-25。优先以本地当前代码为事实来源，公共仓库可能落后于本地版本。

0. 一句话目标
构建“长期记忆按用户确认写入；情景与证据可追溯；组优先、图关联、全局兜底检索并在最终 Top-K 之前去重；Core Memory 在预算内依据目标/决策价值选择”的最小可用版本。然后针对相同数据、预算与模型，对比「最近时间」「历史频率」「目标与决策价值」三种记忆选择策略，以及无记忆对照，给出实测质量/成本/失败分析。
1. 现有工程与工作方式（先读代码，再修改）
项目：https://github.com/happiness-cheng/agent-harness。

首先阅读 README.md、docs/AGENT_VISION.md、docs/DESIGN.md、docs/ACCEPTANCE.md，检查 src/min_agent/application.py、runtime.py、sessions.py、context.py、compaction.py、相关 SQLite 存储、权限与定向确认模块、现有 memory/search 代码、相应测试。

已公开设计表明：当前实现会话内 Memory、原始轨迹、压缩及 SQLite 持久化；尚无完整跨会话用户长期画像。必须在现有架构上增量实现，复用 owner/session 隔离、写入授权、版本/事务/预算/trace 机制。不破坏 Plan/Build、工具调用协议、检查点、压缩与现有会话记忆。不要因为任务书提到文件名就假定本地结构一模一样；以实际代码为准。

第一步输出简短基线审计：现有入口、接口、可复用模块、实现文件列表、风险与回归测试命令。然后直接分阶段实现，不要求我对每个小步骤重复授权。若涉及迁移/覆盖私人记录、联网发送私有数据、额外付费调用、对外发布或不可逆操作，停止该特定动作并报告；可安全实现的其他部分继续。
2. 不可改变的产品规则
1. 写入授权：当前 Session 可根据原始用户信息即时调整行为；跨会话正式 Memory 必须用户确认。明确“记住 X”在范围清楚时即视作对 X 的批准；其余由 Candidate → Review → Confirm → Commit。拒绝、忽略不能当批准。合并候选以减少询问频率，但不同独立事实不得用一次含糊的“是”全批。
2. 事实与推断：DIRECT（用户明确陈述）与 INFERRED（Agent 假说）必须区分；两者的长期写入均需符合授权规则。保存 evidence_id、来源角色、时间、适用范围、确认记录。一次广泛明确陈述即可支持对应广泛偏好，不以“至少 N 条”作硬要求；多条记忆来自同一原话不能冒充独立证据。
3. 场景独立：Codex 与 OpenHands 相似偏好不因相似度高而覆盖，分别保留 ID/来源/适用范围；一条 Memory 可属于多个 Topic。可以由若干具体证据形成待确认的一般化候选，未获确认不能成为长期画像。
4. 目标管理：lifecycle（active / paused / completed / cancelled）和 priority、horizon（长期/阶段）、effective_from、observed_at 分开。用户“先重点做 Adrian”只更新阶段优先级，不得自行暂停求职目标。用户“暂停找工作”才触发暂停，暂停≠放弃。用户明确目标优先于基于行为频率的猜测；行为可作为线索不能直接改写正式目标。
5. 检索去重：同一 memory_id 在多个组、BM25、向量或邻居扩展中多次命中，只占最终候选池一个位置；融合时保留全部命中路径和信号；最终 Top-K 必须发生在候选池 ID 级去重之后。不同情景独立 ID 不应只因语义相似被去掉。
6. 检索与推理：先确定性过滤 user/owner 授权、失效状态、时间与确认资格，再做 group-first 多组检索、组内搜索、必要的全局兜底和可限制深度的图扩展。未确认候选不在跨会话自动召回结果中。复杂适用性尽量交给下一次正常 Agent 模型调用，不要每次检索另加一次 LLM；不确定时显式标识 disputed，关键冲突必要时问用户。当前明确请求高于历史偏好。
7. Core / Index / Graph：少量广泛适用、可靠、缺失代价高的 Stable Core；可暂时高优先级的 Active Core；其余为简短 Root Index → Topic Index → Memory Graph 的渐进视图。所有视图须引用同一权威事实来源、可失效/重建，不复制出互相矛盾的第二份事实。Core 晋升/降级只是视图变更，不代表扩大事实范围、也不代表新授权。
8. 隐私与可删除：最小化敏感数据；不把个人原始行为日志、账号、私有原文或凭据发到公开 API、Git 远程、遥测或测试日志。用户删除请求应同时清除已保存事实、索引、缓存及可控派生副本，历史保留策略不得违背用户删除意愿。生产缺失授权时 Fail Closed。
3. Memory 最小 Schema 与接口
围绕实际存储设计以下逻辑实体（SQLite 现有机制优先，不要求引入 Neo4j/Graphiti；可通过关系表实现图）：
- Memory(id, user_id, scope, kind, content, evidence_type, confirmation_status, observed_at, effective_from, valid_until, lifecycle, version, created_at, updated_at)。
- Evidence(id, user_id, session_id, message_ref, role, observed_at, source_hash, excerpt_or_pointer, classification)，能从 Memory 追踪来源；私有数据按最小原则保存。
- Topic(id, user_id, name, summary, keywords, version)，Scenario(id, user_id, label, conditions)，Goal(id, user_id, content, lifecycle, priority, horizon, effective_from, observed_at)。
- MemoryEdge(id, user_id, source_id, target_id, type, evidence_refs, status)；v0.1 至少支持：BELONGS_TO, APPLIES_IN, ABOUT, DERIVED_FROM, RELATED_TO, SUPERSEDES；为 GENERALIZES/EXCEPTION_OF 预留类型，别为了未来需求写过大图引擎。
- Candidate(id, user_id, session_id, content, context, evidence_refs, status, fingerprint, expires_at)；未确认不作为正式长期记忆。
- MemorySelectionTrace 记录候选来源、去重过程、过滤原因、排序细项、最终注入 ID+版本、Token 估算；不默认记录隐私原文。
建议建立或兼容小型接口：propose, list_pending, confirm, reject, correct, delete, search, assemble_context, rank_for_core。工具对模型可见之前先验证 Runtime 安全边界；所有写入在可信存储层按 owner 与 candidate_id 校验，拒绝跨用户引用和重复确认副作用。

可先提供 API/CLI 的可用确认通路；若 Web UI 现成且修改范围可控，可增加“是/否/修改”的最小交互；不要因为实现完整 UI 而阻断核心评测。
4. 检索和图结构实现顺序
- 4.1 单元输入：用户任务文本、当前明确意图、用户 ID、as_of 时间、上下文预算、已注入 memory_id:version 集合。
- 4.2 Group Registry：主题组多对多；组名、描述、关键词、代表实体与可索引概述。组路由取多个候选组，不得按一个标签硬路由。不存在组/低置信时走全局兜底。组候选 Top-G 为可配参数，得记录遗漏。
- 4.3 组内候选：默认先利用现有 SQLite FTS5/BM25（若本地支持；否则清楚标注退化路径）与结构化实体/场景过滤，向量检索作为可插拔能力，不必为 v0.1 强行引入付费 embedding 服务；统一 SearchHit(memory_id, source, score, topic_id, reasons)。
- 4.4 候选合并：多个组/来源按照memory_id全局去重，保留来源集合和得分；分数融合先选简单透明、可重复的方法，避免命中组越多就不合理累加越高。真正语义相近但情景不同的独立记忆保留。执行最终 Top-K 前完成去重和无效过滤。
- 4.5 图扩展：限制跳数和候选预算，只从已获授权的节点按适用场景扩展；图路由失败不可阻断全局检索。
- 4.6 注入：基于已注入 ID+version 防止重复注入，但上下文被压缩/移除后需要重新计算真实可见集合（不能只凭“某次注入过”的历史表）。当前指令优先。争议记忆标识来源、双方理由，不以同等确定事实灌进高优先级提示。
5. Memory Importance 与 Core Placement（本轮重点评测）
明确分离三个量：
- importance(m, t)：已确认用户目标、决策影响/缺失代价、适用广度、时效有效性及证据强弱；不能由出现次数直接替代明确目标。
- relevance(m, q, t)：对当前任务的适用性与相关性。
- placement(m, workload, token_budget)：是否值得常驻；考虑长期目标/阶段目标、未来任务分布、Token 占用、稳定性、冗余与可检索性。
先实现可解释的 v0.1 决定性规则，规则权重与阈值放在配置中；每条记忆输出可审阅的 reason codes。禁用自动任意“LLM 给 90 分”式黑箱。对同一条记忆分别保存 importance 与 placement 理由。以预算严格约束 Stable/Active Core，维持来源指针和过期/撤回机制。真正的反事实价值要通过成对任务评测估算，不能把预估分数冒充已经实测的提升。

离线同预算对比：
- NONE：没有跨会话 Memory（仅允许该题当前消息）。
- RECENCY：仅按历史新近程度选择 Core。
- FREQUENCY：仅按合法的历史使用/提及频率选择 Core。
- GOAL_VALUE：以明确目标和决策缺失代价为主要因素，兼顾范围、时效、成本和冗余；分层索引按需检索。
- 可加消融：GOAL_VALUE_FLAT（无分组/图）、GOAL_VALUE_GRAPH（有分组/图），以隔离架构效果。
保持所有对照条件一致：相同候选事实池、写入批准策略、历史时间切点、推理模型及参数、任务、Core Token 预算、检索工具预算；唯一切换具体策略。不要让某个策略读取额外未来数据。模型 Judge 也须一致，报告其主观性和随机性。
6. 数据适配与评测协议
数据源 A：用户本地准备好的《深入理解 AI Agent》chapter3/user-memory-evaluation 测试集（60 YAML：L1/L2/L3 各 20），以用户本地版本为准，不硬编码所有路径；官方指南参考：https://github.com/bojieli/ai-agent-book/blob/main/chapter3/user-memory-evaluation/TEST_FRAMEWORK_GUIDE.md。

严格按时间顺序回放每个案例中的会话：Session i 的记忆生成后，后续 Session i+1 只能依赖已构建的 Memory，不得偷偷把所有历史原文直接塞给模型；最终用题目规定的问题及评价标准回答、打分。未确认写入的产品约束与离线 Benchmark 的假定历史事实冲突时，需以显式 approved_fixture / oracle_approval 模式模拟用户批准，只用于该数据集，不能把它当成正式产品默认策略；报告有无审批标签/模拟比例，不可把生成器输出直接等价为用户已确认。

数据源 B：用户近两个月的行为记录，只能从明确提供或本地能发现的被授权路径读取。先检查格式/来源与隐私，不自动上传，不擅自将历史记录全部导入正式记忆。按时间切片，前段构建记忆、后段验证，不允许未来记录进入过去查询；如缺少真实任务标签，先构建少量可审阅的人工/规则 gold，不能把“对原文的检索”冒充“帮助任务成功”。缺少数据路径则报告未执行该部分，不阻塞合成数据测试。

指标至少包括：
1. 质量：分层 L1/L2/L3 评分、总体分数/成功率、关键事实完整率、主动关联质量、幻觉/越权/错误适用案例。
2. 检索：Recall@K（有 gold 时）、MRR、Top-K 唯一 ID 数、跨组重复命中次数、情景命中率、漏掉例外规则的比例、全局兜底命中率。
3. Memory 安全：未经确认长期写入数（必须为 0）、跨用户泄漏数（必须为 0）、已删除/过期仍召回数（必须为 0）、目标优先级更新导致非授权暂停数（必须为 0）。
4. 开销：常驻上下文 Token 或明确标识的近似值、召回 Token、LLM 调用次数、延迟、预算耗用；不能把字符数标称为精确 token。
5. 价值：在等预算下，GOAL_VALUE 相对 RECENCY、FREQUENCY 的配对任务质量差，报告逐题差异、置信限制及胜负案例，不强行追求预设胜率。
先跑确定性离线单测与少量 smoke；若有合法配置的模型与确认后的测试预算，跑 60 题全套；若无模型凭据、评测数据、网络能力或费用许可，明确标注未运行/阻塞，不得虚构真实得分，可保留可复现命令与模拟数据结果。
7. 必须包含的回归和性质测试
- 同一 M1 通过三个主题组 + BM25 + vector 命中多次，最终 Top-K 中只占一条；但保留五条检索路径证据。
- Codex M1 与 OpenHands M2 文字相似，情景不同，保留两个 memory_id。
- 查询与任何 Topic 不匹配时，全局兜底仍能检索到相关记忆；跨用户隔离严格生效。
- CURRENT_DIRECTIVE 覆盖历史偏好；一般偏好不能覆盖具体已确认例外。
- 用户未确认时不产生可跨会话正式 Memory；候选拒绝后不再反复提出同一内容；显式记住有严格作用范围。
- 用户一次明确表达广泛偏好可以形成一般性候选/经授权写入，不需机械的两条独立记忆。
- priority_changed 不修改其他 Goal 的 lifecycle；paused 保留 Goal；resumed 可恢复；历史事件记录保留时间与来源；删除隐私数据时遵从删除策略。
- as_of 回放不会使用未来目标与未来偏好。
- 压缩已从当前模型上下文移除 M1 时，如果新任务需要 M1，可重新注入；仍可见的同版本 M1 不重复注入。
- Core 严格遵守可配置预算；晋升/降级不更改事实有效范围；不同策略同数据同预算。
- 运行现有 agent-harness 完整回归测试，记录测试数和失败项，不得用新增单测通过掩盖旧功能回归。
8. 阶段和完成边界
P0（审计）：说明本地真实依赖图、现有存储与授权接口、测试命令、找到的评测文件及版本。不要猜测代码路径。

P1（最小存储与确认）：实体/证据/多对多主题/状态及有来源的事务写入；实现 Candidate → Confirm/Reject；可离线单测。

P2（最小检索）：组优先、组内检索、全局兜底、ID 去重、情景匹配、增量上下文注入；有可解释 trace、最小回归案例。

P3（Importance 比较）：Stable/Active Core 候选、预算、Recency/Frequency/GoalValue 策略、目标状态变更；同一数据上的消融 runner。

P4（评测）：适配现成 60 YAML 与可用的授权行为数据，先 smoke，再有条件跑完整测试；输出汇总和逐题错误归因。若外部 Judge 不可用，只报告确定性子集与阻塞，严禁报告为真实全量效果。

P5（复盘）：提交可审阅的变更、如何本地运行、测试输出、至少 5 个有证据支持的失败案例或所有实际失败、后续优化排序建议。不允许为了改进评测分数反复修改 test gold。
9. 本轮不实现
不额外引入 Neo4j/Graphiti 重型运行时、不做自主无授权画像变更、不开发多 Agent 复杂评审机制、不开放私人数据云同步、不自动调用付费 embedding、不把 LLM 推断写成用户事实、不自动修改生产数据库模式/覆盖用户旧数据、不声称已实现全套自适应学习策略。
10. 应交付文件与最终汇报格式
文件位置以本地架构为准，建议：
- docs/MEMORY_ARCHITECTURE_V01.md（数据模型、状态图、关系和风险）
- docs/MEMORY_EVAL_PROTOCOL.md（数据、时间回放、基线、Judge、指标）
- src/min_agent/...（新增/修改 Memory 模块，最小侵入 Runtime）
- tests/...（单元、集成、性质测试）
- scripts/eval_memory.py（或等价 CLI，需有 --help、可重放）
- reports/memory_v01/summary.md 和机器可读 JSON（脱敏、带版本与输入数据散列）
最终回复只报告真实执行：
1. 改动文件与架构取舍；
2. 实际运行的命令、通过/失败的测试数量；
3. L1/L2/L3 及策略对比表（缺测写 NOT_RUN）；
4. Token / 延迟 / 费用及数据保密情况；
5. 找到的错误归因、未解决风险、后续最优先三项调整。
不要把“代码写好了”“模型自称成功”当完成。必须有运行证据。
资料
- 现有仓库：https://github.com/happiness-cheng/agent-harness
- 当前设计文档：https://github.com/happiness-cheng/agent-harness/blob/main/docs/DESIGN.md
- 作者第三章实验：https://bojieli.github.io/ai-agent-book/chapter3/user-memory/
- 官方 60 题指南：https://github.com/bojieli/ai-agent-book/blob/main/chapter3/user-memory-evaluation/TEST_FRAMEWORK_GUIDE.md
- 作者记忆评测协议参考：https://github.com/bojieli/ai-agent-book/blob/main/chapter3/EXPERIMENT_LEDGER.md