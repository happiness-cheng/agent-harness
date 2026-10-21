# Agent Harness

从一道“实现最小 Agent”的笔试出发，逐步构建我心目中的个人 Agent：先理解意图，在明确边界内执行，让过程与结果可以追查。

## 我的设计起点

**Agent = LLM + 工具 + 上下文 + Harness**

我把 Harness 单独列出来，是想把模型之外的责任讲清楚：模型看到什么、怎样使用能力、行动如何获得授权、失败后如何恢复。它也管理上下文和工具，这个公式表达工程分工。

我最初将单个 Agent 分成四个部分：

| 部分 | 我关心的核心问题 | 当前落点 |
| --- | --- | --- |
| **① 决策与上下文** | 模型应该知道什么？怎样理解意图并决定下一步？ | 系统提示、需求对齐、Plan / Build、渐进披露与上下文管理 |
| **② 知识与能力** | 模型如何完成任务、如何与外部交互？ | 工具契约、待办与提醒、公开研究子循环、会话记忆与 Skill |
| **③ 执行与运行时** | 如何安全执行？出错怎样处理？ | Session 隔离、权限、预算、检查点、错误反馈与 trace |
| **④ 评估与进化** | 如何验证修复、发现缺陷、持续改进？ | 已有测试与验收；完整自动评估和持续进化后续实现 |

前三部分构成本次最小 Agent 的实现范围；四个部分按职责协作，不是四个必须依次调用的独立服务。

~~~mermaid
flowchart TB
  U[用户的想法与要求] --> D[① 决策与上下文]
  D --> K[② 知识与能力]
  K --> R[③ 执行与运行时]
  R -->|实际结果、错误与状态| D
  D -->|澄清、确认与答复| U
  R -.->|运行证据| E[④ 评估与进化：后续完整实现]
  E -.->|验证后的改进| D
~~~

## 我最在意的几个选择

> 模型决定上限，但如果意图理解错误走错方向那就越走越远

这是我在需求讨论中的原话。很多用户最初只有模糊想法；我希望 Agent 能先说明理解、对齐目标，再执行有副作用的任务。简单对话与目标清楚的后续步骤不必反复规划，也不为意图识别单独增加一个分类模型。

| 我的判断 | 因此做出的选择 |
| --- | --- |
| **理解方向，再投入执行** | 用户可以进入 Plan / Build；写入先提出具体动作并定向确认。只读不等于没有隐私风险。 |
| **工具要说明什么时候用、不能做什么** | 描述、参数 Schema、返回值和错误反馈共同构成契约。“明天写周报，记得提醒我”也应识别需求；待办日期与提醒时刻分别处理。 |
| **上下文应有效，而不是越长越好** | 必要信息渐进提供；大结果保留原文和冻结预览；摘要结合任务目标，压缩后仍能追问、回查与恢复。 |
| **复杂任务可以继续，失控循环必须停下** | 接近预算先整理成果，硬门前停止；保留状态续跑。模型说完成，还要对照真实执行与验收证据。 |

这些选择来自我与 AI 的逐步推敲：我提出目标和偏好、追问边界并做取舍；AI 协助解释、挑战、实现和验证。公式、四层划分及上面的原话见 [需求共创 · 用户005、007](docs/ai-conversations/01-design.md)，更完整的过程见 [我的设计思路](docs/AGENT_VISION.md)。

## 阅读路径与当前实现

| 想了解什么 | 从哪里看 |
| --- | --- |
| 我为什么这样设计，讨论中怎样取舍 | [我的设计思路](docs/AGENT_VISION.md) |
| 五个架构模块的完整论证 | [架构答案：前四题保留原论证，第五题纠偏](docs/ARCHITECTURE_REVISIONS.md) |
| 哪些已经实现、怎样验证、还有什么限制 | [笔试要求对照](docs/SUBMISSION.md)、[设计取舍](docs/DESIGN.md)、[验收记录](docs/ACCEPTANCE.md) |
| AI 怎样参与，提示词怎样调整 | [两份可见对话](docs/ai-conversations/README.md)、[Prompt 记录](docs/AI_PROMPT_LOG.md)、[问题解决记录](docs/PROBLEM_SOLVING.md) |

当前是本地 Python 工作台：主循环自行实现，网页负责交互，后台负责状态、工具、权限和恢复。2026-10-21 提交复核 **901 项离线测试通过**，包含两项本机 Edge 测试；有限真实 LLM 验收另有记录，测试数量不代表模型任务成功率。

真实运行使用 DeepSeek 官方 Responses API，model ID 为 `deepseek-flash`，reasoning effort 为 `high`。OpenAI 适配器保留，真实请求曾受计费额度阻塞；双模型比较暂缓。基础功能与目标设计的差距见文末限制，特别是缓存前缀、累计资源预算、详细日志和第四部分，仍需继续完善。

## 启动

Windows、Python 3.13 或更高版本。在项目目录执行 PowerShell：

新环境按“获取代码 → 安装 → 启动”运行：

~~~powershell
git clone https://github.com/happiness-cheng/agent-harness.git
cd agent-harness

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps .

.\.venv\Scripts\python.exe -X utf8 -m min_agent.web --demo
~~~

这个入口验证界面和持久化；使用真实模型时，停止演示进程并按下面的命令启动。代理为可选项：没有对应本机代理时，去掉 `--proxy`，不要直接照抄端口。

~~~powershell
# 在项目根目录；凭据文件在项目外，路径由启动者明确指定。
$credentialsFile = Join-Path $env:USERPROFILE 'agent-harness-credentials.json'
.\.venv\Scripts\python.exe -X utf8 -m min_agent.web --live --credentials-file $credentialsFile --proxy http://127.0.0.1:7890 --desktop --database data/workspace.sqlite3 --port 8765
~~~

打开 [本地工作台](http://127.0.0.1:8765)。后台需要保持运行；Ctrl+C 关闭。端口已被当前后台占用时直接打开页面，不重复启动。

凭据放在项目外的指定 JSON 文件，DeepSeek 字段名为 deepseek_api_key；不要把真实值写进源码、命令参数、截图或日志。应用不读取环境密钥、Codex auth，也不自动换提供商。代理参数只用于模型和公开搜索；受 SSRF 防护的网页读取直接连接公开地址。

凭据文件最小格式如下，使用自己的真实 API key 替换占位符后保存在指定的项目外路径：

~~~json
{"deepseek_api_key": "REPLACE_WITH_YOUR_KEY"}
~~~

默认启动不读凭据、不调用模型：

~~~powershell
.\.venv\Scripts\python.exe -X utf8 -m min_agent.web --demo --database data/demo.sqlite3
~~~

离线模式保存消息并明确回复“离线演示”，不能证明自然语言理解或真实工具选择。测试使用脚本模型检查控制流程。

开发测试另装 `requirements-dev.lock`。依赖锁在 Windows / Python 3.13 验证；不承诺未经验证的平台。也可双击 `scripts/start.bat` 启动已安装环境的离线演示。

## 使用

- 新建会话后直接提问。普通消息默认排队，当前任务结束后处理；暂停或等待确认时队列保留。
- 待办、提醒等写入先由模型提出具体动作，用户选择 Build 并定向确认目标。每个确认动作实例只能消费一次，换工具调用 ID 或重启都不能补次数；两条同内容事项需明确确认两个实例或新提案。Plan 只能查询。
- “引导当前任务”把新要求交给当前执行；默认重新确认范围。停止后保留已执行结果，在调用边界阻止后续操作，不撤销已经发生的写入或正在进行的 HTTP 请求。
- 暂停后用“继续本任务”恢复对应检查点；补充文字填在输入框。直接发送普通“继续”仍属于队列，不能绕过定向恢复。
- 需要联网时勾选“允许公开网络调查”。研究循环只收到问题与必要约束，只能搜索和读取公开网页；返回来源、引文位置、限制和未查明项。
- 工具结果里的“查看原文”按当前会话受控引用分页读取，不能指定本地文件路径。
- 日期截止与定时提醒独立。提醒必须给明确时刻及时区；不会从“明天截止”猜测早上九点。
- 每个会话独立保存历史、待办、提醒、原文、预算、记忆和未读。网页刷新或后台正常重启可以恢复。

Windows 系统通知目前关闭，实机通知未通过：用户报告未看到测试弹窗，原生 API 返回 DISABLED_FOR_USER，注册表 ToastEnabled=0。应用不会擅自开启系统设置。会话通知照常可见；桌面 API 接受不代表用户看到，也不会自动标记已读。启用系统通知后仍需实测显示和点击定位。

## 系统设计

| 层 | 技术与职责 |
| --- | --- |
| Runtime | Python，自行实现循环、状态、权限、预算和恢复 |
| 数据契约 / 持久化 | Pydantic 严格校验、SQLite 事务 |
| 模型 / 公开信息 | HTTPX、DeepSeek / OpenAI Responses 适配、Bing 搜索与 trafilatura 正文提取 |
| 本地交互 / 验证 | FastAPI、Uvicorn、原生网页、pytest、Playwright / Edge |

~~~text
src/min_agent/       主循环、状态、上下文、工具、模型适配及网页
tests/              单元、集成、意图场景、浏览器和受控真实调用测试
scripts/            真实验收与独立安装检查入口
docs/               架构答案、设计取舍、Prompt、问题记录和验收证据
docs/ai-conversations/ 两份经批准的脱敏可见对话
requirements*.lock  已验证的依赖版本
~~~

~~~mermaid
flowchart LR
  UI[本地网页] --> API[Cookie / CSRF / 归属检查]
  API --> APP[会话单写入者与控制队列]
  APP --> RT[自写 Runtime]
  RT --> CTX[冻结投影 / 窗口 / 压缩]
  CTX --> LLM[指定真实模型]
  RT --> TOOLS[计算 / 待办 / 提醒 / 研究]
  TOOLS --> DB[(SQLite)]
  APP --> DB
  DB --> SCH[后台调度器]
  SCH --> NOTE[会话通知 / Windows 通知]
~~~

主循环在 [runtime.py](src/min_agent/runtime.py)，可信入口在 [application.py](src/min_agent/application.py)。真实工具参数经过 Pydantic 校验、模式和精确动作范围检查；执行结果与模型声明分开。业务事实以实际存储和工具返回为准。

SQLite 保留 sessions、todos 两表和版本 1。新增功能放在受校验的会话快照 extensions，未增加表或迁移。data/workspace.sqlite3 是当前工作库；原有 data/agent-harness.sqlite3 不被验收脚本使用。不要同时启动两个后台写同一数据库。

每个逻辑任务默认累计最多 40 次模型、60 次工具；每个执行片段 6 次模型、12 次工具。已 completed 后的普通新输入开启新逻辑任务账本，保留同一会话历史及旧任务统计；暂停、等待和检查点继续复用原账本，不通过发普通消息清零。BudgetRecord ID 同时标识逻辑任务，TaskState.task_id 是本次执行，segment_id 是预算片段。当前没有对耗尽的旧任务追加累计额度的入口。

研究默认子预算 6 次模型、8 次工具，与主任务共享扣款；软阈值预留一次无工具整理。HTTP 客户端另有每进程最多 100 次请求的硬门，可用 --max-requests 降低；这是独立的显式进程请求额度，新任务不会绕过它。不自动重试。

上下文预算是完整消息和工具 JSON 的 UTF-8 字节上界，加 4096 输出预留及 1024 安全余量，默认窗口预算 32768；不是精确计费 token。实际 provider usage 单独记录。

## Memory 与恢复

大工具结果先保留原文，模型接收冻结预览及引用。正常轮次沿用同一投影，补读作为新消息追加。当前会话的 recall_memory 工具只返回相关归档摘要和引用；不跨会话召回，也没有向量数据库。

召回时机与放置方式：每次模型请求前构造可见上下文；已有压缩摘要自动以 `untrusted_session_memory` 参考块放在系统/应用状态之后、未压缩历史之前。更早归档不全量注入，由模型在需要历史依据时调用 `recall_memory`；结果作为工具消息追加到末尾，必要时再按引用调用 `read_artifact`。这些内容不能修改权限或充当系统指令。

完整交互结束时，原文范围/哈希、任务和账本 ID、工具执行状态、未核实的助手文字与终态一起落盘，不额外调用模型。等待或暂停尚未闭合的交互不提前归档，最终继续完成时保留原起点。后续语义压缩另记一条记录，不替换已有交互出处。

超窗时先整理已有摘要，再压缩较旧的完整交互，保留最近交互和工具配对。摘要覆盖位置、源哈希及版本持久化，校验和窗口通过后才切换；原始记录保留。日期、否定、用户纠正和出处要求保留，但结构校验不能保证所有语义都正确。相同源连续两次失败或压缩后仍超窗会熔断并暂停。

检查点绑定会话、拥有者、目标版本、历史哈希及原预算。恢复使用新片段并保留累计计数。未知写入先核对真实状态；基础设施同一操作 ID 不重复新增，合法的同名新待办不会被合并。运行中崩溃后不盲目重放工具。

桌面外发采用先记录尝试再发送：崩溃可能留下 unknown，需用户决定是否重发；会话事件独立持久化和去重。不承诺桌面“恰好一次”，也不承诺关机、休眠或后台退出时准时提醒。

## 测试与打包

~~~powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest -q
.\.venv\Scripts\python.exe -X utf8 -m pip --isolated check
.\.venv\Scripts\python.exe -X utf8 -m compileall -q src tests scripts
.\.venv\Scripts\python.exe -X utf8 -m pip --isolated wheel --no-build-isolation --no-deps --no-index --wheel-dir dist/local-build .
~~~

pytest 默认排除 live，普通回归不调用外部 API。浏览器测试使用本机 Edge 无头模式，需安装 Edge 和 Playwright Python 包。Type/Lint 尚无配置，不能标为已通过。

审查前版本为 882 passed / 3 deselected；后续审查修复与最终实际结果以 [审查修复记录](docs/REVIEW_FIXES.md) 为准。旧安装包保留，不将旧包验证等同修复后已验收。

真实验收脚本须 --allow-live、指定凭据及明确请求预算；不要把历史批次脚本当成可随意重跑的命令。原始报告保留失败，不覆盖改成成功。完整结果、截图、实际命令和剩余外部阻塞见 [验收记录](docs/ACCEPTANCE.md)。

交付文档：[设计取舍](docs/DESIGN.md)、[提示词调整依据](docs/AI_PROMPT_LOG.md)、[问题解决记录](docs/PROBLEM_SOLVING.md)、[笔试对照与复核](docs/SUBMISSION.md)。本机恢复日志、凭据、数据库和构建缓存不进入 Git。

## 限制

这是本地单用户应用，不是生产多租户服务。环回 Cookie 身份防止网页伪造 owner，不能抵御已掌握本机账户的进程。默认无通用 shell、任意代码执行、开机启动或周期性模型任务。

审查 F03 / F06 仍未补齐：application_state 仍逐轮放在早期位置，目标变化会影响其后的缓存前缀；未测真实缓存命中率。预算仍只硬限制调用次数，窗口估算、单次输出和 HTTP timeout 不能替代累计 token、整个任务 deadline 或费用预算；当前不支持这些累计资源上限，不提供精确费用承诺。

研究与主循环共享有父子关联的 trace，按会话保留最近20次 run，每次最多2000事件。会话接口提供 trace_runs 索引，GET /api/sessions/{session_id}/traces/{run_id} 按归属读取；不包含完整参数、密钥或隐藏推理。旧 last_trace 仅作为最近一次展示，后续消息不再覆盖唯一历史记录。

trace 已满足基础调用追踪，但尚不足以完整诊断现场：部分模型异常归为统一错误码，外层异常或进程崩溃可能丢失未保存的 trace；详细日志与及时落盘属于后续优先项。

升级前缺少消费记录的 v1 目标确认可以读取，但写入须提出并确认新提案；仅重新确认旧 ID 不会生成新的执行次数。旧历史、业务数据和预算不因此清空。

公开搜索固定访问 Bing 的公开搜索页，复用 DDGS 的字段与链接解析，不调用其已禁用的 Bing 引擎或自动回退。真实结果质量可能波动，无付费 SLA；完全没有英文查询词的明显无关结果被拒绝，其他结果仍须模型核对，不能把粗筛当语义保证。网页工具逐跳检查公开地址并固定 IP；gzip/deflate 使用受输出上限约束的解压，其他编码拒绝。直连不可达或搜索失败不能解释为没有相关事实。

会话及 provider 协议状态保存在本机 SQLite，未做静态加密；不要把 data 目录当作可公开附件。密钥不入库，网页和验收报告不展示原生推理状态。有限真实样本不能证明所有任务都可靠。

历史修复及证据见 [审查修复记录](docs/REVIEW_FIXES.md)。两份脱敏聊天已获用户公开发布授权；历史记录中的“仅本地”“尚未提交”描述的是当时状态。当前范围与未通过项以 [提交说明](docs/SUBMISSION.md) 为准。

## 参考与后续学习

后续会结合 [李博杰《深入理解 AI Agent》开源仓库](https://github.com/bojieli/ai-agent-book) 的上下文、工具、评估与持续进化章节，以及 LangSmith、OpenAI Agents SDK 和 Claude Code 的公开设计继续完善。参考资料与本人选择的关系见 [设计思路](docs/AGENT_VISION.md)。本项目的核心 Runtime 保持自行实现。
