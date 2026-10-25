# Adrian Memory v0.1：实施与证据计划

用户已授权按P0—P5直接实施；不重复询问小步骤。不调用外部模型、不外传私人记录、不迁移生产数据库、不远端发布。主代理按同一清单自审，不使用子代理。

## P0 已核查

- 主工作区HEAD 220f6ec，存在此前提示词、Skill、Context、State Bar及评测的未提交改动，保留。待修改既有文件预像保存在项目外adrian-memory-v01-baseline。
- 源码入口：application.HarnessApplication、runtime.Runtime、sessions.SessionStore、storage.SQLiteStorage。原始记忆memory.MemoryJournal只限当前会话；memory_records保留角色、来源索引、哈希；不等于长期已确认事实。
- 现有SQLite sessions/todos两表，SessionSnapshot.extensions支持版本化JSON；不新增生产SQL表。计划在同owner的内部专用快照保存一个权威Memory文档，业务会话通过可信owner访问，内部快照不进入普通会话列表或Runtime。
- ContextManager与RuntimeContextMessage支持模型调用前动态投影，不写永久System Prompt或历史。复用现有整体窗口门、模型预算及Stop；Memory投影不新增LLM调用。
- 权限由TaskController、WorkState、ExecutionContext及Web cookie/CSRF执行。长期确认走可信应用/API，模型工具仅可读检索，不能自己批准。
- 本地书中60 YAML固定在ai-agent-book-memory-reference，现有M02 v2合成32题保留不改。私有目录已授权本地读取，106份日期会话文件，禁止正式自动导入和原文公开报告。
- 主目录实际基线：pytest -q --tb=short，1124 passed、1既有失败、3 deselected、1既有弃用警告，75.93秒。失败为test_explicit_source_read_and_structured_recall_are_not_silently_head_truncated，期待completed、实际partial。
- FTS5本机可用；使用授权过滤后的临时内存FTS，避免生产迁移。不可用时记录词项退化路径。

## 固定标准与阶段

- [x] P0：源码/任务书/依赖/基线审计。附件按原文保存为根目录指定任务书，不覆盖已存在文件。
- [x] P1：实体、证据、候选审核、原子幂等确认/拒绝/纠正/删除；跨owner fail-closed；SQLite重开。先写测试观察RED。
- [x] P2：多组路由、FTS组内检索、全局兜底、有限图扩展、最终Top-K前按ID去重；可解释trace；真实可见集合重建。
- [x] P3：目标历史/生命周期与优先级分离；importance/relevance/placement分离；Stable/Active预算；NONE/RECENCY/FREQUENCY/GOAL_VALUE同池同预算。
- [x] P4：书中60题时间回放适配及已有gold的确定性来源指标；策略配对离线测试；私有目录仅本地统计/时间切分/待人工标注，不正式写入；真实模型与Judge均NOT_RUN。
- [x] P5：单元/集成/性质/完整回归、隐私/差异自审；架构、评测协议、JSON与报告。保留所有实际失败，不改gold凑分。

## 冻结的验收行为

1. 未确认/拒绝/过期/删除/未来确认事实不可自动跨会话召回；一次明确广泛陈述可提交候选，无机械最少证据数。
2. 三组+BM25+vector同一ID五条路径合并成一项；相似但不同场景ID保留；Top-K在去重之后。
3. 无匹配主题仍可全局搜；图扩展不跨owner、不绕时点及场景。
4. 当前指令优先，已确认场景例外优先于一般偏好；推断/争议明确标注，不能冒充确定事实。
5. 优先级变更不改其他目标生命周期；暂停可恢复，所有状态按as_of回放；删除隐私优先于历史回放。
6. 真正仍可见的同ID/version不重复注入，压缩后已不在可见材料中可重注；Core仅视图，不改授权、范围或事实。
7. 所有策略同数据/审批方式/预算/调用上限，无模型时仅报确定性机制指标，不报任务质量提升。后续发现新反例另增回归。

## 风险与降级

JSON权威存储与临时FTS适合最小版本，不是大规模索引方案；进程内单写入锁延续现有应用边界。默认只保留来源指针与受控短证据；选择trace只存ID和分数。没有授权的真实调用、私有语义提取及人工gold不伪造为已执行。删除覆盖本模块受控事实/候选/证据/索引和投影；原始聊天的留存与不可控外部副本必须明确区分并报告。

最终实跑：51项定向通过；完整回归1175通过、1项P0已有失败、3 live deselected。60题离线、8题事实gold、7检索漏召；真实模型/Judge/私有任务收益NOT_RUN。本地提交，不推送。
