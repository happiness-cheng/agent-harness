# 2026-10-21 实现审查修复记录

> 本文保留修复交付时的历史状态。“未执行 Git／仅本地收录”不代表之后的发布状态；本轮独立复核与用户新增公开授权见 [提交说明](SUBMISSION.md)。

本轮已修复 F01、F02 两个阻断，并补齐 F04、F05。默认离线回归 **901 passed、3 deselected、1 warning，29.36 秒，退出 0**；原审查两项探针在源码及项目外独立安装包均通过。F03、F06 按审查允许的“补齐或准确披露”选择保留差距，不能宣称扩大后的设计或全部实机验收通过。

原审查、原交接和历史失败报告保持原样。本轮没有新增真实模型、公开搜索或桌面通知验收请求，没有执行 Git、SQL schema 迁移或凭据修改。两份聊天已获定向批准，仅本地收录。

## 发现与处理

| 项目 | 当前状态 | 实际行为与验证 |
| --- | --- | --- |
| F01：session 预算锁死 | 已修复 | completed 后的新普通输入获得新逻辑任务账本，保留原会话历史及旧预算；暂停/等待/检查点恢复沿用原账本。覆盖12个普通输入、耗尽任务恢复、排队及重启。 |
| F02：一次确认重复写入 | 已修复 | proposal ID + 动作下标确定实例，和模型 call ID 分离；真实业务提交与消费回执同一 SQLite 事务。覆盖不同 ID、重启、并发、提交失败回滚、响应丢失、两次合法同内容动作及提醒。 |
| F03：动态早期前缀 | 未实现，已披露 | application_state 仍放在 system 后、旧历史前；目标变更会影响其后缓存。未实施追加投影，也未测缓存命中或费用。 |
| F04：逐轮归档 | 已补齐 | 每个最终完成的交互与终态一起保存原文区间/哈希、工具状态、账本 ID 和未核实助手文字，不额外调用模型。等待到恢复完成保留原起点，未配对不归档；后续压缩另存记录。 |
| F05：研究 trace | 已补齐 | 父子共享 recorder，子 run 关联父 research 工具 span；每会话保留最近20次 run，每次最多2000事件，截断有标记，重启可查。下一轮不会覆盖唯一历史。 |
| F06：累计资源预算 | 未实现，已披露 | 硬门仅模型/工具调用次数；窗口估算、单次输出限制、HTTP timeout 不等于累计 token、task deadline 或费用门。没有精确计费承诺。 |

代码入口：[application.py](../src/min_agent/application.py)、[task_state.py](../src/min_agent/task_state.py)、[operations.py](../src/min_agent/operations.py)、[sessions.py](../src/min_agent/sessions.py)、[memory.py](../src/min_agent/memory.py)、[trace.py](../src/min_agent/trace.py)、[research.py](../src/min_agent/research.py)、[runtime.py](../src/min_agent/runtime.py)。

研究整合回归另发现：原持久化应用将已绑定账本交给不接受归属的 Runtime.run，导致研究在模型调用前被拒绝。现隔离子循环显式校验存储、session、活动父任务与预算绑定，不新建父 session 任务、不写入子历史、不绕过账本门；错误归属会在调用及子预算创建前拒绝。此链路本轮使用可控模型和合成 search/read 验证，未冒充新实网闭环。

确认版本升级采用保守兼容：旧 v1 目标和历史可以读取，但旧确认缺少消费证明，写入必须提出并确认新提案。重确认旧 ID 不补充次数。成功无变化也消费一次；提交前失败不消费；响应丢失后按同请求回执取结果，无法确定则拒绝盲重放。合法重复内容通过明确的多个动作实例表达，不按标题全局去重。不保证任意外部副作用恰好一次。

BudgetRecord ID 表示逻辑任务，TaskState.task_id 表示执行，segment_id 表示预算片段。默认逻辑任务40模型/60工具，片段6模型/12工具；研究和压缩共用原账本。HTTP 客户端进程100次请求硬门独立存在；新逻辑任务不绕过该门。耗尽旧任务当前没有追加总额度入口。

归档使用确定性结构记录，不将助手文字自动判为已验证事实，也没有声称实现全量语义事实提取。Trace 接口 `GET /api/sessions/{session_id}/traces/{run_id}` 与现有 API 共用身份/归属门；快照提供索引，不返回全部历史事件。只保留固定诊断元数据和哈希引用，不记录完整工具参数或隐藏推理。

## 回归与自审

新增19个集成回归，位于 [逻辑任务预算](../tests/integration/test_logical_task_budgets.py)、[动作实例](../tests/integration/test_confirmed_action_instances.py)、[逐轮归档](../tests/integration/test_turn_archives.py)、[研究 trace](../tests/integration/test_research_traces.py)。先实际复现失败，再实施最小修改。

旧测试中的“单动作可重复执行”“重确认可补次数”与修正后的合同冲突，已改为显式多个实例/新提案；旧轮数测试改用重复只读工具，继续验证轮数硬门，未削弱授权检查。中间失败与修复过程保留在本机恢复日志，公开摘要见 [问题记录](PROBLEM_SOLVING.md)。

主代理完成需求、正常流程、越权、并发、事务故障、重启和隐私边界自审；没有启用子代理或伪造独立 reviewer。缺失的全局代码/审查规则沿用本会话已记录的等价清单降级。现有 SQL 两表及版本1未改变，两份既有数据库与本轮基线 SHA256 相同。

| 阶段 | 实际命令 / 方法 | 结果 |
| --- | --- | --- |
| Test | `.venv/Scripts/python.exe -X utf8 -m pytest -q --tb=short` | 901 passed、3 deselected、1 warning in29.36s，退出0；包含本机 Edge 两项 |
| Dependencies | `.venv/Scripts/python.exe -X utf8 -m pip check` | No broken requirements found，退出0 |
| Syntax | `.venv/Scripts/python.exe -X utf8 -m compileall -q src tests scripts` | 退出0 |
| Build | `.venv/Scripts/python.exe -X utf8 -I -m pip --isolated wheel --disable-pip-version-check --no-build-isolation --no-deps --no-index --wheel-dir dist/review-fixes-20261021 .` | 退出0，新 wheel 101657字节，51份包资源/43个Python模块与源码逐字节一致 |
| Independent install | `.venv/Scripts/python.exe -X utf8 scripts/verify_release.py --wheel dist/review-fixes-20261021/agent_harness-0.1.0-py3-none-any.whl --wheelhouse dist/final-20261020/dependencies --report docs/acceptance/review-fixes-20261021/clean-install.json` | 项目外新 venv，离线安装及 `-I` 计算/正文提取/SQLite工具循环/CLI HTTP/静态资源通过，无源码路径导入 |
| Original probes | 将原脚本复制到临时目录，分别使用源码环境及干净安装环境 `python -X utf8 -I` 运行 | 两契约均 true，退出0；三问题分别 completed/独立预算，归档1→2→3；一次确认实际新增1条 |
| Security | 限定 src 文本扫描常见密钥、eval/exec、单行 subprocess shell=True、verify=False；检查包内容 | 无匹配，无凭据/数据库/日志/测试入包；不是完整安全保证 |
| Diff | 与保存的 SHA256 基线比较 | 9个生产文件、2个旧测试修改；新增4个测试文件，未移除；pyproject/两库不变。不是 git diff |
| Type / Lint | 未配置 | SKIP，未擅自安装检查器 |
| Live / Windows | 本轮不执行 | 原32/32额度耗尽；完整新研究闭环复验、Windows显示/点击仍缺实机证据 |

Starlette/HTTPX TestClient deprecation 警告保留，未借机更换依赖。当前目录未初始化 Git，不能声称 `git diff` 或 GitHub 发布通过。

源码及两项原探针证据：[before.json](acceptance/review-fixes-20261021/before.json)、[after-final.json](acceptance/review-fixes-20261021/after-final.json)、[installed-probe.json](acceptance/review-fixes-20261021/installed-probe.json)、[clean-install.json](acceptance/review-fixes-20261021/clean-install.json)、[verification.json](acceptance/review-fixes-20261021/verification.json)。早期 after-p1.json 是补逐轮归档之前的结果，作为历史保留，不覆盖成最终数据。

## 文档、聊天与本地交付

[五题修订稿](ARCHITECTURE_REVISIONS.md) 已补第五题公开 API 的直接比较、优缺点及双向 Adapter 边界；其余题围绕审查意见收敛。实际项目能力与目标设计分开，原答案不改写，用户仍应核对并能自行解释。

[聊天说明](ai-conversations/README.md) 与 [检查边界](CHAT_RECORDS_REVIEW.md) 记录用户2026-10-21对两份脱敏预览的明确批准。75条和116条消息的两个文件逐字节复制，来源哈希保留；仅本地收录，不提交或发布。完整模式检查和抽样不是逐字隐私审阅，招聘题/讨论/分享链接仍保留，private解析材料及原始JSONL未复制。

本轮安装包：`agent_harness-0.1.0-py3-none-any.whl`（本地构建产物，路径 `../dist/review-fixes-20261021/agent_harness-0.1.0-py3-none-any.whl`）。SHA256：`260f5fc2f45f6f09cae918e019cf1c268dc76fa3ce651cb5eef50cc7a0b0df5a`。版本仍0.1.0，以单独目录和哈希区分历史包；没有覆盖旧包或发布到包索引。

[工作台](http://127.0.0.1:8765) 已在确认无运行任务后重新加载当前源码，保留既有工作会话及 DeepSeek 模式；HTTP/资源/新 trace 索引健康检查通过，启动及检查没有发模型请求。既有工作库和原正式库不用于探针或验收写入。

剩余边界仍成立：F03/F06未完成；Windows系统关闭通知，用户未看到弹窗，显示及点击未验收；最终搜索修正后的全新连续实网研究未重新通过；OpenAI双模型比较暂缓；GitHub发布需用户明确要求。历史真实结果见 [原验收记录](ACCEPTANCE.md)，本轮离线通过不消除这些缺口。

最后文档审计：39份活动Markdown、223个本地链接，问题0；历史聊天按批准字节保留、单独核对哈希，不借链接检查改写聊天。结果保存在 acceptance/review-fixes-20261021/document-audit.json。
