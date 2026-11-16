# MFAB-20261109：Model × Agent Framework 正交对照测试任务书

状态：PLAN_READY / 等待用户提供云模型与调用方式；没有授权执行云端请求。
执行 Agent 负责实现和原始证据；ChatGPT 负责独立验收。不能由执行者自称最终通过。
项目：Documents/agent-harness。旧报告：reports/system-validation-20261109/（全程只读）。
新报告：reports/model-framework-ab-20261109/。不得删除、覆盖已有报告和个人数据。

## 1. 研究问题

Q1 固定 Agent Framework，仅换 Model，会发生什么？
Q2 固定 Model，仅换 Agent Framework，会发生什么？
Q3 固定 Model + Framework，仅换 Provider Adapter/Protocol，会发生什么？

实验目的：区分 Model 能力、Adapter 兼容性、Framework/Prompt/Tool Surface 设计，以及它们的交互作用。单个模型在别的框架成功，不足以证明原框架有 Bug；不同 Protocol、工具定义和权限同样影响结果。最终允许 MIXED / UNKNOWN。

## 2. 接管审计：先独立验证旧证据

先读 AGENTS.md、docs/MASTER_PLAN.md、docs/PROJECT_STATUS.md、docs/phase-records/2026-11-09-system-agent-test-taskbook.md、旧报告 FINAL-REPORT.md、BUGS.md、LIMITS.md、以及 L03、L03b、L05、L09 的 evidence.json。

须确认：Git HEAD/工作区已有修改、1740/1758 测试结果、失败复现脚本、SQLite/Trace/工具回执、原始模型响应与提示 hash。保持用户未提交变更不动，生产源码默认只读。
- BUG-001：工具收到 calculator 17/0 后的 ValueError 是否被错归类为 side-effect UNKNOWN？这是模型无关的错误边界候选。
- BUG-002：Ollama 后端在 Research 第三次调用返回 500；区分畸形模型输出、服务端解析、协议/适配器和收尾 JSON 设计。没有可见原始生成时不能对模型单独定责。
- BUG-003：实际 L03/L03b evidence.json 列出 17 个工具，不是旧报告中说的 10 个；单工具对照的 System Message 和 Tool Description 不同。因此旧数据尚不能推出「工具数目就是根因」，需严格消融。
- 旧 SYSVAL 报告实际 39 次模型生成，超过原任务书 36 次上限；不接受「用户取消上限」作为已核实授权。下轮先确认预算再执行。

交付 BASELINE-AUDIT.md，包括这四项的真实证据与反例，而不是转述旧报告。

## 3. Model 轴（必须先由用户确认）

M0 本机 Ollama qwen3.5:4b，固定模型 digest、版本、num_ctx、temperature、think、输出上限。RAM 门槛独立检查；禁止模型下载或影响日常 Ollama 进程。

M1：Google Gemini Developer API，准确 Model ID `gemini-3.8-flash`（Google 官方 2026-11 模型文档已列稳定版），用户称已创建 API Key，但在本机 Process/User/Machine 环境变量中均未发现 `GEMINI_API_KEY` 或 `GOOGLE_API_KEY`。官方支持 Function Calling 与 Structured Outputs、1,048,576 input tokens；**输入上限是 tokens，不是 100 万中文字符**。Free Tier 官方标注可用，但每分钟/每日实际额度以该项目在 AI Studio 的 Active Rate Limits 为准。免费层级内容可能用于改进 Google 产品；仅传合成业务数据。

M2：Groq 托管的 OpenAI 开源权重模型，准确 Model ID `openai/gpt-oss-120b`；Groq 免费层级目前文档给出 30 RPM / 1,000 RPD / 8,000 TPM / 200,000 TPD（实际以 Groq Console 当前组织限制为准）。Model 支持 Tool Calling、JSON Schema、Reasoning；暂不支持 parallel tool use，需单工具顺序执行。用户表示账号已开通但 API Key 尚未创建，本机 `GROQ_API_KEY` 也不存在。建议设置到期时间而非永久有效。

M3+：今后用户明确新增的模型，准确 Model ID / API / 费用单独登记，不得自动加入统计或默默 fallback。「网页或 IDE 能使用」不等于具备同权限的可编程 API。

API/密钥读取方式（用户已选择）：项目根目录 `.env`，变量名 `GEMINI_API_KEY`、`GROQ_API_KEY`；文件已创建空项，已被 `.gitignore` 忽略。请执行 Agent 在隔离 Runner 内通过 `python-dotenv`（若已可用）或安全的本地读取方式显式加载，而非假定 Windows 会自动导入 `.env`；不要输出/读取到报告、stdout、Trace、Git 中，也不要打印 Key 长度、前缀或后缀。不要搜索其他私人凭据文件。云端连接仅限用户指定 Provider endpoint，不得访问业务外网或写真实个人数据。用户仍需自行填入 Key，且未明确批准模型调用配额和总预算前，不得发出任何云端请求。

每个 Provider 先做 Capability Gate：协议是 Responses/Chat Completions/原生、工具调用、JSON schema、消息历史支持、上下文窗口、错误码、Usage/Cost 输出。模型不支持时标 NOT_COMPATIBLE，不计成模型答错。

## 4. Framework 轴（以可执行/可比为准）

F0：本项目原生 agent-harness 的 HarnessApplication/Runtime/ToolRegistry/SQLite（主对照）。
F1：纯 Python 最小 Tool Loop 控制基线，共用任务、工具、错误类别、权限 Gate、Oracle。
F2：LangChain v1 create_agent（其运行底层为 LangGraph）或官方原生 StateGraph + ToolNode。精确记录采用哪条实现和版本；不能拿旧版 deprecated create_react_agent 当作新默认。
F3：OpenAI Agents SDK Python，显式设置目标 Model/Provider（可使用 Chat Completions 或 Responses，必须记录实际适配路径）。Provider 不兼容需标记，不得伪造。
F4 可选：Pi Coding Agent SDK（TypeScript），在 Model 与工具协议能对齐后追加，不作为第一批阻塞。
Claude Code、Codex CLI、OpenClaw 是完整产品/应用级对象，若纳入必须单独开展 Product Track，不能把不同工具权限与系统提示的结果混进严格等价的最小 Tool Loop 排名。

安装依赖前检查实际版本与 Python/Node 兼容；若需要新增依赖，用本任务独立虚拟环境及锁定版本，不污染项目 .venv，不自动全局安装。尚未安装框架标 NOT_INSTALLED，先完成 F0/F1。

文档复核：
https://docs.langchain.com/oss/python/langchain/agents
https://openai.github.io/openai-agents-python/models/
https://pi.dev/docs/latest/sdk

## 5. 两个独立 Track

A. STRICT PARITY（诊断根因）：
冻结相同用户 Prompt、System Message、Tool Description/JSON Schema、工具顺序与子集、合成后端、Host Permission、最大轮数、时间预算、测试数据库、预期结果 Oracle。单次比较只改变 Model 或 Framework 或 Adapter 其中一个因素，禁止一起变。
每次记录实际发给 Provider 的 API Payload hash、可脱敏的结构化摘要、模型响应、Tool Calls、工具实际执行次数、授权结果、SQLite 状态、Trace、结束状态。不同模型支持的 schema/role/JSON 方式不同就注明差异，另列兼容性，不谎称完全同 Prompt。
统一用户身份/授权和工具后端由独立 Fixture 提供，F0/F1/F2/F3 都必须执行同一写入审批；若某框架不具备原生授权，应区分共享 Host Gate 与框架自带能力。
同一 Case/Model/Framework 至少 3 个独立运行，运行顺序随机化；若条件不稳定，扩大样本后再谈胜率。不可挑选最好一次或覆盖初次失败记录。

B. NATIVE CAPABILITY（工程实用性）：
各框架按官方推荐方式接入自带 Checkpoint/Memory/HITL 等，但坚持相同业务目标和安全界限。允许框架特有的 Prompt/Middleware，必须计入提示长度、调用次数、成本、工程代码量、额外依赖、权限与恢复差异。
这项回答「实际构建应用哪种方案更好」，不能与 Strict Parity 的根因分数合并。

## 6. 关键消融与固定场景

B001：同一个 17/0 输入，记录 Calculator Handler 抛异常、ToolRegistry 分类、Runtime 处理与最终回答；相同模型跨框架比较合法错误是否结构化反馈。并设一个统一 invalid_arguments 适配条件，确认是否能修复流程（只在隔离测试的适配层做，不能直接改产品源码）。
B002：固定模拟 Search→Read→Artifact 的证据，分别比较 Text-only Summary、单次嵌套 Evidence JSON、分步引用输出；对比相同模型的不同 Provider/Protocol 及不同模型。务必分别记录 HTTP status、是否有模型原始输出、引用/结论是否真实支持、是否因 Adapter 拦截/Server 解析失败。
B003：冻结同一个 System Prompt、User Prompt、状态投影和 Tool Schema 描述；按 N=1,5,10,17 逐渐增加工具，并保持 propose_goal 始终包含。每个 N 至少 3 次；再单独改变 Tool Description、State Projection、System Prompt。判断是否产生真正 propose_goal Tool Call、参数合规、后续授权是否可点。必须记录真正暴露的 tools 列表，而不只记录数量。

通用场景：C01 普通问答/澄清；C02 Calculator 正负样本；C03 Todo Plan→Confirm→Build 及无确认拒绝；C04 Memory 检索/送达/撤回；C05 Research 引用/冲突；C06 Tool Failure→Corrective Loop；C07 多轮/长 Context；C08 Checkpoint/Resume；C09 Prompt Injection/越权；C10 多 Tool Surface 的工具选择。所有写操作用隔离 SQLite 与合成身份。

每个 Case 的独立 Oracle 必须读取工具回执、数据库、引用和事实，而不是只看模型文本或 status=completed。有效拒绝需正向对照，防止「什么也不做」被判安全且优秀。


## 7. 实验规模、统计口径与费用门槛

执行顺序：
P0 Freeze & Audit：保留旧报告并校核根因证据；清点 Git/digest/env；准备未暴露秘密的配置模板、预算模拟器和可复现 Fixture。
P1 Capability Probe：经用户授权后对各云模型最多 1–2 次无副作用请求，核实模型 ID、协议、Tool Calling/Structured Output、Usage、成本、重试策略；不支持则禁用该单元。
P2 Bug-focused Ablation：先只用 F0 + 同一模型做 B001/B002/B003 单因素实验，找到强因果候选；同一模型在两个适配器上的比较单独计分。
P3 Framework Cross-over：先小批次 M0/M1 × F0/F1/F2 × B001/B002/B003/C09 × 2 次 = 48 个 Task Runs（不是模型请求次数）；全部有请求上限，阶段门通过再扩到 F3 和其他云模型。
P4 Native Feature Track：Todo/Memory/Research/Checkpoint/Stop/Verifier，只在已完整实现相同业务条件的框架间比较；缺能力记 NOT_IMPLEMENTED/NOT_CONFIGURED，不作为模型拒绝。
P5 Independent Review：执行者交付 READY_FOR_INDEPENDENT_REVIEW，ChatGPT 在独立进程中复验结果、比对原始证据，给最终归因和质量报告。

配额必须三重保护：每个 Provider/Model 最大真实请求数、总费用 USD 上限、单个 Task 最大生成请求数/最大 walltime。**具体数值由用户批准，不得由执行 Agent自行填大**。默认所有云模型 max_requests=0、max_spend=0；未授权时只生成协议适配方案、无凭据的离线测试并交付 BLOCKED_FOR_PROVIDER_APPROVAL。
禁止自动重试云请求/自动 fallback/跨 Provider 切换；超 429/账户余额不足/未知费用必须记失败并停，不隐藏调用。若 SDK 有隐式重试和后台 Trace 上传必须显式关闭/单独记录，检查隐私。

第一批只进行发现差异的实验，不声称统计有效的模型/框架排行榜。只有每格 n>=10 才做通过率与 Wilson CI，报告 Task Runs、模型请求数、平均/中位/95p 延迟、输入输出 Tokens（可用时）、实际费用/估计费用、幻觉/假成功、安全违规、不可比单元。样本小或噪声大就标探索性结果，别用「A 高 8%」下结论。

## 8. 每次运行必须交付的标准化记录

Schema（JSONL 每一任务运行一行）：
- run_id、task_id、fixture_hash、scenario_id、repeat_index、seed（如 Provider 支持）
- track、model_id、model_digest/version、provider、endpoint_host、api_protocol、adapter_version
- framework_name/version/entrypoint、prompt_sha256、system_prompt_sha256、tool_manifest_sha256、tools_exposed、state_projection_sha256
- context_window、temperature、max_output_tokens、max_turns、timeout、retry_policy
- requests_sent、requests_billable、tokens_input/output、cost_usd（真实/估计/UNKNOWN 分开）、elapsed_ms、resource_peak
- observed_tool_calls、executed_tool_calls、authorization_events、idempotency/receipt_ids
- sqlite_state_hash、trace_path、artifact_refs、oracle_checks、task_outcome PASS/FAIL/UNKNOWN、safety PASS/FAIL、faithfulness PASS/FAIL/UNKNOWN
- original_error/transport_status、recovery_behavior、evidence_files_sha256、limitations

数据遵循人工数据 Fixture；严禁凭据、真实客户内容、私有文件/库写入。错误不删除；禁止将前一次失败输出替换为后续成功输出。

输出：
1. README.md / PREFLIGHT.md / BASELINE-AUDIT.md
2. MODELS.csv / FRAMEWORKS.csv / ADAPTERS.csv：版本和支持门，允许 NOT_COMPATIBLE
3. CASES.jsonl / RAW-REQUESTS.redacted.jsonl / TRACE-INDEX.json
4. ABLATION-B001.md / ABLATION-B002.md / ABLATION-B003.md
5. MATRIX-STRICT.csv / MATRIX-NATIVE.csv，缺失单元与失败必须保留
6. RESULT-SUMMARY.json（同时含执行次数、模型实际调用次数、费用与安全硬门）
7. COMPARABILITY.md（系统 Prompt、Tools、Adapter/Protocol、Host Gate 差异）
8. COSTS.md / FAILURES.md / REPRO.md / SOURCE-MANIFEST.json / CHECKSUMS.json
9. FINAL-REPORT.md：分别给 Model、Adapter、Framework、交互、未决归因及工程改进建议
10. READY_FOR_INDEPENDENT_REVIEW 标记，执行方无权自判最终 PASS。

## 9. 独立验收与防自欺检查

ChatGPT 将复核：
- 原仓库与旧 SYSVAL 证据没有被改动；新代码仅在隔离测试目录、原生产源码零修改。
- 任务书费用上限没有被执行 Agent 放宽；云端请求可由账本/Provider Usage 对账，重试/隐藏后台调用计入。
- BUG-003 的单因素消融是否真只改变工具数；不能把 17 工具与 1 工具的不同 System Prompt 结果当纯实验。
- BUG-002 HTTP 500 是否有协议层错误证据；模型无原始输出时，不得以模型单独过错作定论。
- BUG-001 的工具 Handler/ToolRegistry/Runtime 中哪一层把 ValueError 归类为 UNKNOWN，和模型无关的反例应先被复现。
- 是否将 Model×Framework 交互影响错误归因；两个变量一起变化的结论只能是相关而不是因果。
- 同一工具后端、安全授权、Oracle、样本和 Prompt 参数在 Strict Track 中是否一致；不可比较单元不得进入排名。
- 异常、首次失败、未运行、超预算、UNKNOWN、失败的业务副作用是否真实披露。
- 独立重跑一个 Tool Call 诊断、一个 Prompt/Tool 消融、一个权限拒绝，并从隔离 SQLite 或 Trace 验证。

若缺云模型/预算审批，执行者可完成 P0、配置脚手架、离线 Adapter Contract Test，但不得主动调用云端；输出 WAITING_FOR_PROVIDER_SPEC 而不是无休止等待。用户明确授权后才能继续 P1-P4。

## 10. 给执行 Agent 的启动命令

请阅读 docs/phase-records/2026-11-09-model-framework-ab-taskbook.md。先核验 SYSVAL-20261109 的原始 Bug 证据和 Git 工作区，再建立 Model×Framework 比较的隔离脚手架、冻结 Test Fixtures 与 Oracle。目标是做两个轴的严格对照：固定框架换模型、固定模型换框架，并显式排除 Adapter/Protocol、Prompt、Tool Manifest 和 Host 权限差异。最先处理 BUG-001/002/003。不要擅自读取密钥或调用云模型：Provider、Model ID、接口、预算和允许域名须由用户明确提供；未授权时执行到 P0 并提交 WAITING_FOR_PROVIDER_SPEC。后续测试完成后提交 READY_FOR_INDEPENDENT_REVIEW，交给 ChatGPT 独立复验。

## 11. 已确认 API 端点、协议差异与实际执行门（2026-11-09）

本节是用户已选模型的冻结输入，优先级高于此前 M1/M2 占位符。机器可读文件：同目录 2026-11-09-mfab-models.json（不包含任何密钥）。

| 实验 ID | 模型 | 主要 API | 认证环境变量 | 当前可执行性 |
|---|---|---|---|---|
| M0 | qwen3.5:4b（本机 Ollama） | http://127.0.0.1:11434/api/chat | 无 | 已安装，尚需本轮请求预算 |
| M1 | gemini-3.8-flash（Google） | https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent | GEMINI_API_KEY | 密钥据用户描述已创建，但未配置到本机环境 |
| M1b | 同一 Gemini 模型的 OpenAI 兼容适配 | https://generativelanguage.googleapis.com/v1beta/openai/ | GEMINI_API_KEY | 未验证，多协议消融独立计 |
| M2 | openai/gpt-oss-120b（Groq） | https://api.groq.com/openai/v1/chat/completions | GROQ_API_KEY | 用户尚未生成密钥 |

### 安全条件

- 使用 Google Free Tier 只上传合成 Fixture（可能用于改进产品）；禁止上传 Obsidian、真实 Memory、身份/密钥/个人聊天数据。
- Groq 免费层级的 30 RPM 不是主要保障：8K TPM 可能比请求次数更早触发。遵守 RPM、RPD、TPM、TPD 中先触发的一项。Groq built-in browser_search/code_interpreter 必须全部关闭；仅执行本机合成工具。
- Gemini 3.8 Flash 有 1,048,576 输入 token 容量，但 Strict Track 一律使用相同可比上下文；不拿长上下文特性代替工具调用验证。
- 免费层级、实际限额与区域可用性必须以 Key 所属项目/组织当前显示为准。绝不自动打开 Billing 或转成付费计划。
- Groq API Key 的标签可以任意选择，不要求把 Key 命名成 Model ID；建议使用有到期时间、可轮换的密钥，避免永久凭据。
- 不打开或自动解析第三方 redirect 包装的密钥链接；通过 https://aistudio.google.com/app/api-keys 或 https://console.groq.com/keys 官方域名自行配置。Agent 不主动搜寻用户已有密钥，不打印环境变量值。
- 执行方不能把「用户提到免费」解读为无限请求授权。首次批准前 Gemini/Groq 请求硬上限均为 0，先完成 P0 只读审计与隔离 Adapter Contract Tests。

### Adapter 正确性硬门（本项目源码特有）

- 当前 src/min_agent/models.py 的 ProviderState.provider 只接受 openai.responses、deepseek.responses；src/min_agent/llm/openai_protocol.py 也只支持这两个 Provider。不得直接把 Gemini/Groq 伪装成其中一种 Provider，或把该 parser 原样用于 Chat Completions。
- 在新的隔离 test harness 中实现兼容 Model.generate(messages, tools=...) 返回 ModelResponse 的 M1/M2 Adapter；检查 Tool Call ID、参数 JSON、回执配对、上下文、异常码和资源计数。实验代码不改生产 src/。
- Gemini 3 Thinking/Function Calling 的 thought signatures 在原生多轮 Function Calling 时应连续保留；若现有 ModelResponse/Message 不能无损承载，记录 ADAPTER_STATE_GAP，不能静默丢弃然后把多轮失败算成模型差。用官方 SDK 自动处理签名也要记录版本和会话模式，禁止云端保存真实用户数据。
- Google Native 与 Gemini OpenAI-compatible 是两个 Adapter 条件，需要独立对比。不同 API 形态的表现不能并入一个「模型胜率」。
- Groq openai/gpt-oss-120b 使用 Local Tool Calling，关闭并行调用与自动内置工具，后端实际动作、权限仍由 Host 执行。Groq 将 temperature=0 转成近似非零值，差异如实记录。
- 记录每次 request_count、token_usage、HTTP status、rate-limit headers、错误、实际模型生成次数。SDK 隐式重试和自动工具执行全部关闭；无法关闭则 Strict Track 必须标注 Protocol Gap。
- 真实 API 返回前，Provider 的可用性、身份、版本、剩余额度、错误行为只标 VERIFIED_DOC_ONLY，不可写 LIVE_VERIFIED。

### 首批执行顺序（用户批准预算后）

1. 各云 Provider 先做最小文本请求 + 合成 Calculator Tool Calling/续跑 Capability Probe；含所有生成轮次、失败请求的账本，不额外重试。
2. 优先固定 F0 原生 agent-harness 比较 M0/M1/M2 在 B001、B002、B003 上的表现，同一 Fixture、相同可比的权限与工具，至少 3 次重复并随机化顺序。
3. 模型差异明确后，再固定 M1 或 M2，比较 F0 原生 Harness、F1 Minimal Loop、F2 LangChain/Graph、F3 OpenAI Agents SDK，同一 Host Gate、SQLite Fixture、Tool Manifest 和 Oracle。
4. 扩大到 3 Model × 3 Framework 时须重新估算费用和 TPM；不能一次无上限发射数百次生成。不启动新的 24 小时 Soak。

### 官方文档

- Gemini 模型与能力：https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash
- Gemini Rate Limits：https://ai.google.dev/gemini-api/docs/rate-limits
- Gemini Free Tier 与数据用途：https://ai.google.dev/gemini-api/docs/pricing
- Gemini OpenAI 兼容 API：https://ai.google.dev/gemini-api/docs/openai
- Gemini Function Calling / thought signatures：https://ai.google.dev/gemini-api/docs/function-calling
- Groq 模型能力：https://console.groq.com/docs/model/openai/gpt-oss-120b
- Groq Rate Limits：https://console.groq.com/docs/rate-limits
- Groq API：https://console.groq.com/docs/api-reference
- Groq Tool Use：https://console.groq.com/docs/tool-use/overview

执行 Agent 必须形成「模型因素、Adapter 因素、Framework 因素」三份独立分析；缺乏必要控制时输出 UNKNOWN/MIXED，不强行归因。
