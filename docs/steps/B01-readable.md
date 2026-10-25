# B01 完整交付：按需 State Bar

日期：2026-10-24。结论：本阶段的只读状态投影、按需注入和定向验收已完成，可以收尾。没有新增业务状态库、模型调用、依赖或数据库schema，不实现通用业务验收引擎。

## 现状审查

|现有能力|实际入口|本轮处理|
|---|---|---|
|目标、约束、完成条件、定向确认|WorkState / TaskController|复用；新增同事务目标归属元数据|
|一次运行状态|SessionSnapshot.task|复用；不将completed解释为目标验收成功|
|真实提交和防重|operations / action_receipts / TaskController.guard|复用，未改执行规则|
|调用事件与逐轮归档|trace / SessionEvents / memory_records|复用，不按事件数增加任务步骤|
|父任务及片段预算|BudgetSegment / SessionBudgets|直接读取最紧剩余额度，继续硬校验|
|检查点和重启|Checkpoint / SessionStore / HarnessApplication|复用；恢复后从当前对象构建视图|
|原上下文注入|Runtime._run/_close_checkpoint前置完整application_state；ContextManager重建时附完整SessionStateTable|改为一条动态尾部，应用不再隐式附全表|

## 最小改造与文件

- [state_bar.py](../../src/min_agent/state_bar.py)：StatusBuilder只读投影；RuntimeContextMessage作内存标记，Provider序列化字段不变。
- [runtime.py](../../src/min_agent/runtime.py)：统一普通调用和checkpoint收尾的输入构建，系统提示+历史+动态尾部。基础application_state仍只有mode/current_date；需要跟踪时同条消息增加agent_status。
- [sessions.py](../../src/min_agent/sessions.py)：可信入口提交新提案时，以proposal_id/history_start记录所属逻辑任务，和WorkState同一快照提交。
- [tools/registry.py](../../src/min_agent/tools/registry.py)：从绑定会话读取状态栏；无新模型工具或写接口。
- [context.py](../../src/min_agent/context.py)：临时尾部不进入covered/hash/摘要；参与完整窗口检查；整理完成后再次刷新版本和预算，再检查窗口。Stop先于刷新处理。
- [application.py](../../src/min_agent/application.py)：移除隐式全量状态表注入；read_session_state仍可显式读取详情。

生产涉及6文件，未改Agent工具执行分发、权限判定、防重和完成判断。新增1个探针、1个测试文件，并调整3个既有测试对消息位置/状态结构的断言，原来的权限、历史及身份检查保留。

[完整代码diff](B01-code.patch)为零上下文审阅版，包含上述实现及测试；[实现计划](B01-state-bar-plan.md)保留设计依据。[实际输入完整对照](B01-context-comparison.md)提供相同系统提示完整内容、同一个问题、前后完整状态JSON与Git式差异。

## 运行规则

1. 简单聊天不创建计划，也没有agent_status；保留原有基础日期和模式元数据。
2. 当前目标需要跟踪、存在本任务失败/未知操作、恢复检查点或本任务已有压缩时构建短视图。不按固定轮次开启。
3. 已确认动作仅在匹配真实committed凭据时进入completed；普通读取和模型自述不算完成步骤。pending来自同一目标的未提交动作。candidate明确标记未确认。
4. completed只证明动作提交，目标整体verification始终为unknown，不能被Final Answer或checkpoint模型纪要改写。
5. mode/剩余次数/阻塞/未知操作/版本/检查点取可信Runtime；next_action只是限定的下一步建议。
6. 结果未知时显示核对建议，原防重和授权重检仍负责拦截执行。相同持久操作ID恢复的未知update实测handler只执行一次，不对任意新ID重试作全局保证。
7. 每次模型调用仅一条动态投影，不追加到会话原历史。普通新聊天不会重新投影旧目标；旧聊天原文仍按正常历史策略保存。
8. 状态读取、序列化失败或视图超过16000 UTF-8字节时暂停请求，不静默删约束。这个上限是视图的保守体积保护，并非Token。原预算先计一次调用尝试，失败不退还；raw模型未被调用。

RuntimeContextMessage没有额外Provider字段，回调是内存PrivateAttr；两种现有Responses协议build_input测试均通过。移除以用户文本中application_state字样识别可信前缀的旧逻辑，Plan模式下待执行写动作显示planning并建议检查计划或请求Build，不建议直接执行；该项补充回归先RED再GREEN。普通用户JSON不取得临时状态消息身份，也不改变权限等级。

## 固定验收与完整证据

[冻结14项套件](../acceptance/b01-state-bar-suite-v1.json)先于生产代码。B01/B02为开发场景，其余为公开固定验收，不是盲测。

|场景|修改前|最终版|
|---|---|---|
|简单问答不建计划、动态元数据在末尾|失败|通过|
|多步骤动作有目标、已提交和待提交进度|失败|通过|
|3次读取不增加完成步骤|失败|通过|
|工具失败可见|失败|通过|
|未知写入不标成功、相同操作恢复不重复handler|失败|通过|
|模式变化下次可见|失败|通过|
|状态生成后权限变化仍被执行前校验阻止|通过|通过|
|真实结构化压缩后保留新状态及版本|失败|通过|
|新聊天清退旧任务投影|失败|通过|
|模型自称完成不产生验证事实|失败|通过|
|投影失败不调用模型/不写业务|失败|通过|
|checkpoint恢复不提升模型纪要可信度|失败|通过|
|修改投影副本不改业务状态|失败|通过|
|用户伪造状态文本仍受原授权约束|通过|通过|

- [可信现状基线，2/14](../acceptance/b01-state-bar-before-v2-20261024.json)
- [主目录最终结果，14/14](../acceptance/b01-state-bar-final-v2-20261024.json)
- [独立分支结果，14/14](../acceptance/b01-state-bar-public-v2-20261024.json)
- [逐场景消息字节与调用次数](../acceptance/b01-state-bar-comparison-v2-20261024.json)
- [公共Runtime入口探针](../../scripts/audit_state_bar.py)、[23项新测试](../../tests/integration/test_state_bar.py)

报告保存每次实际模型可见messages、状态栏、期望/观测、请求次数和错误状态。messages字节计量不包含工具定义或传输封装，token_usage为null。工具定义由固定注册表提供，脚本模型不进行真实选择。

定向115 passed in 3.35s。新增边界测试涵盖剩余预算命名与刷新、可修正失败phase、超大视图暂停、刷新后增长重新验窗、实际应用清退、SQLite重启恢复、两Provider协议，以及用户伪造application_state不改变历史hash范围。

## 实际验证命令与结果

主目录以`.venv/Scripts/python.exe -X utf8`运行：

```powershell
python -m pytest tests/integration/test_state_bar.py -q --tb=short
python -m pytest tests/integration/test_state_bar.py tests/integration/test_authorized_todo.py tests/unit/test_checkpoint_resume.py tests/scenarios/test_intent_contract.py tests/integration/test_integrated_boundaries.py -q --tb=short
python -m pytest -q --tb=short
python scripts/audit_state_bar.py --suite docs/acceptance/b01-state-bar-suite-v1.json --report docs/acceptance/b01-state-bar-final-v2-20261024.json
python -m py_compile src/min_agent/state_bar.py src/min_agent/runtime.py src/min_agent/sessions.py src/min_agent/tools/registry.py src/min_agent/context.py src/min_agent/application.py scripts/audit_state_bar.py tests/integration/test_state_bar.py
python -m pip check
```

主目录新23项通过；全量**1101 passed, 1 failed, 3 deselected, 1 warning in 72.85s**。失败为此前已记录的`test_explicit_source_read_and_structured_recall_are_not_silently_head_truncated`（期望completed，实际partial），保留原状，不能声称主目录全量通过。

独立分支基于已提交主分支220f6ec，仅含B01改动，未夹带主目录其他提示词和Skill未提交改动。显式设置`PYTHONPATH`为自身src，打印实际导入路径后运行全量：**1001 passed, 3 deselected, 1 warning in 39.60s**。它与主目录测试/提示词集合不同，结果分别报告，字节对比只用主目录前后报告。

共享环境pip check输出`No broken requirements found`。离线wheel构建：

```powershell
python -m pip --disable-pip-version-check wheel . --no-deps --no-build-isolation --no-index --wheel-dir C:/Users/陈独秀/.codex/tmp/state-bar-wheel-final-20261024
python -m pip --disable-pip-version-check install --no-deps --no-index --target C:/Users/陈独秀/.codex/tmp/state-bar-installed-final-20261024 C:/Users/陈独秀/.codex/tmp/state-bar-wheel-final-20261024/agent_harness-0.1.0-py3-none-any.whl
```

wheel SHA-256：`787776f0da3bb6279de4e45e8340ad5a7cabd891db7a0651042fa3836ffe20c4`。从独立安装目标实际导入state_bar.py，绑定会话运行两次：第一轮目标verification为unknown；下一轮普通聊天无agent_status。两次本地脚本响应、外部请求0。没有发布包或改现有venv安装。

## 失败记录与审查

- 探针初版错误地在内存SessionStore创建持久预算；改用合成临时SQLite后重跑。未知写入恢复初版使用非持久run_id，随后改为真实持久任务预算；`before-v2`才用于前后对比，旧before报告仅为探针开发记录。
- 新14项先12失败/2通过，实现后14通过；预算字段和错误phase补充测试先2失败/16通过，再18通过。两项应用测试初版误用不存在的context manager协议，改用closing，不改生产类。
- 首次全量5失败：3项旧位置断言更新，Stop兼容缺陷修复，1项既有记忆失败保留。用户application_state假JSON触发旧前缀猜测导致hash不匹配，先单项RED再移除该猜测，最终回归通过。
- 中间after/after-v2报告只保留迭代记录；最终字段以final-v2/public-v2为准，固定套件判据未改。暂存差异检查发现patch材料的空白上下文行被识别为尾随空格，材料改为零上下文diff后复验；生产代码未因此修改。

主代理自审，无子代理。权限/防重/预算硬检查没有放宽；投影不写回；状态生成失败不派发；用户文本和模型自述不成为验证；新消息类型没有提升role；动态尾部不进入摘要；原消息及用户未提交改动保留。回滚可撤销本分支B01补丁，已有存储内多出的goal_tracking元数据不参与权限，不需要schema迁移。

|阶段|结果|证据|
|---|---|---|
|Build|PASS|py_compile、离线wheel构建和独立目标安装冒烟|
|Type|SKIP|未配置类型检查器|
|Lint|SKIP|未配置lint|
|Test|PASS（B01独立范围）|14/14、新23、独立全量1001；主目录仍有1项既有失败|
|Security|PASS（定向）|敏感特征/凭据文件/危险执行检查；不替代完整安全审计|
|Diff|PASS|限定B01文件、git diff --check、材料链接与suite哈希核对|

## 已知限制与收尾

1. 当前TaskState是运行状态；通用语义步骤、业务最终验收、目标何时应跨新逻辑任务延续没有新增状态机。completed仅为已确认动作提交，其他进度来源不足保持unknown，next_action为建议。pending仅列动作序号和类型，完整参数仍可显式read_session_state读取。
2. 未确认或旧存储中缺少goal_tracking的目标不猜测当前归属；用户可重新提案确认。普通聊天清退投影不会撤销旧授权，权限生命周期仍由原TaskController管理。
3. 复杂任务本批消息体略有增长，普通问答减少102字节。真实Token、缓存命中率、延迟、错误停用/无效工具调用下降和长任务效果尚无模型证据。
4. 没有给所有子循环增加状态栏；本版覆盖绑定会话的主Runtime及checkpoint收尾。压缩辅助调用保持原职责。
5. 只读投影是快照，可能在模型推理期间变化；执行前原授权及操作校验仍是最终边界。投影失败以现有context_limit/收尾失败路径暂停，没有另建错误恢复系统。

本阶段不继续扩展通用任务状态机、差量同步或独立验证器；达到当前State Bar标准后收尾。
