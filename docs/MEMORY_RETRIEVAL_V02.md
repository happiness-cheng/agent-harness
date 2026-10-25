# Adrian Memory v0.2：实现与架构差异

本轮完成一次可解释的检索组件实验。卡片对部分指标有益，但同预算可见覆盖及独立测试总事实数没有提高，**不启用为生产 Runtime 的默认表示**。没有修改重要性权重、数据库 schema、长期记忆确认流程或 Agent Loop。

## 1. 基线与代码边界

- 起点 `b90f104c52cb321d7585accd1e8aa1a6758fe87a`，工作分支 `feat/adrian-memory-v02-local`。
- 专用工作区：`C:/Users/陈独秀/.codex/tmp/adrian-memory-v02`。原 `Documents/agent-harness` 的已有修改与 v0.1 结果不覆盖。
- 书中 60 题和原 Gold 的哈希逐一核对，见 `reports/memory_v02/baseline.json`。原八题为开发诊断，新增六题独立保留测试。
- v0.1 实验源码与 b90f104 的三处既有变化已通过归档/hash/AST 复核：`integration.py` 的工具短返回/动态索引依赖，`selection.py:assemble_context`，`store.py:LongMemory.delete`。构建事实池、records、重要性和原检索未改变，见 `version-audit.json`。
- 本轮 A 组 14 题 Top-8 的 ID 与 v0.1 原始结果逐项一致。实验期间代码哈希固定，最终源码归档/交付收据另外列出可复核版本；未发布远端。

## 2. 原来与现在

```text
原有权威 Memory / Episode / Evidence / Topic / Scenario
   └─ owner、确认、有效时间、scope、场景和撤回过滤
       └─ Topic -> FTS -> graph -> 按 ID 合并 -> cap -> Top-K

v0.2 离线实验新增
   ├─ inspect=True：完整融合前/后池、路径和全局 FTS 探针（只诊断）
   ├─ approved_fixture -> MemoryCard 只读 sidecar
   │    └─ CardView 每次回到原权威层验证，再投影 content/evidence_refs
   └─ A raw / B cards+sameFTS / C cards+entity-alias
        └─ 先检索，后 Gold 分析，再单独测 2048 字节上下文装入
```

现有 Runtime 仍调用原 `search`。`inspect` 默认为 false，不增加线上诊断检索；卡片没有注册为线上写工具，没有自动导入用户行为记录。P2/P3 是离线最小表示实验，不是完整语义记忆系统。

## 3. 完整候选池诊断

`src/min_agent/long_memory/retrieval.py` 只新增可选 `inspect`，保留原默认分数和截断规则。诊断提供：

- 已授权合法 ID、路由 Topic 的分数/入选情况。
- 带来源路径的 raw hits、初始 cap 后候选、cap 前全部融合候选、cap 后候选和最终 Top-K。
- 对所有合法记录另做一次 FTS 探针，区分词汇不匹配与 Topic 路径未触达；探针结果不送入排序。
- 逐事实后验分析再加源 Episode、消息位置、原句范围/哈希/时间、命中路径/排名、截断/预算去向。

Gold 没有进入 `search`、`build_cards`、`entity_hits` 或初始历史构建。合成测试用两个不同 Gold 比较并确认三个组的候选和最终 ID 完全相同。

## 4. MemoryCard 的范围

`src/min_agent/long_memory/cards.py` 提供独立可测试的数据结构：

| 字段 | 本轮含义 |
|---|---|
| memory_id | 沿用来源 Episode ID，作为一对一投影；不同 Episode 不合并 |
| content | 原句 + 同一源会话前一句最多 600 字符 + 明确角色、业务来源 |
| entity | 原文可见的专名/标识符，别名仅识别显式 `Name (ALIAS)` |
| scenario | 原会话 conversation_id；业务权限仍由来源 scenario_ids 控制 |
| valid_time | 来源 effective_from / valid_until；投影不能延长 |
| evidence_refs | 当前原句及邻接片段证据 ID，完整原 Episode 和原文保留 |
| evidence_type | Statement 本身的 DIRECT / INFERRED；邻接片段角色显式保留 |
| confirmation_status | pending / confirmed_fixture，明确不是生产用户确认 |
| topic_ids | 继承源 Topic |
| source_* / user_id | 来源版本/哈希、owner，用于拒绝陈旧或越权 sidecar |

这不是 LLM 提炼器。原始 assistant 陈述保持 INFERRED/角色标识，Fixture 批准仅代表实验允许处理这些材料，不代表其医学/金融/保险内容现实正确。没有把模型推断写成用户确认事实。

核心代码可概括为：

```python
for source in memory.records(context, as_of=as_of, scenario_ids=scenario_ids):
    card = cards.get(source.id)
    if card is None or card.confirmation_status != 'confirmed_fixture':
        continue
    if card.source_sha256 != fingerprint(source):
        continue
    # 实际实现还校验 owner、版本、有效期、每条上下文证据的存在和时间。
    result.append(source.model_copy(update={
        'content': card.content, 'evidence_refs': card.evidence_refs,
    }))
```

可以把它理解成给旧档案加一张可撤回的索引卡：原档案仍是依据，索引卡不能自行改变档案的主人、有效期或可信等级。

## 5. 工程改动范围

| 文件 | 改动 |
|---|---|
| long_memory/retrieval.py | 默认不启用的离线完整池诊断 |
| long_memory/cards.py | MemoryCard、受控构建、权威过滤后的 CardView、实体/显式别名路径 |
| long_memory/retrieval_experiment.py | A/B/C、同预算打包、后验归因、指标 |
| scripts/eval_memory_retrieval_v02.py | 固定数据、Gold 锚点/hash、同基线 Top-K 和代码变化检查；保存完整结果 |
| scripts/inspect_memory_retrieval_v02.py | 无模型的结果检查 CLI |
| scripts/report_memory_retrieval_v02.py | 从固定结果生成报告/微平均指标；不重跑检索 |
| tests/fixtures/memory_v02_holdout.json | 先于检索固定的六题 23 事实及原文证据 |
| 四份新增单元测试 | 卡片/诊断/实验边界及旧原文回查修复 |

## 6. 旧长原文读取问题：单独修复

这项问题在 b90f104 已复现，不计入 A/B/C 收益。

- 原问题：`test_explicit_source_read_and_structured_recall_are_not_silently_head_truncated` 为 partial/context_limit。
- 定向探针观测：显式完整原文加 `recall_memory` 的重复 summary/events 和旧控制状态，使窗口含输出预留约 **35783 字节**，超过 **32768**。
- 修复：`MemoryJournal.recall()` 作为模型专用短投影，返回 archive id / kind / events；事件文字预览上限 96 字符并明确 `truncated=true`，保留完整 source、工具结果语义和 verified 标识。完整归档、select/read API 与原文分页不改。
- `application.py` 的 recall 工具使用该投影，避免同一摘要与事件重复返回；当前控制状态继续由现有 `read_session_state` 提供。
- 同一原回归通过，显式 read_memory_source 返回的完整正文仍与原文逐字一致；窗口预算没有扩大。新单元测试确认回查不改原始归档。
- 限制：固定小窗口仍可能因更多工具/更长材料正确返回 context_limit；这不是任意长原文都能一次装入的保证，仍需分页。本轮不重构压缩策略。

独立修复 patch：`reports/memory_v02/legacy-source-read.patch`，不与检索模块变更混淆。

## 7. 验证结果

- 新增/相关定向验证：最初 61 passed；随后补上卡片 pending/owner/expiry/scene/上下文撤回边界测试，全部包含在最终全量测试中。
- 最终全量：**1187 passed，3 deselected，1 warning**；三个 live 测试未运行，没有外部模型授权。原有 Runtime 测试未删改。
- 现有 owner、确认、有效时间、ID 融合、不同场景、Goal priority/lifecycle、删除/派生撤回回归均保留并通过。卡片另外覆盖撤回其邻接来源后的失效。
- 离线 wheel 构建通过、compileall 通过、pip check 无损坏依赖、diff whitespace 检查通过；详细收据和日志见 `reports/memory_v02`。
- 无独立类型检查器/linter 配置及工具时标 NOT_RUN，不能拿 compileall 充当静态类型证明。Starlette/httpx 兼容警告原样保留。

完整质量结论见 `reports/memory_v02/evaluation_v02.md`；逐案归因见 `retrieval_failure_analysis.md`。本轮在这次固定实验后收尾，下一轮三个建议只记录，不继续实现。
