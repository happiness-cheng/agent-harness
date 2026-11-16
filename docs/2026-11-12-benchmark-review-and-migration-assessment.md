# 2026-11-12 综合报告：Benchmark 校准、全量测试复盘与生产迁入判定

> 生成时间：2026-11-12（GMT+9）。本报告是**跨仓库只读梳理 + 一次有界真实模型补跑 + 离线全量复验**的结果，
> 不是验收通过声明，也没有修改任何生产源码、没有 Git commit / push、没有回写历史报告。
>
> 事实来源分为三类，全文分别标注：**[本次实跑]** 本次会话亲自执行；**[历史记录]** 仓库文档/manifest 中已存在的证据；
> **[用户提供]** 用户贴入的梳理文本或外部实验结果。三者不混算。
>
> 涉及两个仓库：
> - 生产 Agent：`C:\Users\陈独秀\Documents\agent-harness`（`D:\Users\陈独秀\Documents\agent-harness` 为同一目录的另一盘符视图，inode 相同）
> - 评测系统：`D:\agent-framework-bench`

---

## 0. 摘要：先回答最关心的两个问题

### 0.1 一句话结论

**这轮工作把「测得准」补齐了，但没有把「做得好」推进；bench 里没有一份成品代码可以直接 copy 进生产。**
生产真正缺的能力，与 bench 修的问题并不重合。

### 0.2 四问式回答

| 问题 | 回答 |
|---|---|
| **发生了什么** | 用户给了一份系统梳理，要求结合此前全量测试写完整报告，并追问「有哪些代码可以并入生产」；追加要求补上 Benchmark 详细描述、审查方式与审查结果、多框架×多模型运行对比。此前 Gemini 列只跑了 1/35 案例（免费层配额耗尽），用户允许先把该模型补上再总结。 |
| **执行了什么** | ① 逐文件核对两个仓库源码、阶段记录、工程日志与 manifest；② **[本次实跑]** 复跑 bench 离线全量；③ **[本次实跑]** 在明确硬上限下补跑 Gemini 列；④ 逐项核对生产源码，判断每个 bench 改动在生产是否有对应缺口；⑤ 写成本报告。没有改生产源码。 |
| **为什么这样做** | 项目约定「先在 Benchmark 复现和验证 → 独立复验 → 核实生产是否确有同类缺口 → 仅迁入适用的最小改动」；且外部模型调用须有界授权。因此先取证、再补跑、最后才下迁入结论，不把「组件已实现」当成「复杂任务可靠完成」。 |
| **结果怎么样** | 离线全量 **[本次实跑]** 320 项 / 308 PASS / 12 SKIP / 0 FAIL·ERROR，61.448s；Gemini **[本次实跑]** 从 1/35 补到 3/35、12 次运行、18 次真实发送，仍 `TRUNCATED_BY_LIMIT`。**可直接并入生产的成品代码：0 个**；值得按生产接口重写迁入的是 2 项机制（+1 项暂缓）。未完成：独立 Agent 审查、真实模型全量复测、生产迁入与发布、语义质量 Judge。 |

### 0.3 「哪些代码可以并入生产」的直接答案

- **可以直接搬过去的成品代码：0 个。** 15 个 Task ID 的改动里，评分器、比较门禁、诊断、报告、实验适配器属于评测系统专用；其余机制类（调用前拦截、终态保护、参数校验、逐次发送计量、任务内历史）**生产已有等价或更贴合的实现**，重复迁入会造成两套口径。
- **值得迁入的是机制，不是文件**，且必须按生产接口重写 + 同条件回归：
  1. **原始请求/响应证据链**（先脱敏落证、再交给解析层）——直接补上 BUG-002 缺「模型原始输出」的归因盲点；
  2. **把硬预算从「次数」扩展到 Token / 费用维度**——生产 `llm/client.py` 已经在记 `input_tokens/output_tokens/cached_input_tokens`，`budget.py` 明确「不估算 token 或费用」，是现成数据基础 + 明确缺口；
  3. （暂缓）**跨任务整批共享额度 + 合作式异步截止**——仅夜间批量/长任务场景需要，优先级低于上面两项。
- **优先级更高的替代动作**：先修生产 11-09 全量验证暴露的 3 个真实 BUG（详见 §8.5）。这才是「让生产 Agent 确实完成得更好」的路径。

---

## 1. 先分清楚：我们有两个系统

| 系统 | 用途 | 本轮状态 |
|---|---|---|
| **生产 Agent（agent-harness / Adrian）** | 接收用户任务，调用模型与工具，管理权限、上下文、记忆、状态与恢复 | 实际做事的个人助手；本轮**零修改** |
| **评测系统（agent-framework-bench）** | 固定任务、跨框架运行、记录过程、核验结果与预算、保证可比性 | 检查 Agent 到底做得怎样；本轮所有修复都在这里 |
| 生产仓库内 `min_agent/evaluation/` | 生产自身验收、夜间任务、有限改进实验 | 与跨框架 Benchmark 是不同层次，不能混为一谈 |

**关键边界**：本会话此前连续修复（CAL-001～009、RETRY-010～GUARD-015）**全部位于 Benchmark**。
生产仓库里已有的功能不能算作这轮新增成果；反之，bench 的测试通过也不能当作生产已验收。

---

## 2. Benchmark 系统详细描述

### 2.1 定位与设计原则

`agent-framework-bench` 是**可复现的跨框架对照与只读诊断平台**，不是性能排行榜，也明确不实现「自动修改代码 / 自动合并」。
核心原则：同一份冻结场景、同一套共享工具与 host 记录、同一模型 digest 下，只改变一个自变量（框架 / 模型 / 适配协议）；
证据不足一律保留 UNKNOWN，不转 PASS；跨模型 `model_digest` 不同时 `comparison_gate` 拒绝排名，只做并列展示。

### 2.2 分层架构

```
冻结场景 (5 frozen + 30 V0.3 = 35)
   │
   ▼
Runner (串行执行 / 观察生成 / 比较门禁 / 首次分歧)
   │
   ├──► 4 个 Adapter ──► 模型端点 (loopback Ollama | openai_compatible 云端)
   │                          │
   │                          └─ RecordingAsyncTransport (逐次 HTTP 录制 + 列/任务预算 + 响应保护)
   │
   ├──► ToolEnvironment (共享工具 + host 事件/状态 + 调用前额度拦截)
   │
   ▼
独立验证器 (legacy 默认 / calculator-own-record-v1 显式选择 / 四维结果)
   │
   ▼
失败分组与首个事件差异诊断 ─► 只读候选报告 ─► 人工审核状态（不自动执行补丁）
```

另有**完整 Runtime 入口** `agent_bench/full_runtime.py`：用临时 SQLite、脚本模型，通过生产 `HarnessApplication` 的
**公开 API**（`create_session / propose / set_mode / confirm / submit / wait / snapshot / resume / close`）驱动真实生产装配，
而不是复制 `application.py` 或用桩替代生产组件。它证明的是「生产链路能被隔离驱动」，**不能把普通共享工具轨道的成绩当成生产全部组件已验收**。

### 2.3 核心模块清单

| 模块 | 职责 |
|---|---|
| `contracts.py` / `v03.py` / `v04.py` / `v04b.py` | 冻结场景、`build_pool()` 生成 30 个 V0.3 版本化场景（故障注入、预算耗尽、长上下文、多轮纠正、权限冲突） |
| `adapters/{own_harness,openai_agents,pydantic_ai}.py` | 三个正式适配器，共用 `model_endpoint.resolve()` |
| `reports/full-model-20261110/cloud_adapters.py` | 第 4 个**实验**适配器 `PIAIAdapter`（pi-agent 0.1.0 + `recorded-v4`），不属于 `agent_bench` 正式包 |
| `full_runtime.py` | 驱动真实 `HarnessApplication` 的隔离入口 |
| `tools.py` | 统一工具与授权；模型只能**提出**调用，状态由 host 记录，模型不能直接改状态或写文件 |
| `runner.py` | 串行运行、观察生成、比较门禁、`first_divergence` 首次分歧；`response_contract` 一致性检查 |
| `capture.py` | `RecordingAsyncTransport` 逐次真实发送录制、`RequestLedger`、`ColumnBudget` 列级共享额度、异步 deadline、`chat-response-v1` 原始响应校验 |
| `lifecycle.py` | V0.4 请求生命周期账本：`attempted / blocked_before_dispatch / dispatched / completed / transport_error / retry_attempt` 分离 |
| `model_endpoint.py` | 两模式端点（默认 loopback 与显式 `openai_compatible`）；密钥只按**环境变量名**读取，公网强制 https，拒绝 URL 内嵌凭据 |
| `verifier.py` / `calculator_verifier.py` / `scoring.py` | 冻结验证器、窄范围计算器验收协议 `calculator-own-record-v1`、显式选择评分协议并替换主结果 |
| `diagnosis.py` / `report.py` / `report_v04.py` | 确定性失败分组、根因**假设**（不自动认定）、只读汇总报告 |
| `simulator.py` | 受限本地模型模拟用户；**正式评分仍用固定脚本**，未证明能代表真实用户分布 |
| `soak.py` / `resource.py` / `experiment0*.py` | 7 小时长跑调度、资源安全门、历史实验脚本 |

### 2.4 场景与输入

- `scenarios/` 下 5 个冻结场景：`calculator_only`、`todo_add_clarify`、`todo_add_update`、`todo_cancel`、`todo_update_existing`。
- `v03.build_pool()` 生成 30 个版本化场景，合计 **35 个**，是三列实验的固定输入。
- V0.3 场景**故意注入**参数错误、超时、故障、权限冲突；这些场景的 FAIL 属于「预期行为一致」，不是回归。
- 场景数量不代表覆盖所有真实任务；**语义质量 Judge 未实现**，开放式写作/研究质量不能用 PASS 数代替。

### 2.5 框架适配器支持矩阵

| 适配器 | 回环 Ollama | 云端直连 | 说明 |
|---|---|---|---|
| `own_harness` | 支持（生产 Harness 的 `OllamaModel`，原生 `/api/chat`） | **明确 UNSUPPORTED** | 防止它悄悄走本地模型污染对照；要测云端须回环模式 + 本地兼容代理 |
| `openai_agents` | 支持 | 支持 | OpenAI Agents SDK 0.24.0 |
| `pydantic_ai` | 支持 | 支持 | Pydantic AI（有 `httpx.AsyncClient` 弃用警告，保留） |
| `pi_ai`（实验） | 支持 | 支持（PI-012 恢复） | 独立实验目录，经 `recorded-v1 → v4` 迭代；不属于正式包 |

有界批量驱动 `run_model_column.py` **拒绝本地 qwen 列**（本地原生入口未接统一列预算），这不是「模型不支持」。

### 2.6 执行、录制与预算链

- **逐次发送**：`RecordingAsyncTransport` 把每次真实 HTTP 发送都经过预算入口，分配独立 `request_id`，保留 429/503 中间响应；逻辑调用数与实际发送数分列。回环 0 次重试，云端最多 2 次重试（共 3 次发送）。
- **两级预算**：单任务 `dispatch_cap`（`budget-control-v1`）＋整列 `ColumnBudget(max_requests, wall_seconds)`；跨案例/框架/重试共享，失败发送也占额，过期 0 派发。
- **边界**：列 deadline 只保证客户端合作式异步取消，**不是进程强杀，也不撤销远端已收到的请求或已发生的计费**。
- **响应保护**：`chat-response-v1` 在把响应交给厂商 SDK **之前**先落 HTTP 证据，再校验单 choice、assistant 角色、终态（stop/tool_calls）、工具调用 ID 唯一性与参数 JSON 对象（拒绝重复键、`NaN`、`1e999`）；任一无效整批不交给 SDK 执行，标 partial/UNKNOWN。仅支持单 choice 文本/function 调用形状，**不是通用协议验证器**。

### 2.7 评分与判定

| 项 | 现状 |
|---|---|
| 默认评分 | **仍是 legacy**；`calculator-own-record-v1` 必须显式 `--verifier` 选择，只覆盖限定 Own Harness 计算器录制 |
| 统一入口（CAL-002） | 显式选择新判据时，单条记录 / summary / 两个保存文件 / CLI 退出码使用同一主结果；不支持时保留 UNKNOWN，**不回退旧 PASS** |
| 四维结果 | `task_outcome / evidence / safety / faithfulness` 分开表示；有字段不等于每维都有通用语义判据 |
| 安全优先 | 已确定的安全/预算违规 FAIL 不被「任务证据损坏」或「正确答案」覆盖；缺证据保留 UNKNOWN |
| 工具计数 | host 入口累计**实际执行数**（含只读、主体内失败/拒绝）；额度拦截单独记 `blocked_tool_calls`，不冒充执行 |

### 2.8 比较门禁与诊断

- `comparison_gate` 检查模型 digest、场景、配置、协议、证据来源、`response_contract` 与执行规则一致性；不同模型**不能直接做框架公平排名**，缺证据保留不可比。
- `first_divergence` 先比较事件再比较终态（CAL-008），并对照实际执行数/拦截数（CAL-009）；输出的是**线索或假设**，不自动认定根因。
- `diagnose` / `review` 只产出只读候选与人工审核状态；`APPROVED_FOR_EXTERNAL_IMPLEMENTATION` 也不触发任何自动实施。

### 2.9 权限边界

模型只有 calculator、todo 系列等受限工具；**没有文件写入、Shell、Git、代码编辑或补丁执行工具**；诊断器与模拟器不接收工具。
默认回环 Ollama、禁用环境代理、不读云端凭据；云端模式必须显式设置 4 个环境变量，凭据只在进程内存读取，
`describe()` 只输出 `api_key_present` 布尔值，不进配置/报告/日志。验证器**无法防御同权限恶意进程篡改原始记录**。

---

## 3. 审查方式与审查结果

### 3.1 审查方式（分层，逐层有不同的证据强度）

| 层 | 做法 | 证明什么 / 不证明什么 |
|---|---|---|
| **L1 离线机制测试** | `.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -q`；真实 SDK/provider 调用链 + `httpx.MockTransport` 替身，禁用旧 urllib 网络 | 证明代码机制；**不证明**自然语言效果或在线厂商兼容性。SKIP 不算通过 |
| **L2 反例驱动（RED→GREEN）** | 先固定反例与预期，跑出失败证据并保留，再实现，再用同输入复验。例：GUARD-015 初始 29 失败/3 错误、TERM-013 旧实现 8 断言失败 | 证明「原来确实错、现在确实修」；失败证据不删除、不改写 |
| **L3 独立复现** | `reports/independent-20261110/independent_checks.py`：**不复用仓库测试用例**，直接构造 42 个反例（A 计算器验收、B 统一入口、C 计数、D 额度、E 正常对照、F 损坏轨迹、G 比较门禁、H 事件差异、I 计数差异）；冻结输入与源码 SHA-256 | 排除「自己写测试自己验」；`E2` 专门验证「测试器判得更准 ≠ Agent 能力提升」 |
| **L4 只读重评与内存对照** | `verify-record` 对历史真实录制只读重评；`accept001_recheck.py` 用深复制在内存构造 4 组反例，原文件 SHA-256 前后不变 | 新判据不回写历史；原始证据保持原样 |
| **L5 源码/差异/凭据自审** | 执行前文本快照逐行比对（仓库无 HEAD、文件全未跟踪，`git diff --check` 仅作辅助）；`ast.parse` 语法检查；测试使用假端点/假 key；检查无新增凭据日志字段 | 限定范围的改动核对；Type/Lint 未配置，标 NOT RUN |
| **L6 工程日志三册** | `DECISION_LOG`（设计理由/替代方案/Trade-off）、`IMPLEMENTATION_LOG`（改动清单/未完成/副作用）、`VALIDATION_LOG`（命令/环境/结果/失败证据/风险），按唯一 Task ID 追加，不改写历史 | 可追溯性；区分 PASS / FAIL / SKIP / NOT RUN |
| **L7 独立验收方** | 生产侧由 ChatGPT 只读复核 Git/源码、独立跑 18 项系统测试、复现 BUG-001，给出 `ACCEPTANCE=PARTIAL`；bench 侧约定「用户另叫 Agent 测试」，当前助手不冒充 | 执行方不能自证最终验收 |

### 3.2 审查结果

**Bench 侧**

| 项目 | 结果 | 来源 |
|---|---|---|
| 独立复现 42 项 | **42 PASS / 0 FAIL**；5 项优先行为及 CAL-006～009 正反对照在端点改造后仍全部成立 | [历史记录] `reports/independent-20261110/independent_results.json`、`reports/handoff-rerun-20261110/RESULT.md` |
| 离线全量演进 | 224 → 257 → 262 → 276 → 287 → 299 → 306 → **320** 项；每轮 12 SKIP、0 FAIL/ERROR | [历史记录] 工程日志 / `TEST-HANDOFF-20261110.md` |
| 最近一次历史全量（GUARD-015） | 320 项：308 PASS / 12 SKIP / 0 FAIL·ERROR，29.680s | [历史记录] `VALIDATION_LOG.md` |
| **本次复跑全量** | **320 tests in 61.448s，OK (skipped=12)** → 308 PASS / 12 SKIP / 0 FAIL·ERROR；Pydantic 弃用警告保留 | **[本次实跑]** 2026-11-12，`D:\agent-framework-bench` |
| 只读重评 / 四组内存对照 | 退出 0、PASS；4 组分别 PASS / FAIL / UNKNOWN / FAIL，原录制 SHA-256 `6fd1bb62…` 未变 | [历史记录] `RESULT.md` |
| 一次如实记录的插曲 | `accept001_recheck.py` 首次运行退出 1（复验脚本自行加严了文档未规定的预期），原始输出保留，修正脚本后重跑通过；**不是**修改产品验收标准 | [历史记录] `accept001_recheck-first-run.txt` |
| 尚未完成 | **独立 Agent 审查 NOT RUN**、真实模型有界复测此前 NOT RUN（本次补了 Gemini 3 案例）、生产缺口确认与迁入 NOT RUN、语义 Judge 未实现 | [历史记录] + [本次实跑] |

> 本次 61.448s 与历史 29.680s 是同一套 320 项测试在不同机器负载下的两次执行，**结果一致**，不合并成一次成功率，也不拿耗时差异做性能结论。

**生产侧（11-09 全系统验证）**

| 项目 | 结果 | 来源 |
|---|---|---|
| 离线基线 | 1740 passed / 0 failed / 0 skipped / 4 deselected，170.88s | [历史记录] `reports/system-validation-20261109/FINAL-REPORT.md` |
| 加入 18 项新增后 | 1758 passed / 0 failed，172.02s；故障注入 F01–F12 12/12；硬安全门 0 违规 | 同上 |
| **独立验收复核范围** | **只独立复跑 18 项新增系统测试**（18 passed, 13.21s），**完整 1758 项未独立重跑** | [历史记录] `ACCEPTANCE-REVIEW.md` |
| 真实模型任务 | qwen3.5:4b 完整链路 **8/13**（39 次生成，全部 `127.0.0.1:11434`）；且 13 含重复尝试，不能当 13 个互异任务 | 同上 + `ERRATA.md` E4 |
| 验收结论 | **ACCEPTANCE=PARTIAL，不是 PASS**；三个 BUG 未修，生产源码零修改 | `ACCEPTANCE-REVIEW.md` |
| MFAB P0 | 14 项离线契约测试全过、云/本机模型请求 **0**、48+24 计划单元 NOT_RUN，状态 `WAITING_FOR_PROVIDER_SPEC` | [历史记录] `reports/model-framework-ab-20261109/FINAL-REPORT.md` |

### 3.3 审查明确指出的遗留问题

1. **授权证据等级**：SYSVAL 的 39 次生成超出任务书 36 次硬上限；用户口头授权不是可独立核验的书面凭据，执行方接受门禁意见（E3）。后续一律先给 `max_requests` / `max_spend_usd`。
2. **归因过强被纠正**：BUG-003 的「工具数量是主因」被独立验收证伪（真实工具面是 **17 个**不是 10 个，且单工具对照同时改了消息结构/System Prompt/状态投影），降级为「现象确证、归因待消融」（E1/E2）。
3. **BUG-002 不得单独对模型定责**：缺模型原始生成文本，服务端解析器、收尾 JSON 设计、Adapter/Protocol 均未排除（E5）。
4. **旧真实模型报告的请求统计不可信**：RETRY-010 前重试只记一次、中间 429 丢失，历史「309 次请求」不等于真实 HTTP 发送数；「日志没看到 429」不能证明无限流；PASS 增多可能与重试策略改变有关。旧报告保留，缺失证据不补造。
5. **[本次实跑新发现] 预算耗尽被判 FAIL**：见 §5.5。这是评测判据的新反例，不是模型能力问题。

---

## 4. 多框架 × 多模型运行情况对比

### 4.1 三轮实验概览

| 轮次 | 模型列 | 案例 | 运行 | 判定分布 | 状态 | 请求 | 耗时 |
|---|---|---|---|---|---|---|---|
| **11-10 第一版**（端点改造前） | qwen3.5:4b（本地） | 35/35 | 105 | PASS 75 / FAIL 20 / UNKNOWN 10 | COMPLETED | — | 2381.22s |
| | gpt-oss-120b（Groq，经本地代理） | 35/35 | 140 | PASS 42 / FAIL 38 / UNKNOWN 60 | **受 429 限流污染** | 代理转发 307 / 上游错误 143 | ~7 min |
| | Gemini 3.8 Flash | 0/35 | 0 | — | **BLOCKED**（持续 503） | — | — |
| **11-10 补跑**（云端直连 + 退避，4 框架） | qwen3.5:4b | 27/35 | 108 | PASS 74 / FAIL 23 / UNKNOWN 11 | TRUNCATED_BY_LIMIT（75 min 墙钟） | 305 | 4528.09s |
| | gpt-oss-120b | 35/35 | 140 | PASS 84 / FAIL 11 / UNKNOWN 45 | COMPLETED | 309（旧口径，见 §4.4） | 909.6s |
| | gemini-3.6-flash（替代） | 1/35 | 4 | PASS 2 / UNKNOWN 2 | QUOTA_EXHAUSTED / PARTIAL | 6 | 23.01s |
| **11-12 本次补跑**（current 代码 `chat-response-v1`） | gemini-3.6-flash（替代） | 3/35 | 12 | PASS 5 / FAIL 3 / UNKNOWN 4 / UNSUPPORTED 3 | **TRUNCATED_BY_LIMIT** | 18 | 82.21s |

### 4.2 逐框架明细（PASS / FAIL / UNKNOWN）

| 模型列 · 轮次 | own_harness | openai_agents | pydantic_ai | pi_ai |
|---|---|---|---|---|
| qwen · 11-10 第一版（3 框架） | 23 / 6 / 6 | 25 / 8 / 2 | 27 / 6 / 2 | 未纳入 |
| qwen · 11-10 补跑（27 案例） | 17 / 5 / 5 | 19 / 6 / 2 | 20 / 5 / 2 | 18 / 7 / 2 |
| Groq · 11-10 第一版（限流污染） | 10 / 12 / 13 | 9 / 18 / 8 | 14 / 7 / 14 | 9 / 1 / 25 |
| Groq · 11-10 补跑（退避后） | UNKNOWN 35（UNSUPPORTED） | 26 / 5 / 4 | 33 / 0 / 2 | 25 / 6 / 4 |
| Gemini · 11-10 烟测（1 案例） | UNKNOWN 1（UNSUPPORTED） | 1 / 0 / 0 | 1 / 0 / 0 | 0 / 0 / 1 |
| **Gemini · 11-12 本次（3 案例）** | 3 × UNSUPPORTED | 2 / 1 / 0 | 1 / 1 / 1 | 2 / 1 / 0 |

### 4.3 可比性与「为什么不能排名」

- **同列内**：`comparison_gate` 通过才可比。Groq 补跑列 25/35 批次可比；qwen 补跑列 17/27；Gemini 11-10 烟测 0/1；**本次补跑 1/3**（`calculator_only` 可比，另两案例因含 UNKNOWN 被正确拒绝）。
- **跨列**：`model_digest` 必然不同，门禁一律拒绝排名，**三列只并列展示**。
- 云端 `model_digest` 是「端点 + 模型名」的稳定哈希，**不是权重身份证明**；本地列是 Ollama digest。
- `own_harness` 在云端直连模式全部 UNSUPPORTED，不消耗请求、不产生伪装成绩；因此云端列实际是 3 个框架在比。
- 场景含故意注入的故障，FAIL/UNKNOWN 不等于系统回归；**没有语义质量 Judge，不做「哪个模型/框架更强」的结论**。

### 4.4 已知口径偏差与污染（必须与数字一起读）

1. **Groq 第一版作废为能力对比**：7 分钟 307 次转发触发免费层 TPM 8000 限流，逐条 `RateLimitError 24 / ModelRateLimitError 5 / HTTP 429 23 / model_call_failed 24`，只证明链路可用。
2. **Groq 补跑的「309 请求」是旧计量口径**：RETRY-010 之前重试不计真实发送、中间 429 不保留，不能当作真实网络发送总数。
3. **云端模式剥离字段**：`reasoning_effort` / `parallel_tool_calls` / `strict` / `max_completion_tokens` 被统一归一化；`num_ctx=32768` 对云端只是名义值。
4. **Gemini 用替代模型**：用户指定 `gemini-3.8-flash` 持续 503（本次探测仍 ReadTimeout），实际用 `gemini-3.6-flash`，**不与指定模型混称**；思考模型要求回传 `thought_signature`。
5. **`pi_ai` 成熟度不同**：第一版按 turn 重置会话、无原生预算与重试，大量 UNKNOWN 是证据不足，不是「框架能力差」；PI-012 / HISTORY-014 / GUARD-015 之后才补齐共享传输、任务内历史与响应保护。
6. **新旧不可混算**：本次补跑使用 `chat-response-v1` 响应合同、`retries=2` 与 `recorded-v4` 实验适配器，与 11-10 历史数据**执行条件不同**，不得合并计算「提升」。

### 4.5 对比结论

**能确认的**：四框架链路在三种模型端点上都能接通；`calculator_only` 在本次 Gemini 补跑中三 SDK 全部 PASS 且门禁 `comparable=true`（历史烟测该案例不可比、pi_ai 为 UNKNOWN）。
**不能确认的**：任何模型/框架的能力排名、真实任务成功率、语义质量、长上下文与在线厂商通用兼容性。

---

## 5. 本次 Gemini 补齐（实际执行记录）

### 5.1 自设有界边界（超出即停）

| 项 | 设定 |
|---|---|
| 授权来源 | 用户「可以先把 gemini 的模型补上再总结」；按项目规则外部调用须有界，故自行设定**保守上限**，不扩大 |
| 模型 | `gemini-3.6-flash`（可用替代）；用户指定的 `gemini-3.8-flash` 单独探测，不混称 |
| 硬上限 | **18 次真实发送 / 3 个案例 / 420 秒墙钟 / 1 秒案例间隔**；`ColumnBudget` 强制，超即 `TRUNCATED_BY_LIMIT`、退出码 2 |
| 费用 | 免费层，**0 费用**；没有升级计费、没有换 key |
| 数据 | 仅冻结场景的合成 owner/session；密钥只在进程内存读取，不进报告/日志；输出新目录 `gemini-fill-20261112/`，**不覆盖历史** |
| 明确不做 | 不跑 35 场景全量、不做跨模型排名、不改生产源码、不回写历史 manifest、不提交推送 |

### 5.2 预检（发请求前，0 次模型调用）

- `resolve()` 返回 `openai_compatible` / `proxy_configured=true` / `api_key_present=true`；
- `own_harness.available()` = UNSUPPORTED（预期），`openai_agents` / `pydantic_ai` / `pi_ai` 均 `available=True`；
- 探测：**[本次实跑]** `gemini-3.8-flash` → `ReadTimeout`（仍不可用）；`gemini-3.6-flash` → **HTTP 200**，说明免费层配额已重置。

### 5.3 补跑结果 **[本次实跑]**

命令（工作目录 `D:\agent-framework-bench\reports\full-model-rerun-20261110`）：

```
..\..\.venv\Scripts\python.exe -B -X utf8 run_model_column.py --column gemini \
  --max-cases 3 --max-requests 18 --wall-limit 420 --interval 1 --tag gemini-fill-20261112
```

```
[gemini] calculator_only : own_harness=UNKNOWN, openai_agents=PASS, pydantic_ai=PASS, pi_ai=PASS
[gemini] todo_add_clarify: own_harness=UNKNOWN, openai_agents=PASS, pydantic_ai=UNKNOWN, pi_ai=PASS
[gemini] todo_add_update : own_harness=UNKNOWN, openai_agents=FAIL, pydantic_ai=FAIL, pi_ai=FAIL
DONE gemini status=TRUNCATED_BY_LIMIT {runs:12, pass:5, fail:3, unknown:4, unsupported:3}
     requests=18 elapsed=82.21s   EXIT=2
```

manifest：`reports/full-model-rerun-20261110/gemini-fill-20261112/manifest-gemini.json`
（`stop_reason=request_limit`、`selected_cases=3`、`available_cases=35`、`errors=[]`）。

### 5.4 与历史烟测的对照（有价值的新证据）

| 案例 `calculator_only` | 11-10 烟测（旧合同） | **11-12 本次（`chat-response-v1` / recorded-v4）** |
|---|---|---|
| openai_agents | PASS | PASS |
| pydantic_ai | PASS | PASS |
| pi_ai | **UNKNOWN**（400：思考模型要求回传 `thought_signature`） | **PASS**，`final_message = "计算结果为 703。"` |
| `gate.comparable` | false（pi_ai 含 UNKNOWN） | **true** |

解读要保守：这说明 **PI-012 之后的共享传输 + 签名透传 + 响应保护让 pi_ai 在 Gemini 上跑通了链路**，
但执行条件已变（`response_contract`、`retries=2`、适配器版本），**不能**解释为「模型变强」或「框架能力提升」，也不能与旧数据混算。

### 5.5 [新反例] 预算耗尽被判 FAIL，会误导读者

逐案例记录显示，`todo_add_update` 的 3 个 FAIL **不是模型或框架失败**：

| 框架 | `termination.reason` | `dispatched` | `blocked_before_dispatch` | `model_calls` | 耗时 |
|---|---|---|---|---|---|
| openai_agents | `dispatch_budget` | 2 | 1 | 2 | 4.52s |
| pydantic_ai | `dispatch_budget` | **0** | 1 | **0** | **0.03s** |
| pi_ai | `dispatch_budget` | **0** | 1 | **0** | **0.01s** |

列额度 18 次在第 3 案例中途用尽，后两个框架**一次请求都没发出**，却被 legacy 评分记为 `FAIL`。
这正是「执行未运行」被当成「执行失败」的假象，与 CAL-001 修掉的「假 PASS」属于同一类可信度问题，方向相反。
**建议在 bench 登记新整改项**：列预算/异步截止导致的未派发应标 `NOT_RUN` 或 `UNKNOWN`，不得计为业务 FAIL，
且 `manifest.not_run` 应覆盖「已选但未派发的框架位」（本次 `--max-cases 3` 下 `not_run=[]` 只反映 3 个已选案例，不反映 35 个总场景）。
**这条属于评测系统的待修项，不迁入生产**（生产 `evaluation/verifier.py` 的 `auto.execution` 已把「执行未正常结束」保留为 UNKNOWN）。

### 5.6 为什么没能补齐 35 场景

- 免费层硬配额 **20 请求/天**（`gemini-quota-429.txt` 一手证据：`generate_content_free_tier_requests limit: 20`）。本次探测 2 次 + 驱动 18 次 ≈ 已用尽当日额度。
- 按云端 3 个可用框架 × 每案例约 2 次请求估算，35 场景需 **210+ 次请求**（历史 Groq 35 场景 140 运行用了 309 次请求），即免费层需十余天，或升级计费计划。
- 用户指定的 `gemini-3.8-flash` 本次探测仍不可用，不能声称指定模型已验收。
- 因此 Gemini 列状态从「1/35 烟测」更新为「**3/35 有界补跑 + 明确 PARTIAL**」，**不填模拟值、不冒充全量、不做跨版本合并**。

**补齐该列的可执行方案**：升级 Gemini 计费或换有配额的 key → 确认 3.8 Flash 服务端恢复 → 固定 `model_digest` / `response_contract` / `retries` / 适配器版本 → 用同一有界驱动按 35 场景重跑，输出新 tag → 再与 Groq / qwen 列并列展示（仍不排名）。

---

## 6. 此轮连续修复回顾（全部在 Benchmark）

| 阶段 | Task ID | 修了什么 | 关键区别 |
|---|---|---|---|
| 二 | CAL-001～004 | 计算器窄验收、统一评分入口、host 真实执行计数、计数接入评分 | 让「错答案 999」「没调用工具却声称成功」「执行 3 次/额度 2 次」不再 PASS |
| 二 | **CAL-005** | 工具**调用前**原子预占额度并单独记拦截数 | **CAL-004 是事后查出超支，CAL-005 才是行动前阻止超支** |
| 三 | CAL-006～009 | 损坏轨迹不掩盖已确定安全 FAIL、证据来源一致性、事件先于终态比较、只读/拦截计数诊断 | 让结果**不容易误导人**，不提高模型推理能力 |
| 三 | ACCEPT-001 | 全量 276 项 + 4 组内存反例对照 | 离线复验；独立审查与真实模型仍 NOT RUN |
| 四 | 审核发现 | 旧录制层重试漏计真实发送、429 中间响应丢失 | 历史「309 请求」「没有 429」不可作为能力或成本结论 |
| 五 | RETRY-010 / COLUMN-011 / PI-012 | 逐次真实发送计量 + 请求 ID；整列共享额度与异步截止；实验 pi_ai 接回统一传输 | 先关闭未受保护的 pi_ai 云端入口，接线完成后才恢复 |
| 六 | TERM-013 / HISTORY-014 / GUARD-015 | 截断误完成、任务内历史丢失、坏参数被 SDK 转 `{}` 后执行；三适配器统一在 SDK 转换前校验 | 最后全量 320 项；`response_contract` 新旧不可混用比较 |

这些修改**没有把旧报告改成新成绩**，也没有自动推广到所有任务；默认评分仍是 legacy。

---

## 7. 生产 Agent 现状与全量测试

### 7.1 已实现与边界（摘要，详见用户梳理的 20 项）

一次任务链路：用户输入 → 组装上下文/记忆/Skill → 模型决策 → Runtime 检查权限与预算 → 执行工具 → 保存真实结果与轨迹 → 模型继续或交付 → 在受支持场景中独立验收。

已具备：网页工作台与 Plan/Build、提示词、DeepSeek/OpenAI 协议/Ollama 三种模型入口、自写 Runtime、工具注册与 Schema、Skill 按需读取、上下文压缩、SQLite 持久化与恢复、长期 Memory（FTS/BM25，**非 embedding 语义检索**）、owner/session 隔离与授权、调用前次数预算、检查点与保守恢复、Research 子循环、结构化 Trace、单个 `TODO_COUNT` 目标（默认关闭）、PASS/FAIL/UNKNOWN 验证器、夜间调度、受限持续改进。

**明确没有**：统一 Token/费用/总时长硬预算、语义检索、通用自主纠错闭环、多 Agent 委派、任意插件市场、OS 级代码沙箱、
任意远端副作用「恰好执行一次」的保证、语义质量 Judge。Activity Connector 只读链路已建成，但**未正式接入 Memory**。

### 7.2 11-09 全系统验证的三个真实缺陷（生产更该修的东西）

| ID | 现象 | 影响 | 独立复核 |
|---|---|---|---|
| **BUG-001**（中） | calculator 非法输入（除零/语法）以 `ValueError` 穿透注册表 → `runtime.py:386` 宽泛 except → `tool_execution_failed`、`execution_state=unknown`、整轮失败 | 用户得到空白回复，模型没有纠错机会 | **已由独立验收方复现**（`bug001-repro.py`） |
| **BUG-002**（高） | Research 收尾要求一次性输出嵌套 `Evidence` 结构，Ollama 解析器报 XML 错误 → HTTP 500 | 研究能力在默认模型下完全不可用 | 现象确证；**根因 UNKNOWN/MIXED**，缺模型原始输出，不能单独对模型定责 |
| **BUG-003**（高，系统级） | 完整工具面（**实际 17 个**）下模型不再发出 `propose_goal`，自然语言建待办不可达 | 核心待办链路不可用 | 现象确证；「工具数量是主因」的旧结论已被证伪，待 B003 单因素消融 |

### 7.3 本次对生产源码的核对结果（迁入判定的硬依据）

| 检查点 | 生产实际代码 | 结论 |
|---|---|---|
| 长度截断 | `llm/openai_protocol.py::parse_response`：`incomplete` → `finish_reason=length`；`llm/ollama.py::parse_chat_response`：`done_reason=length` 时**禁止执行工具**；`runtime.py:249` → `partial/output_limit` | **等价于 TERM-013，且更贴合生产协议** |
| 参数 JSON 防护 | `openai_protocol.py::load_json` 用 `object_pairs_hook=unique` + `parse_constant=invalid_constant`，**已拒绝重复键 / NaN / 1e999**；`ollama.py::_arguments` 要求解析结果为对象 | **等价于 GUARD-015 的参数部分** |
| 调用前次数预算 | `runtime.py:331-333` 工具执行前 `budget.consume("tool_calls")`；`runtime.py:217` 模型调用前 `consume("model_calls")`；`budget.py` 文档「调用前扣除，不估算 token 或费用」 | **等价于 CAL-005** |
| 逐次发送/重试 | `llm/client.py::generate` 一次逻辑调用一次 `_requests` 递增，**生产没有自动重试**（`_retry_after_seconds` 仅用于错误诊断） | **不存在 RETRY-010 的漏计缺口**；但也缺原始响应/请求 ID/状态链的持久证据 |
| 任务内多轮历史 | `runtime.py` 本来就在 `messages` 中累积 assistant / tool 消息 | **等价于 HISTORY-014** |
| Token 计量 | `client.py` 已把 `input_tokens / output_tokens / total_tokens / cached_input_tokens` 记入 `self._usage` | **有数据、无硬预算** → 明确的真实缺口 |
| 验证器 | `evaluation/verifier.py` 已有 PASS/FAIL/UNKNOWN、`hard_safety` 优先、`auto.false_success`、缺证据保留 UNKNOWN | **与 CAL-001～009 理念一致，独立实现** |

---

## 8. 哪些代码可以并入生产（核心判定）

### 8.1 判定原则（沿用 `TEST-HANDOFF-20261110.md`）

1. 先在**当前生产源码**核实同类缺口；生产已有预算控制，不能因 bench 新增拦截就断言生产也缺失。
2. 区分「可迁移的执行行为改进」与「bench 专用测量修复」；**评分器修复一般留在评测系统**。
3. 只证明规则拦截有效时，**不声称模型能力提高**；离线机制测试通过不能证明自然语言效果。
4. 迁入后必须用同一反例 + 相关回归验证**生产真实入口**；未验证项写清楚。

### 8.2 逐项判定表

| Task ID | 改动性质 | 生产是否有同类缺口 | 判定 |
|---|---|---|---|
| CAL-001 计算器验收 / CAL-002 统一入口 / CAL-004 计数入评分 | 评分器、Runner、CLI 接线 | 生产 `evaluation/verifier.py` 有自己的四态与 `auto.false_success`，不共用 bench 记录格式 | **不迁入**（评测专用） |
| CAL-003 真实工具计数 | host 入口计数 | 生产 `runtime.py` 有 tool span / trace，口径不同 | **不迁入** |
| **CAL-005 调用前拦截** | 工具执行前原子预占 | **已有** `budget.consume("tool_calls")` 在工具执行前 | **不迁入**（重复实现会造成两套口径） |
| CAL-006 损坏轨迹不掩盖安全 FAIL / CAL-007 证据来源 / CAL-008 事件比较 / CAL-009 计数诊断 / ACCEPT-001 | 评分证据、比较门禁、诊断、测试 | 评测专用；生产 verifier 的 `hard_safety` 已体现同一理念 | **不迁入** |
| **RETRY-010 逐次发送计量** | 传输/预算/证据 | 生产**无自动重试**，不存在漏计；但缺原始请求/响应状态链 | **代码不迁入**；「逐次落证」思路值得借鉴（见 §8.3-①） |
| **COLUMN-011 列级共享额度 + 异步截止** | 实验批量驱动 | 生产 `evaluation` 有持久预算与夜间止损，但**没有跨任务整批发送额度/合作式取消** | **暂缓评估**（见 §8.3-③） |
| PI-012 pi_ai 共享传输 | 实验适配器 | 生产不使用 pi_ai | **不迁入** |
| **TERM-013 终态保护** | 适配器 | **已有**：`parse_response` / `parse_chat_response` / `runtime.py:249` | **不迁入** |
| **HISTORY-014 任务内历史** | 实验适配器 | **已有**：`runtime.py` 累积 messages | **不迁入** |
| **GUARD-015 原始响应统一校验** | 传输层响应/参数校验 | **已有等价**：`load_json` 拒重复键/非有限数、`_arguments` 要求对象、`parse_chat_response` 校验角色与形状 | **代码不迁入**；「先落证再解析」思路值得借鉴（见 §8.3-①） |
| `model_endpoint.py` 端点切换 | bench 配置 | 生产 `llm/config.py` 与 `OpenAISettings` 自有 Provider 配置与凭据管理 | **不迁入** |

### 8.3 明确清单：0 个成品文件，2 项（+1 项暂缓）可重写迁入

> **严格意义上的答案：没有任何 bench 文件可以直接 copy 进生产。** 下面三项是「机制与验收契约」，需按生产接口重新实现。

**① 原始请求/响应证据链（建议优先，价值最高）**
- **做法**：在 `llm/client.py::generate` 中，先写一条脱敏的结构化证据（request_id、HTTP 状态、模型中止原因、工具调用数量、usage、可选响应体 hash），再交给 `load_json` / `parse_response`；异常路径也要留证。
- **为什么**：BUG-002 卡在「没有模型原始输出」，导致无法区分模型、解析器、收尾 JSON 设计与 Adapter 责任；这是修该 BUG 的前提，也让 Trace 可回溯。
- **硬性约束**：**绝不写 Authorization / 密钥 / 完整正文**；白名单字段 + 大小上限 + 可回读；不扩大现有日志权限。
- **验收**：固定脚本响应（正常 / 500 / 截断 / 畸形 JSON）各留一条证据且不含凭据；同输入重放 hash 一致；现有 1758 项相关回归通过；明确「这只是证据，不是语义判据」。

**② 统一硬预算扩展到 Token / 费用维度（建议其次）**
- **做法**：`budget.py` 的 `BudgetLimits` 增加 token/费用资源维度；`client.py` 已记录的 `_usage` 在**下一个动作发起前**核对并预占，超限抛 `BudgetExceeded` / `LLMError`。
- **为什么**：这是用户梳理中明确列出的缺口（「普通 Runtime 尚未形成统一 Token、费用、总时长硬预算」），且生产已有 usage 数据基础，不依赖 bench 代码。
- **验收**：脚本模型返回已知 usage 时超限必在下一个动作前阻断；不改现有 `model_calls` / `tool_calls` 语义；夜间实验的局部费用账本不冒充全系统已闭合；未验证 Provider 的精确计量标 NOT VERIFIED。
- **注意**：不要照搬 bench 的 `ColumnBudget`（它绑的是 SDK transport 与实验列），生产应挂在自己的持久预算树上。

**③ 跨任务整批共享额度 + 合作式异步截止（暂缓）**
- 仅当夜间批量/长任务出现「单任务都合规、整批失控」的真实反例时再做；生产已有持久预算与夜间止损，先复现缺口再设计。同上，不保证远端取消或进程强杀。

### 8.4 迁入的前置条件（缺一不做）

1. 由**独立测试方**复验 bench 关键反例（42 项独立复现 + 本次新发现的「预算耗尽误判 FAIL」）；
2. 在生产**真实入口**复现对应缺口，保留改前失败证据；
3. 固定输入、判据、模型与预算，做同条件前后对照，分别说明安全、任务完成与成本；
4. 最小范围改动 + 相关回归，未验证项标 NOT RUN；
5. 不自动启用功能、不自动发布；提交与推送只在用户明确授权后执行。

### 8.5 优先级更高的替代动作（强烈建议先做）

与其搬 bench 代码，不如用已经校准好的评测能力，做**一个小而完整的真实任务对照**：

1. 修 **BUG-001**（最小）：在 `tools/registry.py` 把 handler 的 `ValueError` 归一为结构化 `invalid_arguments` 工具结果，让模型获得纠错机会；保留 `execution_state` 语义与「未知写入不盲重试」的底线。
2. 用 ① 的证据链复现 **BUG-002**，拿到模型原始输出后再做 B002 单因素消融（Text-only Summary / 嵌套 Evidence / 分步 Citation），不要凭日志猜根因。
3. 按 MFAB 任务书 B003 做 **BUG-003** 单因素消融（冻结 System Prompt / 状态投影 / Tool Description，只变 N=1/5/10/17 工具数，每格 ≥3 次）。
4. 每修一个，用同一冻结输入跑「改前失败 → 改后结果 → 必要负面对照」，再决定是否值得扩大。

这能把「测得更准确」推进到「生产 Agent 确实完成得更好」，而不是继续补一长串通用架构模块。

### 8.6 结论对照表

| 方面 | 现在可以确认 | 还不能确认 |
|---|---|---|
| 生产架构 | 核心组成已有较完整实现，支持限定个人助手场景 | 「所有优秀 Agent 能力都齐了」 |
| 评分可信度 | 一批已复现的假 PASS、证据降级、比较漏洞已修复 | 所有任务、所有旧评分都可靠 |
| 执行保护 | bench 调用前拦截 + 云端发送/重试/列预算已接通 | 全系统统一费用硬门、任意进程与远端取消保证 |
| 适配器正确性 | 截断、坏参数、历史丢失等具体反例已修复 | 任意厂商协议与所有在线模型兼容 |
| 测试 | 本次 320 项 / 308 PASS / 12 SKIP；生产 1758 项（独立方只复跑 18 项） | 生产全量当前已独立通过、真实任务成功率 |
| **实际能力收益** | 评测失真减少，测量基础更可信 | **生产 Agent 已明显变强**，或某框架优于另一框架 |
| **交付边界** | 修改、理由、结果、迁入条件均有记录 | 已完成独立审查、生产迁入或发布 |

---

## 9. 未完成、风险与下一步

**未完成（明确标 PARTIAL / NOT RUN）**
- 独立 Agent 审查（项目约定由用户另叫 Agent，本助手不冒充）；
- Gemini 35 场景全量列（免费层 20 请求/天 + 3.8 Flash 不可用），本次只到 3/35；
- 生产缺口确认、最小迁入、迁入后回归与发布；
- `own_harness` 云端原生入口、有界驱动的本地 qwen 列统一保护；
- 语义质量 Judge、模拟用户代表性、全前缀缓存与所有 Provider 精确计量。

**风险**
- 本次新发现：列预算耗尽后未派发的框架位被判 `FAIL`（§5.5），若直接汇总会误读为模型失败；
- 新旧 `response_contract` / 适配器版本混算会产生虚假「提升」；
- 历史 309 等请求统计不可还原，缺失证据不得补造；
- 生产 `8/13` 的分母含重复尝试，不能据此给成功率区间；完整 1758 项未经独立方重跑。

**下一步（按顺序）**
1. 在 bench 登记 §5.5 的新整改项并做 RED→GREEN（未派发标 NOT_RUN/UNKNOWN）；
2. 由独立测试方复验关键反例与本次 Gemini 补跑证据；
3. 选**一类生产真实任务**，固定输入/判据/模型/预算，观察它究竟失败在哪里；
4. 只在生产确有对应缺口时做最小修复（优先 §8.5 的三个 BUG 与 §8.3 的两项机制），跑同条件前后对照；
5. 需要补齐 Gemini 全量时，先解决配额与 3.8 Flash 可用性，再按 §5.6 方案重跑。

---

## 10. 证据索引

| 内容 | 路径 |
|---|---|
| 本报告 | `C:\Users\陈独秀\Documents\agent-harness\docs\2026-11-12-benchmark-review-and-migration-assessment.md` |
| 评测系统详细说明 | `D:\agent-framework-bench\README.md` |
| 测试交接与迁入约定 | `D:\agent-framework-bench\docs\TEST-HANDOFF-20261110.md` |
| 评测当前状态 | `D:\agent-framework-bench\docs\PROJECT_STATUS.md` |
| 模型端点改造与配额证据 | `D:\agent-framework-bench\docs\MODEL-ENDPOINT-20261110.md`、`reports\full-model-rerun-20261110\gemini-quota-429.txt` |
| 校准记录 | `docs\CALIBRATION-20261109.md`、`docs\CALIBRATION-INTEGRATION-20261109.md` |
| 工程日志三册 | `D:\agent-framework-bench\docs\engineering-log\{DECISION_LOG,IMPLEMENTATION_LOG,VALIDATION_LOG}.md` |
| 11-10 第一版全量报告 | `D:\agent-framework-bench\reports\full-model-20261110\REPORT.md` |
| 11-10 补跑三列对比 | `D:\agent-framework-bench\reports\full-model-rerun-20261110\COMPARISON.md` |
| **本次 Gemini 补跑 manifest** | `D:\agent-framework-bench\reports\full-model-rerun-20261110\gemini-fill-20261112\manifest-gemini.json` |
| 独立复现 42 项 | `reports\independent-20261110\independent_results.json`、`reports\handoff-rerun-20261110\RESULT.md` |
| 生产全系统验证 | `C:\Users\陈独秀\Documents\agent-harness\reports\system-validation-20261109\FINAL-REPORT.md` |
| 生产独立验收意见 / 勘误 | `reports\system-validation-20261109\{ACCEPTANCE-REVIEW,ERRATA,BUGS,LIMITS,SECURITY-REPORT}.md` |
| 生产 MFAB P0 | `reports\model-framework-ab-20261109\FINAL-REPORT.md` |
| 生产当前状态 / 整改清单 | `docs\PROJECT_STATUS.md`、`docs\IMPROVEMENT_BACKLOG.md` |
| 生产源码核对点 | `src\min_agent\runtime.py`、`llm\{client,openai_protocol,ollama}.py`、`tools\registry.py`、`budget.py`、`evaluation\verifier.py` |

**本次实际执行的命令**

```bash
# 离线全量复验（D:\agent-framework-bench）
./.venv/Scripts/python.exe -B -X utf8 -m unittest discover -s tests -q
# → Ran 320 tests in 61.448s / OK (skipped=12)

# Gemini 有界补跑（D:\agent-framework-bench\reports\full-model-rerun-20261110）
../../.venv/Scripts/python.exe -B -X utf8 run_model_column.py --column gemini \
  --max-cases 3 --max-requests 18 --wall-limit 420 --interval 1 --tag gemini-fill-20261112
# → TRUNCATED_BY_LIMIT / request_limit / 12 runs / 18 requests / 82.21s / exit 2
```

> 本次未运行生产 pytest 全量（1758 项为历史记录，独立验收方只复跑 18 项），未调用除 Gemini 之外的任何云端模型，
> 未读取或输出任何密钥值，未修改生产源码，未执行 Git commit / push / 部署。
