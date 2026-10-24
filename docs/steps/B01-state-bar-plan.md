# B01 State Bar 最小实现与验收计划

日期：2026-10-24。用户已给定完整需求并授权实现；按需状态投影，不新建任务数据库、不改权限、防重、预算或业务验收状态机。主代理设计/代码自审，无子代理。缺失的全局code-quality/review-checklist文件按项目AGENTS及同等清单执行。

## 现状与缺口

Runtime._run和_close_checkpoint每次把完整WorkState置于system之后、历史之前；普通问答也注入提案。ContextManager仅在重建可见投影时附SessionStateTable全量视图，包含全部todos/reminders/committed_actions。两条入口重复且触发不一致。

WorkState/TaskController已有用户目标、约束、验收条件、定向确认；SessionSnapshot.task是一次运行状态，不能冒充业务目标完成。operations/action_receipts、SessionEvents、trace、BudgetSegment及Checkpoint已实现，全部复用。工具事件不等于任务步骤。普通聊天不创建计划。

## 最小方案与取舍

1. 新增state_bar.py，只读构建短视图。当前已确认动作+实际提交凭据形成completed/pending，普通读取不增加进度。整体目标验证缺少独立验证器时保持unknown。next_action是受限建议，不授予权限。
2. set_work_state提交新提案时，同一快照记录proposal_id/history_start归属元数据，区分结束后的新普通聊天。它不建立新事实库、不改变授权；旧提案无归属时不猜属于当前任务。
3. Runtime统一普通调用与checkpoint收尾的上下文构建，保留基础application_state(mode/current_date)，把可选agent_status放入同一条末尾user消息。系统提示不改。普通聊天只有已有基础元数据，无额外agent_status。
4. RuntimeContextMessage仅为内存类型标记，序列化仍为标准Message字段。ContextManager把该可信类型的临时尾部与历史分开，不参与covered/hash/普通摘要；完整请求仍计入它的预算。普通用户文本伪造JSON不能取得该类型。
5. application不再自动注入完整SessionStateTable；该表和read_session_state继续供显式读取。状态生成失败停止本次模型请求，不影响原有硬边界。

替代方案：每次注入全量状态实现简单但有冗余和过期风险；让模型维护摘要需要额外推断且无可靠事实来源；均不采用。通用语义计划没有现成存储，本阶段仅展示可验证的动作进度，不重建计划引擎。长视图超限明确暂停，不静默裁剪约束。未来扩展另行验收。

## 冻结验收

详见b01-state-bar-suite-v1.json。新测试先跑原实现并保存失败。比较相同脚本模型/输入的原始请求、状态栏、调用次数及UTF-8请求字节数；无tokenizer/真实usage，不冒充Token计量。不宣称脚本模型证明真实工具选择改善。

场景覆盖简单问答、已确认多步骤动作、三次读取不增加进度、可修正工具失败、未知写入及相同操作恢复防重、权限刷新、执行前授权重检、真实压缩后刷新、已结束任务清退、模型虚称完成、状态生成失败、检查点恢复、状态只读、伪造用户状态文本。

## 实施步骤

- [x] 固定套件/公共入口探针与现状基线；不改生产代码。
- [x] 首片：只读投影+可信任务归属+工具注册表访问入口，增加对应测试。
- [x] 次片：Runtime统一构建+ContextManager临时尾部+应用去重复接线。
- [x] 同套件复验；相关授权/预算/压缩/检查点回归；主工作区全量结果单独报告。
- [x] 完整代码diff、实际新旧输入及小结、自审、限定范围Git交付。达到本阶段标准即收尾。
