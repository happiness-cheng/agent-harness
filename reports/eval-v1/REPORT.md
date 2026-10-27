# Adrian 统一验证与夜间模式 V1：实际交付报告

交付日期：2026-10-27。结论：统一评估、独立验证、受控修复闭环及夜间最小队列已实现；可以运行离线演示、现有项目回归，以及显式授权后的有限真实任务。**尚不能当作通用无人值守编程平台，也没有真实连续运行10小时。**

本轮没有推送、提交、合并候选、生产数据库迁移或私人历史导入。现有网页服务未自动重启或部署；新输出策略在正常重启后生效。未设置开机自启或后台定时任务。真实付费批次已停止，未安排后续自动消耗。

## 1. 代码审计与实现范围

复用原HarnessApplication、Runtime、Tool Registry、Session/SQLite、Plan/Build确认、持久预算、Checkpoint、ResearchService、Long Memory、Artifact/Trace和数据库文件租约。原Runtime和权限/Memory实现未被另起一套替代。

新增 `src/min_agent/evaluation/`：

| 文件 | 职责 |
| --- | --- |
| contracts / suites | 冻结任务、五类20题、输入权限和验收契约 |
| worker / runner | 原Harness的隔离执行、父进程只读业务数据库复核、版本/成本/超时收据 |
| verifier / review | Outcome、Process、Evidence、Semantic分层三态验证；人工注释不能覆盖硬安全失败 |
| improvement / codex_adapter | 隔离worktree、受限JSON/AST修复、非交互Codex、相同任务复验、不自动合并 |
| journal / finance | JSON原子持久化、文件租约、每次生成前保守费用预留与usage结算 |
| night / night_executor / night_cli | 队列、路由、依赖、审批挂起、有限重试、崩溃恢复和早晨报告 |
| __main__ | run / export / compare / review / improve入口 |

既有源码仅六处必要接线：`application.py`允许配置原工具账本预算；`llm/client.py`读取安全白名单usage中的缓存Token；`llm/deepseek.py`、`model_context.py`、`context.py`、`web/__main__.py`支持真实入口自适应输出上限及上下文门禁。真实模型容量继续取官方metadata，不猜模型窗口；保守字节估计不是精确tokenizer。适配器兼容性默认4096仍保留，网页和新live评估显式采用None自适应。没有关闭上下文、费用、工具或轮次门禁。

新增六个测试文件、四个脚本入口/绘图脚本、两份使用文档、任务JSON及Schema、SVG/PNG架构图。README和已有ADRIAN文档只增加入口说明。准确增量及保护文件校验见本目录的 `review-checks.json`、`changes.json`、`implementation.patch`。

## 2. 实际测试结果

| 验证 | 真实结果 | 证据 |
| --- | --- | --- |
| 改动前定向基线 | 87 passed，1 warning | baseline.txt |
| TDD初始失败 | 13 failed；夜间模块初始6 failed | red.txt / night-red.txt |
| 额度错误停止路线的反例 | 先复现错误仍执行多个同路线任务，再修复通过 | provider-block-red.txt / units-complete.txt |
| 新增测试最终单元检查 | 20 passed | units-complete.txt |
| 最终全量pytest | **1242 passed，1 failed，3 deselected，1 warning，147.26秒** | full-regression.txt |
| 最终离线冻结20任务 | 15 PASS / 2 FAIL / 3 UNKNOWN；全部符合预先预期 | offline-acceptance/summary.json |
| 真实DeepSeek 3任务 | 待办PASS、跨会话Memory PASS、研究冲突FAIL | live-acceptance/summary.json |
| 真实Codex隔离修复 | 同标准计算题FAIL→PASS；减法/乘法仍PASS；原计算器回归61 passed | codex-improvement/improvement.json / calculator-regression.txt |
| 夜间加速模拟 | 36000秒模拟时钟、真实Harness/SQLite/Verifier，真实模型调用0 | night-demo-command.txt / night-demo/morning-report.md |
| 真实进程中断恢复 | A挂起、不重复执行；B独立完成 | test_actual_scheduler_process_crash_does_not_replay_running_job，全量测试包含 |

全量唯一失败仍是改动前已存在的 `tests/integration/test_memory_tools.py::test_explicit_source_read_and_structured_recall_are_not_silently_head_truncated`。它属于长原文读取，未在本轮顺便修复。因此**不能宣称全量验收全绿**。旧1215通过的基线及该失败保留在先前model-context交付中；本轮新增27项测试后为1242通过。

离线两个故意失败为虚假成功、错误引文；三个UNKNOWN为超时、未知副作用和待语义审查。离线50次是脚本模型调用，**外部生成请求为0**。这证明工程机制，不是智能水平或泛化成绩。20题均为开发/诊断题，没有声称独立未见集提升。

编译、依赖、wheel、差异及敏感信息检查的实际命令和结果分别见 `build-check.txt`、`wheel-smoke.txt`、`review-checks.json`。项目未配置独立类型检查器或linter，未安装新工具；这两项标为NOT_RUN，不冒充通过。自审按方案、代码、安全和交付清单执行，没有伪造独立审查agent。

交付wheel另见 `delivery-build.txt`。直接把wheel作为zip导入时，既有提示词资源包加载失败，原日志保存在 `wheel-zip-smoke-NOT_SUPPORTED.txt`；按正常安装方式装入独立目录后，隔离解释器确认新模块来自该目录、缺证据返回UNKNOWN、夜间CLI帮助均通过。此项不声称工作树任务能够脱离本地Git工程独立运行。

## 3. 夜间模式验收与真实能力边界

| 场景 | 实际行为 / 验证范围 |
| --- | --- |
| A需要审批 | WAITING_FOR_USER；不阻碍独立B完成 |
| DeepSeek费用达到上限 | 调用前预留失败即阻断，不进入生成；未知费用保留预留 |
| Codex达到次数限制 | 不继续启动CLI；真实订阅余额为UNOBSERVABLE |
| 一个任务连续失败 | 最多初次+配置重试次数；故意虚假成功演示两次后FAILED |
| 模型路线额度/认证错误 | 持久关闭该路线，继续独立程序任务，不反复探测余额 |
| 调度进程被终止 | 已落盘状态保留；未知RUNNING转待核对，不盲目重放；有效VERIFYING可按hash恢复 |
| 用户回来 | 每项状态、尝试、原因、证据路径、可观测费用和下一步写入morning-report.md |
| 10小时 | 加速模拟36000秒；真实墙钟10小时值守为**NOT_RUN** |
| 豆包 | 生成手工接力包，不调用App，不假装有API |

任务可直接执行原项目pytest等确定性工作；普通分析通过原Runtime调用DeepSeek；语义质量不足以机械证明时交给人工，不因模型说完成就自动COMPLETED。代码路线已真实接通Codex，但自动应用范围只有计算器四则运算绑定的受控示例。任意复杂代码任务挂起等待范围审核，**不是已经实现通用编程自主执行**。

默认真实模型预算0、最长10小时、保留20%。预算、开始时间、尝试数不因重启重置。2个Codex槽位配20%预留时仅可自动占用1个。费用门禁基于配置峰值费率和保守上界，不是Provider账户的账单级硬限额，也不能限制其他应用同时花费。无人值守仍需电脑保持运行，睡眠/断电不会继续工作。

测试和worktree执行限于可信项目代码；文件夹隔离不是对恶意代码的操作系统沙箱。Codex本轮使用read-only、never、ephemeral、ignore-user-config；无yolo、无扩权、无安装、无生产发布、无对外消息。所有本轮worktree保留待审查，未自动清理。

## 4. 真实回答、失败与成本

三题均为合成数据，deepseek-flash / high、自适应输出，单任务最长180秒、总生成尝试上限16；实际6次，无外部Judge、无私人聊天。Token：输入15306、输出1292、其中缓存输入12032。按2026-10-26价格快照估算 **0.001302396–0.002604792美元**，不是实际账单。计费来源为[DeepSeek官方Pricing §Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)。夜间每次调用的硬预留按配置峰值价，不依赖折扣一定命中。

逐题完整自然语言问题、回答、业务状态、工具与验证检查：

- [待办任务](live-acceptance/tools.add/report.md) / [完整结构化结果](live-acceptance/tools.add/result.json)：真实测试库中新增一项；模型自述不作为唯一凭据。
- [跨会话Memory](live-acceptance/memory.recall/report.md) / [完整结构化结果](live-acceptance/memory.recall/result.json)：原确认流程写入合成偏好，新Session检索读取，引用来自实际送达集合。
- [研究冲突](live-acceptance/research.conflict/report.md) / [完整结构化结果](live-acceptance/research.conflict/result.json)：模型要求补充两份规则原文/来源，没有调用研究工具，未发现两份合成资料中的30天/14天冲突，保持FAIL。

研究失败首先说明测试入口的可发现性不足：原题没有显式告知资料来源或预期使用的受控工具边界。模型要求材料是合理策略，但没有满足当前冻结任务。不能由此直接归因为模型不会研究，也不能悄悄把“请提供材料”改判PASS。独立的[输入变更提案](research-input-change-proposal.md)只提出后续审核方案，本轮没有改Gold或追加重跑来改善分数。所有生成请求均在原授权范围内停止，没有用完余额的目标。

受控改进真实使用 **1次Codex CLI进程**，gpt-6.1-sol；约127.736秒；可观测usage输入23052、输出29。CLI内部出现过error事件后完成，本轮只能观测进程与最终usage，**不能宣称内部只发出1次API请求或准确知道剩余额度/费用**。原始安全收据见 `codex-improvement/codex/call-1/receipt.json`。只保留白名单事件类型/usage及最终候选，不记录原生推理或认证。

改进证据为刻意在隔离worktree制造Add→subtract缺陷，由真实Codex根据公开源码和失败诊断返回受限候选，AST白名单应用，再跑相同三题和现有61项计算器测试。76份非目标源码受保护。它验证真实“发现→修复→同标准复验”机制，不能当作自然缺陷修复成功率。主工程计算器未被替换，修复未合并。

历史收据字段澄清：保留的早期 `offline-final/summary.json` 中 `generation_requests=44` 实为脚本调用计数；不是44次付费请求。最终 `offline-acceptance` 已区分 `model_invocations=50 / generation_requests=0`。旧 `codex-improvement/improvement.json` 的 `generation_requests=1` 表示CLI启动一次，后续实现用 `model_invocations` 和 `invocation_unit` 区分。旧实验原件不重写，不能将这几个计数相加冒充API总量。

## 5. 版本与复现

Git HEAD为 `220f6ec18d50e08316a54c3b34b81b0d00c6744d`，**加大量原有未提交修改**，不能仅凭HEAD复现本轮。

| 对象 | SHA256 |
| --- | --- |
| 离线验收src快照 | 604b6d4572ec5e122fbf1c91f7e64820da4481f5307b7a193b95a57b86573fab |
| 交付src快照 | 5705d3e66d442d4dc7973b15e8a16e4d62753ebc8ace34678f31cbf482a74280 |
| 真实DeepSeek执行src快照 | 4a1c8574f8a2157258e6425a268695852463d522d969563684b17418cb063989 |
| live-protocol.json | 5bf29b59533268f5a4c47464c8553566553d2837ac924f91b4eb2bc410fd833c |
| 修复前worktree src | 45c7f49f594d7874df75cd9388b142cc2436b6ab129054b502bda7f8e99504e1 |
| 修复后worktree src | 6c7aa7192b74fdc491d5d8e6d0673e9894b8ed4dcca378cf8d60480dd369eba6 |
| 修复三题冻结契约digest | 618fb6fd42b57564a35470388e6e8919e53e4bb417b50e9bccd0c72e86354aa8 |

src版本hash算法位于worker.source_version：排序路径名与`.py/.md/.js/.html/.css`正文共同进入SHA256。它不是zip字节hash或Git commit。`frozen-source/`保存真实调用时源码，`final-source/`保存20题离线重放源码，`delivery-source/`保存交付源码；离线验收到交付只删除journal.py末尾多余空行，审计逐字验证没有逻辑变化。真实调用版到离线版的差异主要为后续夜间封路回归等工程修复，**没有将最终版宣称为再次真实模型实测过**。协议/具体结果/脚本的逐文件hash见 `data-hashes.json`；旧报告未覆盖。

在项目根目录PowerShell执行，所有输出目录需新建且不同于已存在目录：

```powershell
$env:PYTHONPATH='src'
# 实际执行过的完整回归形式；本轮结果见full-regression.txt
.\.venv\Scripts\python.exe -X utf8 -m pytest -q
# 同一冻结20题、最终src离线重放；不调用模型
.\.venv\Scripts\python.exe -X utf8 scripts/run_adrian_frozen_eval.py --source reports/eval-v1/final-source --protocol reports/eval-v1/acceptance-protocol.json --out reports/replay-002
# 零真实模型的10小时模拟；不是真正运行10小时
.\scripts\night-adrian.cmd demo --out reports/night-demo-002
# 保存的真实候选离线复验；不会再次调用Codex
.\scripts\evaluate-adrian.cmd improve --out reports/repair-replay-002 --candidate reports/eval-v1/codex-improvement/candidate.json
# 重新生成已存在队列的早晨报告，不执行任务
.\scripts\night-adrian.cmd report --queue reports/eval-v1/night-demo/queue.json
```

真实DeepSeek复现形式如下，**会产生费用，不作为默认验收自动执行**：

```powershell
$credentialsFile=Join-Path $env:USERPROFILE 'agent-harness-credentials.json'
.\.venv\Scripts\python.exe -X utf8 scripts/run_adrian_frozen_eval.py --source reports/eval-v1/frozen-source --protocol reports/eval-v1/live-protocol.json --out reports/live-replay-002 --live --credentials $credentialsFile --proxy http://127.0.0.1:7890 --request-ceiling 16 --cost-limit 0.75
```

真实Codex修复复现形式为 `scripts\evaluate-adrian.cmd improve --out reports/repair-live-002 --codex --live`，会占订阅额度。日常队列初始化/执行/停止/恢复说明见 [NIGHT_MODE](../../docs/NIGHT_MODE.md)。无需模型时优先program回归，不必为耗尽额度而启动真实夜间批次。

## 6. 收尾与后续建议

已完成本轮有限实现和验证，停止扩展。进入试用前需明确普通任务的真实完成判据；只有模型最终答复不够。

下一轮若用户决定继续，优先：一、审查研究任务资料可发现性的输入提案，冻结后再测；二、另行设计通用编码任务的范围与OS级隔离，不能从计算器示例直接开放任意命令；三、改善真实Token/费率/账单观测，并审查语义UNKNOWN，不能用更多请求替代可信验收。本轮不自动开展这些事项。
