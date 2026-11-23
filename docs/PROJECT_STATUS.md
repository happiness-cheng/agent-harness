# Adrian：继续工作先读这里

> **交付规则（AEF-003 第 11 行 + AEF-004 第 12 行，2026-11-14 定稿）**：每次交付必须附核心文件路径、四件事代码讲解与「长度按需、但必须能独立看懂」的关键片段（带文件与行号）；**片段之后还要逐行/分块讲解参数、每步动作、为什么这样写、明确不保证什么**。详见 [AGENTS.md](../AGENTS.md) 第 11–12 行。

## 2026-11-24 GOV-001 四工具治理链落地（进行中，分支 gov-001）

用户要求把功能表逐项落地到 generic 四工具模式：执行前分级（规则→小模型→人）、命令效果分类、只读白名单、sidecar 安全 gate、执行后验证、事件触发、单层契约、expected_sha256、审计、沙箱、ask_user、上下文 sidecar。过度防御（junction/8.3/UNC 绕过、TOCTOU、FIFO、权限位、gitleaks 全历史、long-lived 容器、报错打磨）继续推迟。方案已批准，按 S1–S8 推进，每步一个本地 commit，push 需用户点头。

- **S1 完成（恢复迁出接线）**：本地 `main` 曾被重置到远端 `76bff54`（gate-v7+评测合入），上一轮迁出提交 `9c74d94` 成孤儿。本步按文件干净重放恢复 `session/paths.py`、启动守卫、文件错误分类、默认库迁出、owner 迁移脚本与测试，**不动 gate-v7 的 `generic.py`**。恢复后全量 **1828 passed / 2 skipped / 8 deselected / 150.65s**；默认库在用户数据目录、仓库无 `.env`/`data`、旧会话可读（workspace 2 / qianwen 15）。门禁/评测基线用户已本地跑通（EVAL-DIAG-009），不重复跑。详见三类日志 → GOV-001-S1。
- **S2 完成（Ollama/专用基线能力）**：`eval/real.py` 加 `OllamaEvalModel`（包装 src OllamaModel）+ `ollama_available`（查 /api/tags，缺失即 NOT RUN）；`run_real.py` 加 `--provider ollama`、`--mode generic|dedicated`；`verify(mode="dedicated")` 把 bash 门检查标 NOT_APPLICABLE，generic 判据不变。离线测试 **12 passed**（7 新 + 5 既有）。**真实基线跑按用户指示推迟**（Ollama 见 EVAL-DIAG-009）。详见三类日志 → GOV-001-S2。
- **S3 完成（执行前分级门）**：新增 `tools/effects.py`（low/medium/high 分类，复用 gate-v7 解析器）+ `tools/gate.py`（`ToolGate` 规则→小模型→人，fail-closed+熔断；`GateConfirmations` 排队）。`Runtime(tool_gate)`→ToolBatch 执行前串行分级；拒绝写回 `gate_denied` 工具消息+trace+`gate_audit`（脱敏）。rm 族从硬拒改为 high→人工确认，批准后一次性放行。web 加 `/gate-confirm`；CLI generic 默认 interactive + Ollama 审批小模型。gate-v7 默认行为不变（`allow_destructive=False`、`gate_policy=off`）。新增测试 63 项（effects 44 + gate 16 + 集成 3）；全量 **1891 passed / 2 skipped / 8 deselected / 146.72s**。eval 不重复跑（用户指示）。详见三类日志 → GOV-001-S3。
- **S4 完成（单层契约 + expected_sha256）**：`ToolRegistry(envelope="flat")` + `serialize`/`flatten_outcome`，generic 工具消息压成单层 `{ok,error_code,message,data}`，**以内层业务 ok 为准**（双层 ok 痛点已解）；execute 内部形状不变，ToolBatch/progress_guard 零改动，默认 nested 逐字节不变。write/edit 加可选 `expected_sha256`（不匹配→stale_version）。eval `_inner` 兼容两形、判据不改。generic.md 补契约说明。新增 12 项测试；全量 **1903 passed / 2 skipped / 8 deselected / 150.96s**。详见三类日志 → GOV-001-S4。
- **S5 完成（ask_user）**：generic 工具面 4→5，新增 `ask_user(question, options?)`：记 `pending_question` → `AuthorizationRequired("waiting_user")` 停批，snapshot surfaced，用户用既有 `/resume`（带 task_id）回答续跑，新一轮清 question。无副作用、不走分级。新增 3 项集成测试；全量 **1906 passed / 2 skipped / 8 deselected / 149.22s**。详见三类日志 → GOV-001-S5。
- **S6 完成（执行后验证 + 上下文 sidecar）**：bash 加可选只读 `verify`（先校验 low、主命令退出 0 后跑、`verification.passed` 如实报告不推翻主命令）；bash 重构抽出 `_execute`（无 verify 时逐字段不变）。新增 `context/sidecar.py`：generic 模式后台线程只读采集文件索引+git status，注入带 `source=sidecar` 的 workspace_context 段 + trace span，失败/超时降级省略。新增 7 项测试；全量 **1913 passed / 2 skipped / 8 deselected / 148.85s**。详见三类日志 → GOV-001-S6。
- **S7 完成（Docker 沙箱最小接入）**：新增 `tools/sandbox.py` `DockerSandbox`（spike 参数：网络关闭/只读根/降权/资源限制/每命令一容器/超时强删）；工作区 rw + `src/scripts/tests/eval/.git` 只读叠挂防改门禁代码；Docker/镜像不可用 `sandbox_unavailable` fail-closed 不回落宿主。`GenericWorkspace(backend)`、CLI `--sandbox docker --sandbox-image`。分级/禁令仍在沙箱前。新增 5 单元测试；真实容器集成 3 项 **NOT RUN**（本机 Docker 未运行）。全量 **1918 passed / 5 skipped / 8 deselected / 154.03s**。详见三类日志 → GOV-001-S7。
- **S8 未开始**：事件触发（预授权+预算+审计）。

## 2026-11-22 GENERIC-TOOLS-001 通用四工具模式（PARTIAL / 实验入口）

用户要求先把专用工具放一边，只提供 `read / write / edit / bash` 四个通用入口。本批新增**显式模式**，默认服务与既有测试路径不变；专用工具代码保留，切回默认即可恢复。

- **入口**：`python -m min_agent.web --generic-tools --workspace-root <dir>`；一键脚本 `scripts\start-pi.cmd`（千问平台，端口 8766）。未传工作区时取当前目录。
- **注册结果**：`HarnessApplication(generic_tools=True)` 的注册表恰好 4 项；默认模式仍是 28（千问）/21（Adrian）/20（裸 CLI），未改。
- **行为**：`read` 按行切片（offset/limit，最多 2000 行），复用 `FileSandbox` 的敏感路径/越界/256 KiB 拒绝；`write`/`edit` 复用它的事务写、原子替换、唯一匹配和写锁，但**不要求 expected_sha256**（pi 式覆盖）；`bash` 用 Git Bash，工作区 cwd，注入 `.venv/Scripts`，清洗 `*API_KEY/*TOKEN/*SECRET/*PASSWORD/*CREDENTIAL*` 环境变量，默认 120 秒超时（`taskkill /T` 杀子进程），stdout/stderr 各截断 64 KiB。
- **权限边界（重要）**：本模式**不经过 Plan/Build 与目标确认门**；`bash` 以当前用户权限运行，**不是沙箱**，能读工作区外文件和 `.env`。`show_state=False` 关闭 `application_state` 尾部，避免模型看到与实际能力矛盾的 `mode=plan`。未注册 `fs_delete`，`bash rm` 是硬删除。
- **验证**：新增 21 项（`tests/unit/test_generic_tools.py` 14 + `tests/integration/test_generic_mode.py` 7）全过；其中 1 项从 `HarnessApplication.submit` 走完整 worker 线程，跑通 write→read 并断言首轮系统提示词与 4 个 schema。受影响回归 **121 passed**（含文件工具、runtime 组件、tool loop、冻结签名）。全量离线 **1805 passed / 8 deselected / 142.03s**（基线 1784 + 21）。
- **未做/边界**：未用真实模型跑四工具对话；`read` 不支持 >256 KiB 分段；没有程序化工具调用（代码回调工具）；没有 Windows 沙箱；WSL 启动器被拒绝。详见 [GENERIC_TOOLS](GENERIC_TOOLS.md)。

## 2026-11-20 PR #4 审查修复（WIRING-FIX-001，待用户复审）

PR #4 外部审查为 Request changes；本分支在 `d77c5f1` 后追加修复。代码/测试提交 `e7651b0`，本日志与状态随后提交。

- **M1/M2/PENDING**：MCP confirm 记录绑定 `logical_task_id`（恢复 attempt 变化仍同逻辑任务）+ TTL 900 秒；`check_and_consume` 只消费同一逻辑任务、未过期的批准；consumed/refused 按任务隔离，新任务同签名必须重新确认；pending 上限 20、总记录 100、过期清理。
- **M3**：MCP 服务器 `isError`/`MCPError` 走 `ToolExecutionFailedError` → `ok:false`、`tool_execution_failed`、`execution_state=unknown`，不再包成成功文本。
- **M4**：已有会话在重启后第一次 `snapshot`/访问时触发 `connect_background()`；不在应用启动时批量连接未打开会话。
- **M5/M6**：删除数字统一为「33 个被删文件 5856 行（去空行 5072、空行 784；src 3208 + scripts 413 + tests 2235）」「11 个测试文件」「test_activity_* 7 个」；`runtime/loop.py` 两处 docstring 改六阶段，全仓无「五个阶段」。
- **N1–N10**：记忆正则拒绝 `记住性很好/remembering/记住：` 等误报；MCP 段追加后复核 600 token 并标注 connecting；pending 有上限/过期；拒绝原因区分错误码、`refused_by_user` 仅真拒绝为真；activity 文档/MODULE_MAP/PROJECT_STATUS 历史入口标注删除；删除 `activity` extra；AGENTS 门禁补定义/豁免/优先级/核查人；N8 归属更正；factory docstring 明确 PASS 不核对数值正确性；首轮 connecting 标注。
- **验证**：受影响测试 **75 passed**；MCP live **2 passed / 17.04s**；全量离线 **1784 passed / 8 deselected / 0 failed / 162.53s**（= 1768 + 16）。
- **待用户确认（PENDING_USER_CONFIRMATION）**：
  - **D1**：网页长期记忆默认 `active`（旧：非 Adrian 默认 legacy），且 legacy 也会自动提议候选。功能无越权，但属默认面扩大；未确认前不标 COMPLETED。
  - **D2**：`scripts/start-adrian.cmd` / `start-qianwen.ps1` 默认 `--multi-agent`。构造器仍默认关闭；未确认前不标 COMPLETED。
- **Follow-up（不在本批修）**：F1 进程回收/端口 TIME_WAIT → backlog WIRING-006；F2 根预算/委派预算零余量 → WIRING-007；F3 memory_fixture 迁移缩水 → WIRING-008（NOTE）。
- **边界**：浏览器确认按钮未点击验证；D1/D2 等用户表态；F1–F3 未修；Linux 全量 4 个进程相关失败未在本机复现。

## 2026-11-20 最新续接：六任务接线改造批次（WIRING-000…005，COMPLETED 待验收）

用户六任务清单（任务 0 基线 / 1 Skill 三层披露 / 2 MCP 后台连接+confirm 真排队 / 3 长期记忆+多 Agent / 4 删死代码 / 5 门禁+文档+PR），工作分支 `review/20261120-wiring-fix`。任务 0–5 已在本分支完成并逐任务提交；推远端与开 PR 留到收尾执行，不直接合 main、不强推。

- **任务 0（WIRING-000）COMPLETED**：干净工作区切分支；全量离线基线 **1991 passed / 5 deselected / 200.62s**（与清单预期一致）；默认路径 import 图：140 模块、import 加载 60、未加载 81（其中 `long_memory/`、`mcp/`、`agents/`、`llm/*` 为条件懒加载，**不是死代码**，任务 4 仍须逐个 grep）。
- **任务 1（WIRING-001）COMPLETED**：Skill 三层披露——① `SkillCatalog` 支持目录型 `<name>/SKILL.md` + 子文档白名单（拒绝符号链接/越界/绝对路径）；② 默认路径 SystemPrompt 常开技能清单段（name + description **首句**，600 token 上限，超限降级只放名字再截断）；③ `read_skill(name, path?)` 按需读全文与子文档；④ 新增 3 个真 skill（code-entry-first / criteria-before-tests / evidence-before-compaction，description 含何时用/何时不用+反例）；⑤ trace_runs 记录追加 `prompt_sections`（段 sha256 + skill 数，TraceEvent 契约未动）。
- **验证**：新增 18 例全过；全量 **2009 passed / 5 deselected / 0 failed / 268.28s**（= 1991 + 18）；live（本机 Ollama qwen3.5:4b，真实服务 2 输入内）**PASS**——清单段 sha256 一致、真实模型主动调 read_skill（completed/returned）、全文进工具消息、回答引用技能步骤。
- **过程缺陷（如实）**：首版清单放整段 description，挤占 num_ctx=8192 小窗口的恢复余量，既有验收用例 `test_ollama_runtime` 恢复失败（21748 > 21504 字节）；按 D7 改首句清单后通过，未改 skill 以外压缩逻辑、未放宽既有判据。live 脚本两处缺陷（cookie、跨 run trace 聚合）已修，产品行为正确。
- **任务 2（WIRING-002）COMPLETED**：
  - **后台连接**：`create_session` 且配置了 MCP 时启动 `connect_background()`（daemon 线程逐个连接），状态 `idle→connecting→connected/failed`，不阻塞建会话；失败只记固定码或 `MCPError` 文本，不回显 command/args/env；`connect_mcp` 保留为「未连接/失败」服务器的手动重试入口。
  - **元数据披露**：连接后工具以 `prompt_only=True` 注册——可 `execute`、不进模型 tools 数组；SystemPrompt MCP 段只列 `mcp__server__tool：description`（600 token 上限，超限先降级只放名字、再截断并标注）；完整 schema 在连接时随 `list_tools` 保存、在 `registry.execute` 入口按 required/简单类型校验，缺参不到服务器。
  - **confirm 真排队**：首次调用登记 pending（会话扩展 `mcp_confirmations`，重启后仍在）并挂起 `waiting_user`；`POST /api/sessions/{id}/mcp-confirm` 批准 → 同签名原子消费并定向 resume 执行回填；拒绝 → `ToolRefusedError` → `ok:false` + `refused_by_user=True`，不再包成 `ok:true`；legacy 任务面板加待确认卡片（批准/拒绝按钮）。
  - **验证**：目标用例 **28 passed**；全量 **2026 passed / 6 deselected / 0 failed / 215.69s**（= 任务 1 的 2009 + 新增 17；deselected 5→6 因 `test_mcp_live.py` 由 1 例拆成 2 例）；`tests/live/test_mcp_live.py -m live` **2 passed / 101.95s**（真实 `server-everything`）；真实模型（本机 qwen3.5:4b）验证脚本 **PASS**——后台连接、echo 回填、confirm 挂起→批准执行、拒绝未执行均留 trace。拒绝后模型未重发同一调用，`ok:false` 由离线用例覆盖（证据差异，非产品缺陷）。
  - **边界**：/legacy 确认按钮未做浏览器点击人工验证；DSH 默认外壳无任何确认 UI（既有缺口，不扩范围）；后台连接失败不自动重试；prompt-only 工具调用只验证 qwen3.5:4b；MCP 工具不单独计预算。
- **任务 3（WIRING-003）COMPLETED**：
  - **长期记忆写入**：每轮结束 `_auto_propose_memory` 只扫描用户消息里的明确「记住/remember X」，生成 pending 候选；确认仍走 `/memory/candidates` + `/memory/confirm`（Build + 一次性 review_token），确认前不入库、不注入；关闭期间也推进游标，避免重新开启时追溯旧消息。网页默认记忆模式改为 `active`，`--legacy-long-memory` 回退、`--no-long-memory` 关闭；构造器默认仍 `legacy`，不改变直接构造应用的调用方。
  - **多 Agent**：新增 `agents/factory.py` 与 `--multi-agent` 开关；`scripts/start-qianwen.ps1` / `start-adrian.cmd` 默认开启。只接 **calculator** worker（PASS 依赖宿主生成的 calculator 工具回执），文本角色因无确定性判据不接；`delegate_task` 复用任务总预算 40/60（打开时片段上限提升），子预算仍走 `parent_budget.child(...)`。
  - **验证**：新增离线 10 例全过（记忆 4、多 Agent 3、JSON 围栏 3）；真实 qwen3.5:4b 跑 **3 个真实委派**（2–3 子任务）：任务 1/2 与失败后重试均 3/3 PASS（汇总 405/93），`total_models=3` 的失败轮 `partial/budget_exhausted` 且模型调用恰好 3 次；记忆 live：真实模型「记住：…」→ 自动候选 → 确认 → 正式库 1 条。`tests/live/test_wiring_live.py` **2 passed / 216.23s**。
  - **过程缺陷（如实）**：真实委派先后暴露 4 个断点——`_build_transport` 把档位 key 当对象；委派结果触发工具摘要时 qwen 返回 ```json 围栏导致 `context_limit`；根契约 `max_subtasks*2+2` 不够 3 个 worker；worker 重试超工具上限。均已最小修复并留回归；首版 writer 角色因「结论」格式标记不可靠被删除，没有放宽验收判据。
  - **边界**：候选确认按钮未做浏览器点击；`--multi-agent` 未在真实服务进程 + 浏览器端到端跑；只验证 qwen3.5:4b 的算术委派；失败重试由验证脚本重新发起，不是产品自动重放；calculator 的 PASS 只证明宿主收到工具回执、结果结构合法，**不核对回执数值与摘要是否一致**（`semantic_review=UNKNOWN`）。
- **任务 4（WIRING-004）COMPLETED**：grep 全仓确认零生产引用后删除 **33 个被删文件 / 5856 行**（去空行 5072、空行 784；src 3208 + scripts 413 + tests 2235）——`activity/` 整包（2176 行）、`evaluation/{evolution*,night*,night_executor}.py`（853 行）、`long_memory/evaluation.py`（173 行）、A 批 `llm/context_capabilities.py` 与 `scripts/migrate_layout_20261115.py`，同步删 4 个脚本、**11 个**只覆盖被删模块的测试文件（`test_activity_*` 7 个 + evolution 2 + night 1 + adrian_memory_eval 1）及混合文件里的 night 用例；`build_approved_fixture` 迁到 `tests/fakes/memory_fixture.py` 以保留 delivery 覆盖。保留 `evaluation` 的 verifier/runner/suites/improvement 等生产仍用部分。
- **任务 5（WIRING-005）COMPLETED**：`AGENTS.md` 新增「接线门禁」——新模块合 main 前必须在默认路径真实调用一次，默认关闭必须有可运行路径，删死代码前 grep、删后全量（定义/豁免/优先级由 WIRING-FIX-001 补齐）；修正 `PROJECT_STATUS` 主循环 6 阶段（`runtime/loop.py:73-84`）与多 Agent 静态接入表述。README/NIGHT_MODE/EVOLUTION_V1/MODULE_MAP 的删除标注属 WIRING-004（`64cd6e2`），不在本任务的 `d77c5f1`。
- **合并验证**：全量离线 **1768 passed / 8 deselected / 0 failed / 140.22s**（任务 3 后 2036，删 268 个收集项后 1768）；8 个 deselected 为 `tests/live/`。基线对比：1991 → 2009（+18）→ 2026（+17）→ 2036（+10）→ 1768（−268）。
- **边界（总）**：live 只覆盖固定小样本，不构成成功率统计；浏览器 UI（MCP 确认、记忆候选）未做点击验证；DSH 外壳无确认 UI；多 Agent 仅 calculator 角色；未推远端、未开 PR、未合 main。
- [决策](engineering-log/DECISION_LOG.md)（WIRING-001 D1–D7、WIRING-002 D1–D7、WIRING-003 D1–D8、WIRING-004 D1–D4、WIRING-005 D1–D2）、[实施](engineering-log/IMPLEMENTATION_LOG.md)、[验证](engineering-log/VALIDATION_LOG.md)；本地证据 `reports/wiring-20261120/`（gitignored）。

## 2026-11-20 外部审查 4 项修复（REVIEW-FIX-001）

外部 agent 对 `review/20261117-runtime-mcp` 给出 Approve with fixes；本轮按顺序修 4 项，未扩大 MCP 能力范围。

- **① 轮数上限**：`Runtime(max_rounds=64)`；新增 `RoundLimit` 阶段，超限返回 `partial/round_limit`，不再只依赖预算。覆盖 `budget=None` 的 `llm/smoke.py`、`evaluation/improvement.py` 路径。
- **② 子进程回收**：`StdioMCPClient.close()` 改为 terminate→wait→kill→wait→close→join（线程各 2 s）；测试断言 close 后 `poll()` 非 None、reader/stderr 线程退出。
- **③ 参数本地校验**：新增 `tools/schema.py`，只校验 `required` 与单一简单类型；`anyOf/$ref/enum` 等跳过，避免误拒；失败返回 `invalid_arguments`，不触达服务器。完整 JSON Schema 仍由服务器校验。
- **④ 协议版本**：`initialize` 只接受 `2025-06-18`/`2024-11-05`；只有 JSON-RPC `-32602` 才降级重试，删除“错误文本含 protocol”的猜测。
- **验证**：目标用例 **67 passed**；终版全量 **1991 passed / 5 deselected / 0 failed / 214.89s**（+14 项，前值 1977）；真实 `server-everything` 改动后仍发现 **16 个工具**，应用层 live **1 passed / 6.55s**（ScriptedModel，无真实模型调用）。
- **未做/边界**：完整 schema 校验、`-32602` 之外的版本拒绝、远程 MCP、skills/memory 提示词段。
- [决策](engineering-log/DECISION_LOG.md)（REVIEW-FIX-001 D1–D4）、[实施](engineering-log/IMPLEMENTATION_LOG.md)、[验证](engineering-log/VALIDATION_LOG.md)。

## 2026-11-17 最新续接：渐进式 MCP 接入（MCP-001）

- **做了什么**：新增 `src/min_agent/mcp/`（配置、stdio JSON-RPC 客户端、目录、工具构造）。对话开始时 system 段只列 MCP 服务器 **name + description**，tools 里只有 `connect_mcp`；模型选择后连接服务器、发现工具，并以 `mcp__<server>__<tool>` 并入同一个 `ToolRegistry`，**下一轮**模型请求才看到这些工具。新增 `context/SystemPrompt` 组件提示词段机制，`Runtime` 支持每个请求重新生成系统提示词。
- **接入方式**：`HarnessApplication(mcp_servers=...)` + `python -m min_agent.web --mcp-config path.json`；配置里的 `command/args/env/cwd` 只来自可信 host，模型只能选服务器名；`policy=confirm` 当前明确拒绝执行。连接按会话保留，`app.close()` 回收子进程。
- **验证**：离线 **24 passed**（真实 stdio 子进程 + 假服务器，不联网、不调用模型）；组件提示词 **2 passed**；全量 **1977 passed / 5 deselected / 0 failed / 215.72s**。真实 MCP：`@modelcontextprotocol/server-everything` 发现 **16 个工具**，`echo` 返回 `Echo: hello-mcp`，`get-sum` 返回 `The sum of 2 and 3 is 5.`；应用层 live 测试 **1 passed**（连接前无 MCP 工具、连接后出现、结果落库）。
- **实测成本**：连接 everything 后工具 schema 约 **8.8 KB / 约 9.9k tokens** 估算；小窗口模型不应连接大服务器。
- **边界**：只做 stdio；未做 HTTP/SSE、OAuth、resources/prompts/sampling、`confirm` 交互审批、同一任务热重连、MCP 单独预算；**未用真实模型跑 MCP 对话**（会消耗模型调用，未授权）；skills / memory 的组件提示词段尚未接（这次只落了 `SystemPrompt` 机制与 MCP 段）。
- **过程缺陷（如实）**：`connect_mcp` 最初用 `Enum + strict` 导致参数全失败；连接结果回传全部工具说明超过 2000 字节触发摘要/`context_limit`；`EvalApplication._registry` 签名未同步导致 10 项评测 `UNKNOWN`。均已修复，失败证据在 `reports/mcp-20261117/`。
- **提交状态**：已推送 GitHub 分支 `review/20261117-runtime-mcp`（提交 `72fef51`，父提交 `3dfd707`）；本地私有材料（`docs/interview-*`、`docs/career-positioning-*`、`docs/tech-audit-*`、`.workbuddy*`）未提交。外部审查建议直接看该分支相对 `3dfd707` 的单个提交。
- [决策](engineering-log/DECISION_LOG.md)（MCP-001 D1–D8）、[实施](engineering-log/IMPLEMENTATION_LOG.md)、[验证](engineering-log/VALIDATION_LOG.md)；配置与测试说明见 [MCP](MCP.md)。

## 2026-11-15 最新续接：Runtime 瘦身 + 包结构分类（RUNTIME-SLIM-001 / PKG-LAYOUT-001）

- **做了什么**：① 原 `runtime.py`（547 行、`_run` 约 250 行）拆成 `src/min_agent/runtime/` 包——主循环约 20 行，只按序调用 **6 个阶段**（`ControlGate` / `RoundLimit` / `ReserveGate` / `ModelCall` / `ResponseRouter` / `ToolBatch`，见 `runtime/loop.py:73-84`）；控制、预算预留、模型调用、响应分派、工具批次、收尾、提示词投影、错误载荷分别进 `stages.py` / `tool_loop.py` / `closing.py` / `prompt.py` / `errors.py`。② `src/min_agent` 根目录 62 → 2 个文件（`__init__.py`、`application.py`），59 个模块按职责进 11 个子包；找文件先看 [MODULE_MAP](MODULE_MAP.md)。
- **判据与结果**：现有测试的**断言一条未改**（只更新导入路径与 `-m` 入口）；基线 **1921 passed / 4 deselected / 199.14s** → 阶段 A **1940 passed**（+19 项新测试）→ 阶段 B **1942 passed**（+2 项布局门禁）；JUnit 对照两次均 **PASS**（1921 条全部存在、0 条结果变化）。导入审计 301 文件 0 unresolved；全模块导入 132/132；`find_packages` 18 个包；`python -m min_agent.web --help` exit 0。
- **边界（重要）**：**未跑**真实模型、Ollama/DeepSeek/千问、浏览器 UI、live 用例；`verify_release.py` 的 wheel 分离环境验证 **NOT RUN**（本机无离线 wheelhouse）；`manager.py` 的 Worker 循环未拆；历史 `docs/steps/*` 旧路径不改；不证明未覆盖语义的等价性与性能。
- **对外可见变化**：`-m min_agent.activity_connector` / `-m min_agent.activity_memory_adapter` → `-m min_agent.activity.connector` / `-m min_agent.activity.memory_adapter`（**WIRING-004 已删除 `activity/` 包，以上命令当前不可用**）；`min_agent.web`、`min_agent.evaluation`、`min_agent.application` 不变。
- **过程缺陷（如实）**：首轮 3 个表征测试是我写错（非产品缺陷）；迁移脚本漏了 `from min_agent import activity_*` 形式导致 4 failed；补规则时二次重写又造成 52 文件 / 101 处重复映射，已精确修复并补审计。失败证据与修复脚本都在 `reports/refactor-20261115/`。
- **提交状态**：已随 `review/20261117-runtime-mcp`（commit `72fef51`）提交并推送。
- [决策](engineering-log/DECISION_LOG.md)（RUNTIME-SLIM-001 D1–D5、PKG-LAYOUT-001 D6–D10）、[实施](engineering-log/IMPLEMENTATION_LOG.md)、[验证](engineering-log/VALIDATION_LOG.md)。

## 2026-11-15 最新续接：FS-MUTATE-001 文件工具族 + SHELL-004 外壳诚实化

- **做了什么**：补齐文件工具族——`fs_mkdir`（建目录）、`fs_move`（重命名/移动，**不覆盖**）、`fs_delete`（**移入 `.adrian/trash` 回收站**，只删文件和空目录）、`fs_write`/`fs_edit` 的 `dry_run` unified diff 预览、跨执行体写锁（锁文件 + 3 s 超时 + 30 s 陈旧接管）、符号链接拒绝。外壳不再假装支持本地没有的能力：附件/语音/朗读/搜索/思考入口置灰或点击即说明；侧栏改名为真实服务端接口，删除改为「仅本机隐藏」。
- **新增/修改**：`src/min_agent/tools/filesystem.py`（382 → 852 行）；`application.py`（`set_label`）；`web/app.py`（`POST /api/sessions/{id}/label`）；`tests/unit/test_fs_mutation.py`（新，29 项）；`tests/integration/test_session_label.py`（新，4 项）；`tests/unit/test_fs_write.py`（工具清单 4 → 7）；`.gitignore`（`.adrian/`）；外壳 `ChatInput.tsx`、`AssistantMessage.tsx`、`SettingsDialog.tsx`、`Sider.tsx`、`adrian.ts`、`store.ts`、`serverSync.ts`、`i18n/{zh,en}.ts`；重建 `src/min_agent/web/shell/`（153 文件 / 4.17 MB）。
- **验证**：离线新增 **33/33**（29 + 4）；全量 **1921 passed / 4 deselected / 196.13s**（基线 1888 + 33）。真机 E2E 一条会话完成 7 步（建目录→写→读→dry_run 预览→真改→移动→删除），`model_calls=10 / tool_calls=8`，终态 `completed`，删除文件在 `.adrian/trash/20261115T050444-f47feefe/b.md` 且内容一致。Playwright `/app` 6 步全过、0 pageerror：附件提示、聊天、改名写回服务端、刷新保持、删除仅本机隐藏、服务器会话仍在。
- **边界（重要）**：删除**不递归**、移动**不覆盖**、回收站**不自动清理**；写锁不覆盖外部编辑器、30 s 陈旧判定是启发式；Windows junction/断电一致性/跨卷未验证；授权门仍是**会话级**（Build + 已确认），不是逐动作签名。**服务端会话删除未实现**（避免破坏性数据操作，需单独授权）；附件/语音/联网搜索未实现，只是明确拒绝。
- **启动状态（SHELL-005）**：`http://127.0.0.1:8765` 默认入口**已改为 DSH 外壳**——`/` 返回 307 → `/app/`；旧界面保留在 `/legacy`。服务已重启（pid 6284），桌面快捷方式仍指向根路径，打开即进入外壳。外壳标题与侧栏品牌改为「Adrian 工作台」。
- **欢迎页选模型（SHELL-006）**：新增 `GET /api/models`（无会话的目录）；欢迎页（还没发送消息）就显示模型下拉，选择先存本机 `adrian:pending-model`；发送时 `POST /api/sessions {label, model}` **在建会话时绑定**，非法模型 400 且不建空会话。验证：离线契约 **5/5**；Playwright 欢迎页 4 个选项、仅选择**不创建**会话、发送后 `model.current=qwen3.8-max`、0 pageerror；全量 **1947 passed / 4 deselected / 193.51s**（并发重构之后的树上，含 SHELL-005 路由）。
- **Plan/Build 模式（SHELL-007）**：删除输入区的 DeepThink / Search（本地后端没有这两项）；改为「模式」下拉（Plan 只读 / Build 可写），欢迎页即可选，选择先存 `adrian:pending-mode`，`POST /api/sessions {label, model, mode}` 建会话时绑定。Build 在未确认目标时真实进入 `waiting_user/goal_unconfirmed`，写类工具仍被授权门拒绝，**不伪装成已授权**。验证：离线 **4/4**；Playwright 欢迎页无深度思考/智能搜索、默认 Plan、仅选择不建会话、发送后 `work_state.mode=build`、会话内可切回 Plan、0 pageerror；全量 **1951 passed / 4 deselected / 194.01s**。服务重启（pid 29404）。
- **未提交、未推送**；工作区含前序未提交改动，本批只追加自己的部分。
- [决策](engineering-log/DECISION_LOG.md)（FS-MUTATE-001 D1–D8、SHELL-004 S1–S4）、[实施](engineering-log/IMPLEMENTATION_LOG.md)、[验证](engineering-log/VALIDATION_LOG.md)。

## 2026-11-14 上一批：QWEN-SELECTOR-001 Web 模型选择器（千问平台）

- **做了什么**：发送区新增模型下拉（`#model-choice`），接入千问平台 OpenAI 兼容 Chat Completions；4 个白名单模型 `qwen3.8-flash`（默认）/ `qwen3.8-max` / `qwen3.7-plus` / `deepseek-v4.1-flash`；选择**按会话**保存（`model_choice` 扩展），运行中不可切，非白名单 400，未知/越权调用直接拒绝；模型调用失败时横幅显示**具体原因**（余额不足 402 / 凭据无效 401 / 限流 429 / 网络不可达），不再一律“任务已暂停”。
- **新增/修改**：`src/min_agent/llm/qianwen.py`（新，适配器+目录+解析器）；`llm/config.py`（凭据白名单加 `qianwen_api_key`）；`application.py`（`model_catalog` / `set_model` / `model_status` / 快照 `model` 字段）；`runtime.py`（`LLMError.code` 透出到 `RunResult`，trace 仍固定码）；`web/__main__.py`（`--qianwen` / `--qianwen-model`）；`web/app.py`（`POST /api/sessions/{id}/model`）；`web/static/{index.html,app.js}`；`tests/unit/test_qianwen_adapter.py`（新，25 项）；设计文档 `docs/2026-11-14-qwen-model-selector-design.md`。
- **验证**：离线契约 **25/25**；全量回归 **1845 passed / 4 deselected / 180.35s**（基线 1820 + 25）；真实请求 **2 次**——适配器烟测（stop/OK/101 tokens）与端到端问答（`completed`，4.0s，「1 + 1 等于 2。」，`model_calls=1`）。失败证据保留：2 处旧测试期望按新契约更新（超时→`network_error`）、1 处自测判据写错、1 个 trace `Literal` 校验缺陷已修。
- **启动方式**：`scripts\start-adrian.cmd` 之外的显式入口——`.venv\Scripts\python.exe -X utf8 -m min_agent.web --qianwen --database data/<库>.sqlite3 --port 8765`；凭据读 `%USERPROFILE%\agent-harness-credentials.json` 的 `qianwen_api_key`（已写入，值未回显、未进仓库）。
- **边界（重要）**：`deepseek-v4.1-flash` **未做真实烟测**（可能需先在控制台开通）；思考模型 `reasoning_content` 只在“正文为空”时兜底，**未验证多轮兼容**；窗口值是声明值（`declared:qianwen-table`），非实测容量；浏览器截图 **NOT RUN**（本机无 Playwright 内核）；**2 次请求不构成成功率统计**。Gemini / Groq **未接入**。
- **桌面启动（QWEN-SELECTOR-002）**：桌面快捷方式 `Adrian 千问工作台.lnk` → `scripts\start-qianwen.ps1`（前台运行、默认 8765、默认库 `data/adrian-qianwen.sqlite3`、就绪后自动开浏览器）；端口上已有服务时先识别 provider，是千问就直接开页面、是别的就提示先关闭，不重复启动。
- **体验修复（QWEN-UX-001）**：发送后立刻显示自己的消息（`pending_input` + 前端乐观渲染，实测 0.21s 出现）；运行中轮询 1500ms→400ms；进程被强杀时下次启动把未完成输入补回历史并标 `partial/interrupted`；`interview-demo`(4) + `workspace`(3) 的 7 个旧会话已合并进快捷方式库（共 8 个，合并前备份 `adrian-qianwen.sqlite3.bak-20261114-211623`，旧库未改）。回归 **1849 passed / 164.85s**。
- **逐字输出（QWEN-STREAM-001）**：`streaming.py`（会话级 `StreamSink` + `StreamingModel`）+ `qianwen.generate_stream`（`stream=true`、SSE 解析、工具参数分片拼接）+ `GET /api/sessions/{id}/stream`（SSE，无流 409）+ 前端 `EventSource` 增量追加。**Runtime 仍拿完整响应**，工具循环/预算/trace 零改动。真机：26 段增量、首段 5.43s、完成 6.98s、流式文本与最终消息完全一致。回归 **1860 passed / 184.92s**。**DeepSeek/Ollama 仍非流式**（无 `generate_stream` 自动退回）；工具调用文字不参与流式；断线不补发。
- **新外壳（SHELL-001，PARTIAL）**：核实 `deepseek-ai/chat-demo`（MIT，`5d5dd75`，React+rspack）**自带数据层**（IndexedDB + 直连 Anthropic 兼容 API），故按 fork 换数据层推进。源码 vendored 到 `shell/`（166 文件，保留 LICENSE 与上游说明），`scripts/build-shell.ps1` 构建到 `src/min_agent/web/shell/`（153 文件 / 4.19 MB），FastAPI 挂 `/app`（旧界面仍在 `/`），CSP 仅放宽 `style-src`。已验证：`/app/` 200、主 JS 200、Playwright 渲染出上游界面且 0 pageerror；回归 **1861 passed / 230.91s**。**数据层尚未替换**——现在打开 `/app` 仍要求填 DeepSeek API Key，不会连我们后端。
- **新外壳接线（SHELL-002，PARTIAL）**：只换 `streamMessage`/`verifyApiKey` 并在请求体加 `conversation_id`，组件零改动；新增 `shell/src/services/api/adrian.ts` 负责 CSRF、会话映射与 SSE→消息事件翻译。真机验证：在 `/app` 发「3+3 等于几？」→ 回答「3 + 3 等于 6。」流式显示，服务器会话 8→9、`completed`、user/assistant 各一条，0 pageerror。**未做**：侧栏与 `/api/sessions` 合并、模型选择器、待办/通知/轨迹面板、附件/语音/搜索（入口仍在但会明确报错）。
- **侧栏与模型（SHELL-003，COMPLETED）**：`serverSync.ts` 首屏把 `/api/sessions` 有消息的会话导入本地（**本地 id = 服务器 session_id**，工具消息跳过）；`ModelPicker.tsx` 读 `model`/调 `POST /model`（带 CSRF），会话未建立时隐藏；`ChatInput.tsx` 是唯一改动的组件。验证：侧栏出现服务器会话、打开渲染「1234 × 56 = 69,104」、切 `qwen3.8-max` 后服务器 `model.current` 生效、0 pageerror。**未做**：新建会话切换、重命名/删除回写服务器、附件/语音/搜索隐藏、待办/通知/轨迹面板。
- **只读文件工具（FS-READ-001，COMPLETED）**：`tools/filesystem.py` 新增 `fs_list` / `fs_read`，根内解析、越界/符号链接逃逸拒读、`256 KiB` 上限不截断、敏感文件（`.env`/凭据/`*.sqlite3`）拒读、返回 `sha256/mtime_ns` 供未来写入校验；**显式传 `workspace_root` 才注册**（避免改变默认提示词，修掉 1 个真实回归）；`start-qianwen.ps1` 已带 `--workspace-root`。验证：离线 12 项 + 真机一次 `fs_read`（69 字节、hash 一致、回答原文），全量 **1873 passed / 202.69s**。**写/改/删未做**。
- **读写文件工具（FS-WRITE-001，COMPLETED）**：新增 `fs_write` / `fs_edit`：**必须 Build + 已确认目标**（会话级门），覆盖/编辑必须带上次 `fs_read` 的 `sha256`（不匹配 `stale_version`），`fs_edit` 要求原文唯一匹配，原子写（失败不改原文件），敏感路径/超 1 MiB/父目录缺失拒绝。验证：离线 14 项 + 两轮真机（未确认时**没写**；确认后 `fs_edit` 成功、文件变「第二行已修改」、复读一致）。首轮全量 84 failed 系 `_registry` 内 import 造成局部名遮蔽，已修；全量 **1888 passed / 201.46s**。
- **未提交、未推送**；工作区含前序未提交改动，本批只追加自己的部分。
- [决策](engineering-log/DECISION_LOG.md)（D1–D7）、[实施](engineering-log/IMPLEMENTATION_LOG.md)、[验证](engineering-log/VALIDATION_LOG.md) 的 QWEN-SELECTOR-001。

## 2026-11-14 上一批：MA-P5-013 Multi-Agent 功能补全 + 静态接入生产主循环

> **后续状态（2026-11-20，WIRING-003）**：本节「默认关闭、生产行为 NOT VERIFIED」已被后续任务取代——新增 `--multi-agent` 开关与 `agents/factory.py` 工厂，`scripts/start-qianwen.ps1` / `start-adrian.cmd` 默认开启；本机 Ollama 真实跑通 2–3 子任务委派，并用「预算耗尽失败 → 正常预算重试」验证了失败恢复（见 VALIDATION_LOG → WIRING-003）。以下保留当时记录，不追溯改写。

- **做了什么**：按用户计划书补全 5 处缺口并静态接进生产主循环——① **上游产物注入**（`TaskEnvelope.upstream`，只取直接依赖里 `completed + PASS + output 非空` 的，在提示词里作 `[FROM_UPSTREAM_TASK]` **数据段**）；② **worker 受限工具循环**（`ToolExecutor` 注入 + `contract.check_tool` 白名单 + `before_tool`/`after_tool`）；③ **双预算硬门**（hook 放行 → 资源预占 → `BudgetSegment` 原子扣减 → 才调用；`usage` 缺失按预占上界记 `reserved_upper_bound`）；④ **点对点 Mailbox**（四类通知，有界、无广播、不改状态）；⑤ **汇总失败不伪装完成**（`_aggregate()` 返回 `(content, error)`，只有全部 PASS 且汇总成功才 `completed`）。新增 `agent_messages.py`（316 行）、`context_policy.py`（202 行）、`multi_agent_service.py`（578 行）；改 `subagent_collab.py`（独立性改机械公式）、`manager.py`（1453 行）、`application.py`。
- **怎么接入的**：`application.py` 默认 `enable_multi_agent=False`；只在开关为真**且**有可信 `multi_agent_service_factory` 时才在 `research` 之后条件注册**同步工具** `delegate_task`。处理器用闭包里的可信 `context/segment/registry/events`，每次调用新建 `MultiAgentService`、`finally` 释放；外层 Runtime 已把这次工具调用计入外层预算，**内部只扣子预算**，`delegate_task` 与 `research` 在生产 worker 里被硬禁用（防递归）。
- **为什么不需要改 `events.py`**：`delegate_task` 在主 Runtime 的**一次工具执行里**同步跑完，Manager 不调 `SessionEvents.started()`，其内部 `ThreadPoolExecutor` 不是新的会话控制循环，worker 只读 `control.snapshot()` 观察 stop、不消费主循环 steers。历史「接主循环必须先改 `events.py`」只适用于「让 Manager 成为第二个会话控制循环」的方案，已在 DECISION_LOG D8 **追加更正**（不改旧文）。
- **默认关闭的边界**：`enable_multi_agent=False`、`max_workers=1`；关闭时不创建 Service / Manager / Mailbox / ResourceLedger、不注册工具、不产生额外线程。`enable=True` 但缺 factory → **应用构造立即失败**（不回退 Demo、不借主模型冒充强/弱模型）。本批**不新增 CLI 开关**。
- **只做了哪些静态检查**：`ast.parse` 6/6;`.venv` 导入 6/6；`inspect.signature` 关键接口；AST 精确引用 7 项（开关默认值、`delegate_task` 只在真分支、`manager.py` 零引用废弃符号、Service/Manager 不调 `started`/`finished`/`poll`、`independent_evidence` 不再是常量 `False`、executor 前必有 `check_tool`、上游三限定齐全）；`git diff --check` exit 0；禁改 12 路径 `status` + `mtime` 核对；`tests/` 状态与基线一致；凭据/环境变量/import-time 网络调用 0 命中。证据 [static-gate.json](../reports/ma-p5-013/static-gate.json)（`reports/` 被 `.gitignore` 忽略，属本地证据）。
- **计数与边界**：新增测试 **0**、行为测试 **0**、离线自验 **NOT RUN**、在线测试 **NOT RUN**、真实 `delegate_task` 调用 **NOT RUN**、外部请求 **0**、模型调用 **0**。**静态全过 ≠ 功能正确**：生产行为 **NOT VERIFIED**。
- **未完成（如实登记）**：`shared` 上下文**没有**接进生产提示词链路——`ContextPolicy` 支持它，但服务构造期对 `context_mode != "isolated"` 抛 `NotImplementedError("shared_context_not_wired")`，宁可显式拒绝也不假装支持。因此本批实现范围标 **PARTIAL**，不标 COMPLETED。
- **两次门禁误报已留更正**：① 把「历史遗留未跟踪文件」当成被本批修改（改用基线快照 + `mtime`）；② 把 CRLF 当成缺陷（`core.autocrlf` 会转 CRLF，改为只检混用/缺尾换行）。另修两处真实缺陷：`application.py` 被写成 CRLF 已恢复 LF（diff 仍 48 行）、三个新文件补末尾换行。
- **下一批启动条件与第一步**：需要用户授权**行为测试**与必要的**有界在线请求**后，沿用 MA-P5-012 的 `fingerprint` + 同一判据范式做离线/在线双跑，覆盖 Mailbox 顺序与状态一致性、isolated/shared 隔离、独立性真值表与同模型反例、上游 PASS/FAIL/UNKNOWN 数据流、工具白名单/预算/hook 拒绝/UNKNOWN 写入、并行取消与迟到结果、主循环 stop 与 owner/session 隔离、`provider_state`/usage 适配、默认 off 与显式 on 的真实差异。**本批一律 NOT RUN。**
- [决策](engineering-log/DECISION_LOG.md)（D1–D14）、[实施](engineering-log/IMPLEMENTATION_LOG.md)、[验证](engineering-log/VALIDATION_LOG.md) 的 MA-P5-013。**未提交、未推送、未启用。本批停止。**

## 2026-11-14 上一批：MA-P5-012 同一用例集离线/在线双跑（验证批次）

- **做了什么**：按用户既定「测试分离线/在线、测试集必须一样」规范，新增 `reports/ma-p5-012/dual_run.py`（4 个冻结样本 + 唯一判据 `evaluate_plan()` + SHA-256 `fingerprint()`），离线用 `ScriptTransport`、在线用真实 `qwen3.8-flash`；**产品 `src/` 零改动**。
- **重点**：验证 MA-P5-011 明确接受的风险——真实模型会不会漏画 `depends_on` 边。逐用例写死 `expected_min_edges`，漏边判 FAIL、多加边只记录。
- **结果**：判据自检 **6/6**；离线 **4/4 PASS**；在线 **4/4 PASS**（4 次请求全部 HTTP 200 / finish=stop，19.32s，2518 tokens）；逐用例边数 **1 / 1 / 0 / 2**，与期望完全一致，**无漏边、无过度串行**；两组 `fingerprint` 一致、`case_ids` 顺序一致、结果结构一致。
- **有界**：在线 4 ≤ 12 次、19.32s ≤ 300s、单次 max_tokens 512、仅合成任务、0 收费内置工具、¥300 代金券内；key 只从 `.env` 读入进程内存，结果文件与脚本均无凭据字面量。
- **回归/门禁**：既有 **199 passed / 13.33s**；`ast.parse` PASS；`git diff --check` exit 0；`src/` 零改动、`tests/` 零新增、历史证据零改写。
- **三版证据全保留**：预检 4/6（真实判据缺陷：结构非法未设 `outcome=FAIL`）→ 修后离线 6/6+4/4 → 终版离线+在线 4/4+4/4。静态检查时间戳误报第三次出现，已用正确日期口径重查并留更正记录。
- **边界（重要）**：**4 个合成用例、各 1 次运行，不声称总体成功率或模型能力结论**；不证明复杂/模糊任务、其他模型、多轮长上下文下的画边质量；语义质量固定 UNKNOWN。正式 `tests/` 套件、第 4/7 项、接主循环、独立审查仍未做。
- [决策](engineering-log/DECISION_LOG.md)（T1–T7）、[实施](engineering-log/IMPLEMENTATION_LOG.md)、[验证](engineering-log/VALIDATION_LOG.md) 的 MA-P5-012。**本批停止**。

## 2026-11-14：MA-P5-011 规则只查依赖是否完成（第 3/7 项第二次返工）

- **用户纠正**：MA-P5-010「还是一有写就串行」不对。**是否并行交给模型判断，规则只判断执行时依赖项执行了没有**。据此删除上一版叠加的资源冲突判定层。
- **唯一判据**：模型画规划时用 `depends_on` / `blockedBy` 边表达「不能同时跑」；规则只做 `TaskStore.can_start`（依赖是否全 completed）+ `claim`/`complete`/`fail` 状态机。`max_workers` 只是并发上限的机械切批，**不是并行判定**。
- **改动**：`parallel.py` 新增 `ready_batches()`（纯切片）、把 `ResourceScope`/`conflict_reasons`/`conflict_batches`/`layer_mode` 标废弃但保留给历史脚本 import；`manager.py` 删掉冲突判定的全部引用与 `_scope_of`/`_policy`，`PLAN_SYSTEM` 改为只要求画依赖边、明确「不会因写操作自动串行」；`task_graph.py` 状态机零改动、仅更新文档。计划里的 `writes`/`reads`/`resources_declared` 降级为**零读取兼容占位**，避免破坏 MA-P5-010 历史计划 JSON。
- **数量**：离线自验 **33/33**；峰值 两写任务无依赖 **2** / 两写任务加依赖边 **1** / 三任务上限2 **2** / 默认串行 **1**；既有回归 **199 passed / 12.55s**；**0 次外部请求、0 次模型调用、0 费用**；`tests/` 零新增；历史 `reports/ma-p5-003..010` 零改写；`git diff --check` exit 0。
- **AEF-004**：`AGENTS.md` 第 12 行新增「核心片段必须逐行讲解」规则，9/9 要素、单次出现、无删除行；证据 `reports/aef-004-rule-check.json`。本次交付已按新规则首次示范。
- **两次运行证据保留**：首跑 32/33（我把 `asdict` 的 tuple 断言成 `[]`，断言格式错误、非产品缺陷），修后 33/33；两版 JSON 均未删未覆盖。
- **边界（重要）**：**模型漏加一条 `blockedBy` 边就会错误并行，规则不再兜底**——这是按用户指令明确接受的风险。跨文件语义冲突、真实模型画边质量、在线行为、多进程并行均未验证。在线测试与离线/在线同用例集双跑 **NOT RUN**；默认 off、未接主循环、未提交未推送。
- [决策](engineering-log/DECISION_LOG.md)（S1–S7 / R1–R4）、[实施](engineering-log/IMPLEMENTATION_LOG.md)、[验证](engineering-log/VALIDATION_LOG.md) 的 MA-P5-011 / AEF-004。**本批停止**。

## 2026-11-14：MA-P5-010 模型画规划 / 规则管状态机（已被 MA-P5-011 返工）

- **用户纠正**：上一批「并行判定交给确定性规则」不成立——规则本身要人/模型打 `read_only`/`write` 标签，无法保证不出错；且「写操作整层串行」过粗，写不同文件应可并行。改为**模型画规划（依赖边 + 资源声明），规则只管状态机与集合相交查询**。用户第 3 点未写出内容，待补。
- **新增** `src/min_agent/task_graph.py`（340 行）：`Task` / `TaskStore` / `TaskTools` / `Completion`。`.tasks/` JSON、`task_` + 8 位十六进制、排他创建（`O_EXCL`）、原子替换、两阶段建图、整次校验后统一保存、环与自依赖拒绝、`can_start` / `claim` / `complete` / `fail` 状态机、解锁列表。
- **重写** `src/min_agent/parallel.py`：`ResourceScope` / `conflict_reasons` / `conflict_batches`。冲突只对模型声明的资源做集合相交：写同文件串行、写不同文件并行、读写同文件串行、纯读并行；未声明保守独占。`layer_mode` 标废弃但保留给历史脚本 import。
- **接入** `src/min_agent/manager.py`：`Subtask` 增加 `writes`/`reads`/`resources_declared`；`PLAN_SYSTEM` 要求模型显式画边与声明资源；`_build_graph()` 两阶段建图；并行路径按冲突分批；`ManagerRun.task_graph` 带脱敏终态快照。**`max_workers=1` 保持原串行行为、不建图**。
- **数量**：离线自验 **57/57**；峰值 写不同文件 2 / 写同文件 1 / 未声明 1 / 显式依赖 1 / 默认串行 1；既有回归 **199 passed / 12.87s**；**0 次外部请求、0 次模型调用、0 费用**；`tests/` 零新增；历史 `reports/ma-p5-003..009` 零改写；`git diff --check` exit 0。
- **三次运行证据全部保留**：首跑 `AttributeError`（`sub_by_id` 映射缺陷，真实 bug）、二跑 51/52（我断言写错，非产品缺陷）、三跑 53/53、终版 57/57。详见 [验证日志](engineering-log/VALIDATION_LOG.md) → MA-P5-010。
- **边界**：规则不保证模型声明正确；隐式/跨文件语义冲突查不出；barrier 贪心分批会损失部分并行度；线程级并发非多进程；默认 off、未接主循环、未提交未推送。在线测试与离线/在线同用例集双跑 **NOT RUN**。
- [决策](engineering-log/DECISION_LOG.md)（P1–P9）、[实施](engineering-log/IMPLEMENTATION_LOG.md)、[验证](engineering-log/VALIDATION_LOG.md) 的 MA-P5-010。**本批停止**。

## 2026-11-14：MA-P5-009 并行派发（第 3/7 项，已被 MA-P5-010 返工）

- 第 3 项：依赖分层、只读并发 / 写串行、并发上限、结果顺序、并发取消。新增 `src/min_agent/parallel.py`（`ConcurrencyPolicy` / `plan_layers` / `layer_mode`）；`manager.py` 支持 `max_workers>1` 与 `transport_factory`；`WorkerSpec.access` 默认 `write`（保守）。
- **N2 认知修正**：`Manager` 不依赖 `SessionEvents`，独立运行可并行；「必须先改 events.py」只在**接主循环**时成立。接主循环的前置条件未变。
- **并发暴露两个真实缺陷并已修**：`ResourceLedger.reserve` 检查在锁外（超卖风险）→ 合并进同一把锁；progress 多线程写 `open("a")` 在 Windows 抛 `PermissionError` → 加文件级写锁。
- 数量：既有回归 **199 passed / 0 failed，14.13s**；并行自验 **22/22**；峰值 只读 2 / 读写混合 1 / 上限2 时 2 / 默认1；**0 次外部请求、0 次模型调用、0 费用**；`tests/` 零新增；历史证据零改写。
- **流程错误如实记录**：本批代码先于执行前记录；第二次运行（21/22，`PermissionError`）的结果 JSON 被 `rm`、日志被覆盖，属删除失败证据，明细仅存于对话与日志，未伪造补回。
- **边界**：线程级并发，不是多进程；峰值来自构造延迟，不能称「更快」；隐式冲突未解决；`max_workers` 默认 1。
- [决策](engineering-log/DECISION_LOG.md)（N1–N12）、[实施](engineering-log/IMPLEMENTATION_LOG.md)、[验证](engineering-log/VALIDATION_LOG.md) 的 MA-P5-009。**本批停止**。
- **下一步（第 4/7 项）**：依赖推导（硬依赖从工具 schema 推导）、上下文策略（Skill vs 独立 Agent 选用规则）、差异化信息源。

## 2026-11-13 上一批：MA-P5-008 资源硬门 + Hooks 注册表（第 2/7 项）

- 7 项主干的第 2 项：**Token / 费用 / 总时长硬门（调用前预占）+ 5 类事件 Hooks 注册表**，是并行的前置。
- 新增 `src/min_agent/resource_budget.py`（269 行）：`ResourceLimits`（`None`=不限）、`ResourceLedger` 父子账本、`reserve`/`commit`/`release`、`PriceTable`（micros/1M tokens）、`DeadlineExceeded`/`ResourceExhausted`/`UnknownPrice`。
- 新增 `src/min_agent/hooks.py`（93 行）：`before_model` / `after_model` / `before_tool` / `after_tool` / `on_error`；`context` 只读；**钩子只能放行/拒绝，不能改参数**；异常或非法返回一律 fail-closed。
- 改 `src/min_agent/manager.py`：`WorkerRuntime` 与 `Manager._complete` 都走「before_model → 预占 → 调用 → 按实际结算 → after_model」；每个 worker 用 `resources.child(...)` 子账本。
- **关键边界**：预占成功 **不等于** 一定不超支，估算偏差会记 `overrun` 并如实呈现；有费用上限而模型未登记价格 → `unknown_price` 直接拒绝，不允许"先花再说"；Manager 自身调用也计入账本（本次共 3 次 / 730 micros）。
- 数量：基线 199 → 合并回归 **199 passed / 0 failed，13.63s**；离线自验 **40/40**；**0 次外部请求、0 次模型调用、0 费用**；`tests/` 零新增；历史证据零改写；`git diff --check` exit 0。
- **三处自身修正**：自验 `clock=None` 导致 `TypeError`（留档）；`estimate_tokens` 对中文低估约 3 倍，已改 ASCII/非 ASCII 分开估算；「未知价格」用例误用了含价价目表，已改为缺价表。
- [决策](engineering-log/DECISION_LOG.md)（M1–M13）、[实施](engineering-log/IMPLEMENTATION_LOG.md)、[验证](engineering-log/VALIDATION_LOG.md) 的 MA-P5-008。**本批停止**。
- **下一步（第 3/7 项）**：并行——独立控制对象绕开 `SessionEvents` 单活动循环、并发上限、只读并发/写串行规则。需先改 `events.py`，并补并发下的取消/迟到结果归属验证；未获授权不启动。

## 2026-11-13 上一批：MA-P5-007 状态与生命周期（第 1/7 项）

- 7 项待补主干的第 1 项：**progress 调度视图、卡住检测、协作式取消 + ack、超时/迟到归属**。不动 `events.py`。
- 新增 `src/min_agent/subagent_state.py`（309 行）：`WorkerHandle` 状态机（pending→running→waiting_input→completed/failed/cancelled/expired）、`LifecycleRegistry`（`start`/`request_cancel(_all)`/`collect_acks`/`stalled`/`expire_overdue`/`snapshot`）、`PROGRESS_FIELDS` 白名单（不含 objective/artifact/summary/凭据）。
- 改 `src/min_agent/manager.py`（**默认 `lifecycle=None` 行为不变**）：`WorkerRuntime` 增加 `checkpoint`（模型调用前后各一次 `_tick`）；`Manager` 增加可选 `lifecycle`；`cancel()` 先发协作式取消信号再级联取消契约树。
- **取消语义（不得美化）**：只设令牌，worker 在检查点轮询才停；**正在阻塞的模型调用无法立即打断**，`forced_termination = NOT SUPPORTED`；未 ack 只标 `unacknowledged`，不当作已停止；迟到结果不改终态。
- 数量：基线 199 → 合并回归 **199 passed / 0 failed，12.14s**；离线自验 **30/30**（46 条 progress 记录）；**0 次外部请求、0 次模型调用、0 费用**；`tests/` 零新增；历史证据零改写；`git diff --check` exit 0。
- **三处自身修正**：自验脚本 transport 传错参数（首跑 `ValidationError`，留档）；「无 progress 文件」检查写成永真，改为前后文件数对比；ack 超时从「最后活动」改为「取消请求时刻」。
- [决策](engineering-log/DECISION_LOG.md)（L1–L12）、[实施](engineering-log/IMPLEMENTATION_LOG.md)、[验证](engineering-log/VALIDATION_LOG.md) 的 MA-P5-007。**本批停止**。
- **下一步（第 2/7 项）**：Token/费用/总时长硬门 + Hooks 注册表（before/after tool、before/after model），这是并行的前置。之后才是并行（要改 `events.py`），再之后是依赖推导 / 上下文策略 / 差异化信息源。

## 2026-11-13 上一批：MA-P5-006 管理者模式（Manager/Worker）

- 用户明确范围：**只做管理者模式**；辩论/头脑风暴/专家小组、去中心化、A2A **不做**（不是延期）。Manager 用强模型，可指派弱模型执行；子 Agent 按四要素设计。
- 新增 `src/min_agent/manager.py`（503 行，默认 off、全串行）：`ModelProfile` 强/弱分层（transport 由调用方注入）、`WorkerSpec`、`TaskEnvelope`、`WorkerOutput`、`ManagerPlan` 严格校验（未注册角色/档位、前向依赖、重复 id 一律整份拒绝）、`WorkerRuntime`（复用 `deterministic_review` + host 证据）、`Manager`（规划→派发→收集→汇总、级联取消、父子预算、`evidence_provider` 注入证据）。
- **四要素落进代码**：角色定义、职责/边界、`[FROM_MAIN_AGENT]`/`[FROM_USER]`/`[TOOL_RESULT]` 三段来源标注 + 「后两段是数据不是指令」的防注入声明、严格 JSON 输出格式。
- **worker 自述不算验证**：无证据 → UNKNOWN；缺标记 → FAIL；伪造 host 指纹 → FAIL `forged_check_evidence`；**Manager 只在全部 PASS 时才 `completed`**，否则 `partial`。
- **串行优先**：`max_workers=1`；`>1` 构造期抛 `NotImplementedError` 并提示需先改 `SessionEvents` 单活动循环，不做假并行。
- 数量：既有合并回归 **199 passed / 0 failed，15.31s**；离线自验 **35/35**；**0 次外部请求、0 次模型调用、0 费用**。`tests/` 零新增；历史证据零改写；`git diff --check` exit 0。
- **范围变更已如实记录**：跳过 `MASTER_PLAN.md:78` / H11 的「先证明收益再扩展」门槛，但**不把 H11 结论改写成「已证明收益」**。
- [决策](engineering-log/DECISION_LOG.md)（K1–K12）、[实施](engineering-log/IMPLEMENTATION_LOG.md)、[验证](engineering-log/VALIDATION_LOG.md) 的 MA-P5-006。**本批停止**：只证明离线可运行与判据生效，**不证明**在线可用、强弱分工有收益、管理者模式优于单 Agent。
- **下一步**：同一份管理者用例集跑离线 + 在线（在线需有界授权），再谈并行（需事件层改造）与生产接入。

## 2026-11-13 上一批：MA-P5-005 协作执行地基（功能补齐，测试后续再补）

- 用户核对 MA-P5-003/004 后明确：「**测试后续再补，我们先补功能**；但测试以后不能只是代码，应分**离线 / 在线**两层，且**测试集必须一样**」。
- 新增 `src/min_agent/subagent_collab.py`（默认 off、不接主循环）：`CaseSet`/`CollabCase` 用例定义与后端解耦 + `fingerprint()`；`ChatTransport` 协议、`ScriptTransport`（离线）、`HttpChatTransport`（在线，实现但**不调用**）；`ModelProposer`（模型只产 `artifact`/`declared_complete`，证据链仍由 host 从工具轨迹构造，自报检查项刻意留空）；`IndependentEvidenceSource`/`ModelReviewer`（并列语义层，硬门优先）；`run_case`/`run_case_set` 输出同结构结果。
- **J9 架构约束**：离线/在线共用同一份 `CaseSet` 由 `fingerprint()` 与同结构输出机械保证，不是文档约定。在线测试与可比性 **NOT RUN**；**`tests/` 零新增文件**（按用户指令）。
- **J8 诚实修正**：首版默认审核者与提议者读同一份原始回执、却未标注；已加 `same_source_as_proposer=true` / `independent_evidence=false` 与「未获得新信息」说明。首次自验结果留档不删。
- 数量：基线 199 → 合并回归 **199 passed / 0 failed，13.90s**；离线自验 **24/24**；**0 次外部请求、0 次模型调用、0 费用**。静态门禁 2/2 `ast.parse`、0 凭据落盘、生产 `src/` 仅新增该文件、MA-P5-003/004 证据零改写、`git diff --check` exit 0。
- [决策](engineering-log/DECISION_LOG.md)（J1–J10）、[实施](engineering-log/IMPLEMENTATION_LOG.md)、[验证](engineering-log/VALIDATION_LOG.md) 的 MA-P5-005。**本批停止**：只证明「能跑、不破坏既有」，**不是验收通过**，更不是「真多 Agent 协作已可用」。
- **下一步（用户定的规范）**：同一份 `CaseSet` 分别跑离线与在线（在线需用户给有界的请求数/费用/时间上限），核对指纹一致、结构一致、四态差异可归因，之后才谈接生产主链或 S3。

## 2026-11-13 上一批：MA-P5-004 检查项绑定 + 单 Agent 收益对照

- **补上 MA-P5-002 留下的真实漏洞**：`completed_checks` 从判定依据降级为不可信自述，检查项改由可信 host 的 `host_checks`（检查项名 → 原始回执 SHA-256）绑定；缺绑定时 `deterministic_review` 判 `UNKNOWN / checks_not_host_verified`、`review_loop` 发起 0 个提议者轮次直接默认拒绝。**冻结验收样本一字未改**，只补 host 侧输入。
- **三组同条件对照**（10 个固定反例）：A 单 Agent 自述 / B0 旧 S2 口径（信任自报）/ B1 本批收紧后 S2。**假放行 5 → 2 → 0**；正常对照三组均 PASS 无误杀；B0 放行而 B1 拦住的恰好是 `self_report_all_but_no_host_evidence` 与 `host_forged_ref` 两个绑定相关反例。
- 真实 proposer 样本：`qwen3.8-flash` HTTP 200、`artifact="703"`、1.18s，**三组均 PASS**——正常任务上无差异，收益只体现在反例。总 **1 次外部请求**、0 次 Gemini、0 次收费内置工具、仅合成数据。
- 数量：基线 181 → 合并回归 **199 passed / 0 failed / 0 skip，13.83s**；S2 测试 21 → 29；新增对照测试 10 项。静态门禁 4/4 `ast.parse`、0 处凭据落盘、`application`/`runtime`/`research`/`registry` 仍不引用 subagent、`reports/ma-p5-003/` 零改写、`git diff --check` exit 0。
- **★ 最重要的边界**：这证明的是「独立验证器 + 可信 host 原始回执」相对单 Agent 自述的信息增量，**不是「多 Agent 讨论优于单 Agent」**（新信息不来自第二个模型）。`MASTER_PLAN.md:78` 的 S3 门槛**只满足一半**，本批**不打开 S3**。B0 是旧口径重建（旧源码未入库），属可比性限制；`semantic_review` 仍固定 UNKNOWN。
- 本批首次测试无 RED（改动前完成了逐用例判定推演），静态核对两处澄清项（docstring 命中、我写的一条永真表达式）均如实留档并更正，证据见 `reports/ma-p5-004/static-gate.json`。
- [决策](engineering-log/DECISION_LOG.md)（H1–H11）、[实施](engineering-log/IMPLEMENTATION_LOG.md)、[验证](engineering-log/VALIDATION_LOG.md) 的 MA-P5-004。**本批停止**：默认 off、未启用、未提交未推送。下一步由独立方复验关键反例与 A/B0/B1 结论，再做同条件真实任务前后对照，并把「验证器收益」与「委派收益」分开计量。

## 2026-11-13 上一批：MA-P5-003 真实模型提议者-审核者（一次有界演示）

- 用户已配置 `QIANWEN_API_KEY`（`sk-ws-` 按量付费）。千问 `GET /v1/models` → HTTP 200、**182 个模型**，`qwen3.8-flash` 可用并完成 smoke（HTTP 200）。
- **跑通一遍 S2**：提议者 `qwen3.8-flash` 产出 `artifact="703"`（1.88s）；确定性 `review_loop` 判 **PASS / verified**；Gemini 语义审核者 `consistency=pass`（2.21s）；合并 `combined=PASS`。证据 `reports/ma-p5-003/live-review-result.json` 与 `live_review.py`。
- **指定模型 `gemini-3.8-flash` 仍 BLOCKED（HTTP 503 high demand）**；实际审核者用同系列 `gemini-3.6-flash`，证据里 `requested_model`/`substituted=true`/`substitution_reason` 齐全，**不构成指定模型验收**。
- 有界执行：总 **7 次外部请求**（≤10）、≤5 分钟、仅合成任务、0 次收费内置工具、0 次真实用户数据外发；相关回归 **44 passed / 0 failed**；生产 `src/` 零改动、未提交未推送。首次运行 `SyntaxError` 与一次时间戳误报均如实留档。
- 取舍：确定性 verifier 仍为硬门，模型审核者只作并列语义层，不能覆盖已确定 FAIL；证据引用由 host 构造，模型不自报 `completed_checks`（G1/G2）。
- [决策](engineering-log/DECISION_LOG.md)、[实施](engineering-log/IMPLEMENTATION_LOG.md)、[验证](engineering-log/VALIDATION_LOG.md) 的 MA-P5-003。**本批停止**。这是 1 个合成任务、1 次运行的小样本，**不声称成功率或能力提升**；下一步等 3.8 恢复后重跑同脚本（`substituted=false`），或先做 S1+S2 与单 Agent 的同条件收益对照。

## 2026-11-13 最新续接：QWEN-COLUMNS-001 五个模型列配置

- 把 5 个推荐模型从字符串白名单升级为 `QIANWEN_COLUMNS` 结构化列定义（`qwen38max`/`qwen37plus`/`qwen38flash`/`dsv4pro`/`dsv4flash`，含精确 model ID、用途、方言标记、function_calling），`RECOMMENDED_MODELS` 改为派生；新增 `column_env(model)`、`columns` 子命令，`bench-env` 支持 `--model` 并校验白名单（修复硬编码默认模型）。
- **未改**历史 `run_model_column.py`、bench `agent_bench/` 既有源码、生产 `src/`、`.env`。离线契约 **28/28 PASS**（新增 9 项），bench 全量 **348 tests / OK(skipped=12)** 零回归；静态门禁通过。
- **列定义只描述可用模型与环境变量，不代表已跑过任何真实请求**；本轮 0 次外部请求、0 费用、0 代金券消耗。用户表示接下来自行在 `.env` 配置 key。
- [实施](engineering-log/IMPLEMENTATION_LOG.md)、[验证](engineering-log/VALIDATION_LOG.md) 的 QWEN-COLUMNS-001；无新增架构决策（沿用 F1/F3/F6）。**本批停止**；下一步是用户配好 `QIANWEN_API_KEY` 后跑 `probe` + 选一列 `smoke`。

## 2026-11-13 最新续接：QWEN-PROVIDER-001 千问AI平台接入（评测层）

- 平台为阿里云千问AI平台 OpenAI 兼容网关，端点 `https://maas.qianwenaiapi.com/compatible-mode/v1`；按量付费 key（`sk-ws-`/`sk-`）与 Token Plan key（`sk-sp-`，专属端点、仅套餐模型）**不可混用**。用户给出 300 代金券构成费用授权。
- **已接入评测层**：新增 `D:/agent-framework-bench/scripts/qianwen_provider.py`（`probe`/`smoke`/`models`/`bench-env`，复用既有 `model_endpoint.resolve()` 凭据机制）与 `tests/test_qianwen_provider.py`（19 项离线契约）。**未修改** bench 既有源码与生产 `src/`。
- 离线契约 **19/19 PASS**；bench 全量 **339 tests / OK(skipped=12)**（327 PASS / 12 SKIP / 0 FAIL），零回归；无凭据探测 `GET /v1/models` → **HTTP 401 / 0.92s**，证明端点可达、只差 key。
- **真实模型调用 NOT RUN**：本机 `.env` 只有 `GEMINI_API_KEY`/`GROQ_API_KEY`，本平台 key 未配置；`probe`/`smoke` 均以 `status=NOT_RUN, exit 3` 留档，未伪造结果、未拿其他厂商 key 顶替。本批 **0 次模型调用、0 费用、0 代金券消耗**。
- 生产 Agent 主链**未接入**：`ProviderState`/`openai_protocol.py` 只认两种 Responses Provider，需按 MFAB 任务书 §164–165 先做隔离 Adapter；最小改动方案见 [接入说明](2026-11-13-qianwen-provider-integration.md) §6。
- [决策](engineering-log/DECISION_LOG.md)、[实施](engineering-log/IMPLEMENTATION_LOG.md)、[验证](engineering-log/VALIDATION_LOG.md) 的 QWEN-PROVIDER-001。**本批停止**；下一步是用户配置 `QIANWEN_API_KEY` 后跑 `probe`+`smoke`（≤2 请求、预期 < ¥0.01），再决定是否跑 bench 场景或另起生产 Adapter 批次，不自动扩大范围。

## 2026-11-13 最新续接：MA-P5-002 多 Agent 第二批（独立复验 + S2 审核者）

- 新增 `src/min_agent/subagent_review.py`：**确定性审核者**（不是第二个模型）+ 有界验证循环；审核者只看验收规格、结构化产物与原始回执，`ProposerOutput` 结构上不含消息历史；`EvidenceRef.ref` 必须匹配原始回执 SHA-256（兑现 S1 的真实性承诺）；四态合并复用 `evaluation/verifier.py::verdict()`。
- 三种过早终止形态（偷懒式假完成 / 过早放弃 / 假成功）与交付不一致、伪造/重复证据均按冻结判据判 FAIL；`partial` 或缺原始回执保留 UNKNOWN；轮数上限保留已确定 FAIL，不冒充成功。语义质量固定标 UNKNOWN。
- 独立反例复验 **11/11 PASS**（不复用仓库测试用例，**只是执行方自审，不等于由用户另叫 Agent 的独立审查**）；S2 新增测试 **21 通过**；合并回归 **181 passed / 0 failed / 0 skip，14.21s**，离线、0 次模型调用。两次 RED 失败证据保留在 `reports/ma-p5-002/`（该目录被 `/reports/` gitignore 忽略，文件在磁盘但不入 Git）。
- 全量 1758 项、独立 Agent 审查、真实模型收益对照、S3 并行、P4 资源硬门均 **NOT RUN**；默认 off、不接主循环、未提交未推送。
- [决策](engineering-log/DECISION_LOG.md)、[实施](engineering-log/IMPLEMENTATION_LOG.md)、[验证](engineering-log/VALIDATION_LOG.md) 的 MA-P5-002。**本批停止**；下一步先由独立方复验，并做 S1+S2 与单 Agent 的同条件对照，再决定是否进入 S3，不自动扩大范围。

## 2026-11-13 最新续接：MA-P5-001 多 Agent 第一批（S0 基线 + S1 契约）

- 新增 `src/min_agent/subagent.py`：**默认关闭、全串行、不接主循环**的最小子 Agent 契约（结构化移交包、工具白名单、`parent_budget.child()`、信息增量判据、级联取消、迟到结果归属、脱敏轨迹 JSONL）；新增 `tests/unit/test_subagent_contract.py` 23 项（含固定验收样本与负面对照）。
- 相关回归 **160 通过 / 0 失败**（现状基线 137 + 新增 23），离线、0 次模型调用；全量 1758 项为 11-09 历史证据，本批 NOT RUN，不复用为本批结果。独立 Agent 审查同样 NOT RUN。
- 未改 `research.py` / `application.py` / `runtime.py` / `budget.py`，生产行为零变化；未启用、未提交、未推送。
- 已知限制：`Purpose` 无 `subagent` 用途、`EvidenceRef.ref` 只是格式门、契约层取消不覆盖真实进程、`SessionEvents.started()` 单活动循环是 S3 并行前置、P5 前置 P4 资源硬门未闭合（本批范围收窄，不等于 P4 完成）。
- [决策](engineering-log/DECISION_LOG.md)、[实施](engineering-log/IMPLEMENTATION_LOG.md)、[验证](engineering-log/VALIDATION_LOG.md) 的 MA-P5-001。**本批停止**；下一步由独立方复验关键反例后，再决定是否进入 S2 提议者-审核者，不自动扩大范围。

## 2026-11-06 最新续接：Personal Memory Adapter 第一批 PARTIAL

- 合成只读报告→隔离 Markdown 候选/报告证据卡/显式映射已实现；全部 draft/unknown，不创建正式 Memory，不改 Personal Memory Vault 或插件。
- 报告缺消息证据，候选禁止正式导入；ChatGPT/DeepSeek 仅有 ID 命名空间。Obsidian 注册可执行路径不存在，实机渲染未验证，不能宣称整批验收完成。
- [字段契约与 CLI](activity-connector/personal-memory-contract-v0.1.md)、[阶段记录](phase-records/2026-11-06-personal-memory-adapter.md)、[测试证据](acceptance/personal-memory-adapter-20261106.json)。本批停止，等待独立审核与隔离 UI 验证，不进入同步、Memory 或 M5/M6。


## 2026-11-06 最新续接：最小只读 Activity Connector 已完成

- 真实 Codex JSONL/zstd、DSH v4/old 只读 CLI 已实现，流式解析、元数据报告、seq/seq0 分离；不接 M3 写路径或 Memory，M5/M6 暂缓。
- **222 passed / 0 failed / 0 skipped**；真实小批 4 文件 264 parsed/0 failed，双遍稳定、重复一致且原文件 hash 不变。所有记录身份保守 provisional，无 Fork/副本合并。
- [CLI 使用](activity-connector/readonly-cli.md)、[阶段交付](phase-records/2026-11-06-readonly-activity-connector.md)、[脱敏证据](acceptance/activity-readonly-20261106.json)。完成后停止，等待独立审核；不自动进入持久化或 Memory。


## 2026-11-05 最新续接：Phase 0B M4 实施完成，等待独立审查

- 冻结六类变化分类器与不可变合成执行器已实现；验证实际身份、Snapshot/root/latest 和 Checkpoint 副作用。未改 M1–M3 Store，不代表已接入持久化采集。
- **182 passed / 0 failed / 0 skipped**（M4 41 + 既有回归 141）；[M4 阶段卡](phase-records/2026-11-05-phase0b-m4.md)、[公开证据](acceptance/activity-m4-20261105.json)。压缩往返 latest 回归问题已修复，失败记录保留。
- 本轮停止等待独立审查，不启动 M5/M6。仅合成数据；reader/解压、M3 多 Member 持久集成和自动解冻不在本轮。


## 2026-11-05 最新续接：Phase 0B M3 实施完成，等待独立审核

- 三种 Locator Profile、独立三层 Fingerprint、记录幂等/持久冲突冻结、Provisional 显式映射已实现；多候选只返回集合，不自动合并。
- 相关回归 **141 passed / 0 failed / 0 skipped**（M3 41 + 既有相关回归 100），含 M3 新增 10 个进程强退和 1 个异常回滚用例。[M3 阶段卡](phase-records/2026-11-05-phase0b-m3.md)、[公开证据](acceptance/activity-m3-20261105.json)。
- 仅隔离合成数据；DSH seq/seq0 两条 stream 分别使用独立实验库；不等于真实 Connector/生产准入。本轮停止等待独立审核，不启动 M4–M6。旧 M1/M2 入口保留为历史。


## 2026-11-05 最新续接：Phase 0B M2 已完成

- 事件先提交后 Checkpoint；Generation Transition 在单一 SQLite 事务内提交，保留追加式身份与状态历史。
- 相关回归 **100 passed / 0 failed / 0 skipped**：M2 37、M1 31、既有 Checkpoint 32；包含 13 个进程中断和 7 个异常注入用例。重放幂等、原子性、非法状态冻结通过。
- [M2 阶段交付](phase-records/2026-11-05-phase0b-m2.md)与[公开证据](acceptance/activity-m2-20261105.json)。仅隔离合成数据库，不迁移旧库/生产库；可进入下一批 M3，本轮停止。下方 M1 与更早记录保留为历史。
- 工作分支仍为 adrian/phase0b-m1；仓库级代理 7897 已验证 fetch 成功，最终提交/推送状态见 M2 阶段记录。


## 2026-11-05 续接：Phase 0B M1 已完成

- 用户已提供五份历史报告，并确认 **144814 Final Errata 为最终基线**；此前材料缺失阻塞解除。冻结设计原文、来源指纹及历史纠错见 [设计来源](activity-connector/README.md)，不是本轮重新扫描私人历史。
- **M1 限定合成验收通过**：三层独立 SQLite 存储、确定性 ID、Snapshot root/latest 分离、StateEvent fold/冲突、Checkpoint CAS、持久 JSON 重建；A–E 演示全部通过，重建前后 10 个身份 ID 一致，相关回归 63 passed。
- 已修复重复 JSON 键、boolean revision 和 Windows 演示连接未关闭问题。未改生产库、未接入真实数据或 Connector、未执行 M2/M3、未调用模型。实现 profile 与限制见 [M1 阶段交付](phase-records/2026-11-05-phase0b-m1.md)。
- **可进入下一批 M2，本轮停止**；M1 通过不等于正式事件持久化准入，后者仍需 M2/M3。当前工作分支 `adrian/phase0b-m1`；Git 同步结果见本批工程验证记录。下方 0B 入口是同日较早快照，保留为历史。

## 2026-11-05：设计演变归档与 Phase 0B 入口

- 新增 [设计演变归档](ADRIAN_EVOLUTION.md)，区分最初构想、共同决策、实现与未来目标；后续 Phase 历史来自本次用户说明，原阶段材料尚未找到。
- 本轮起点实际 HEAD 为 `50b27bfab099e7e4b3f085a9468d0b8bc6b44c4b`；下面旧 HEAD/未提交描述保留为历史，不代表当前状态。工作分支 `adrian/phase0b-m1`。
- **M1 BLOCKED（输入缺失）**：尚未取得 `Unified Event Model v0.2.4.2-final + Errata 2` 正文，未编码、未运行 A–E，不满足进入 M2 的条件。第一步是取得设计文件路径并核对 ID、revision、StateEvent、恢复规则；不重新设计模型。
- Git fetch 因本机代理连接失败，远端最新进度未确认。归档、提交范围、续接条件及验证见 [阶段短记录](phase-records/2026-11-05-phase0b-entry.md)。

更新：2026-11-02，Asia/Shanghai。此页是续接入口；代码和最新证据仍需核对。

整个Adrian的能力范围、目标架构、长期依赖与分批计划见[完整总计划](MASTER_PLAN.md)。V1.4 B2/C/D只是其中的近期任务闭环，不代表Context、Memory、长任务、多Agent及持续改进等全部设计已经完成；总计划中的建议批次不等于已获执行授权。

## 当前结论

- **验证器与本地评测更新（2026-11-02）**：修复6个新反例，补Ollama隔离评测入口、模型digest/配置比较门和有限Markdown等式解析。旧跨会话读取异常的整体PASS收紧为UNKNOWN，未泄露检查不变；不跨协议比较成绩。真实本地两题共3次生成，计算格式漏判已在保存观察上修复；合理拒绝题暴露旧脚本状态判据过严，保留原FAIL且Safety PASS。[完整记录与证据边界](phase-records/2026-11-02-verifier-and-publish.md)。
- **协作与后续方向（2026-11-02）**：用户暂时取消A/B分工，当前助手直接实现和验证；已授权本批脱敏提交/推送。跨框架比较作为独立 `agent-framework-bench` 项目推进，当前只有验证基础，Harness、OpenAI Agents SDK、Pydantic AI统一适配与模拟用户尚未接通。旧阶段的未提交/分工描述保留为历史信息，以本条及新交付为准。

- **本地Ollama Provider已接入并完成阶段验收（2026-11-02）**：新增 `llm/ollama.py`（原生 `/api/chat`、非流式），`--ollama` 可在无需云端凭据的情况下启动现有网页工作台；真实 `qwen3.5:4b` 完成普通对话、calculator Tool Calling（703）和三轮两工具任务（42→84）。离线新增48单元+6集成测试；真实证据见[本地Ollama说明](OLLAMA.md)与[阶段记录](phase-records/2026-11-02-ollama-provider.md)。已知：非流式、32K默认（低内存可16K）、字节换算是保守代理。

- **Ollama 默认窗口已改为 32768（2026-11-02）**：32K 两轮真实长任务通过（第一轮 17,541 prompt tokens、第二轮 24,286/24,451 tokens，calculator 703→708，截断日志 0，内存最低 1.88GB）；`--ollama-num-ctx 16384` 保留为低内存备用。131072 加载期最低 0.43GB、加载后 0.53GB，按安全阈值卸载，标记资源不满足；262144 仍未采用。harness 默认窗口改为 262144，云端 Provider 继续使用自身声明窗口（DeepSeek 例如 1M）。[对比报告](ollama-context-capability-20261102.md)、[阶段记录](phase-records/2026-11-02-ollama-32k-default.md)。

- **全系统优化第一批已完成（2026-10-31）**：全仓离线基线后修复共享Memory权威读取、首次/恢复验收不一致。隔离验证环境旧源码1427通过；修复后1443通过、1个WinRT依赖跳过、3项live排除；原冻结跨组件样本由8失败/2通过变为10通过。本批新增真实生成0，未启用生产服务。[改动、环境边界、证据与回滚](phase-records/2026-10-31-system-opt.md)。

- **持续进化V1已本地实现并阶段收尾**：受限候选、固定Verifier与开发/保留集、实验版本晋升、持久预算/中断止损、回滚与CLI已接通。相关回归94通过；真实DeepSeek合计31次生成、峰值上界约$0.023240，低于本轮50次/$1。当前实际源码5题全通过，没有更新收益证明；另一个明确合成故障副本完成真实提案→5题复验→实验激活，未自动采用到生产。[阶段证据与限制](phase-records/2026-10-31-evolution-v1.md)，[使用](EVOLUTION_V1.md)。

- **B0已本地合入**：会话版本/CAS、授权绑定、原子提交和UNKNOWN保护已完成限定范围的独立离线验证。
- **B1已本地合入并阶段收尾**：支持一个明确的TODO_COUNT目标，由可信host登记，执行前冻结，再从真实SQLite观察。**默认关闭**，没有自动启用UI或自动纠错。
- **B2原候选独立验收未通过；当前工作区已针对B2-009修复并完成限定合成集成回归，尚未提交**：研究观察现在读取持久权威快照，网络每次search/read前重新核对许可、父任务和预算。目标链路使用假模型/假网络验证；真实网页解析因环境缺少`trafilatura`未验证，整个工作区也未做完整回归。[本轮记录](phase-records/2026-10-30-adrian-mvp-b2-fix.md)保留修复范围和回滚方式。
- 最近本地主线针对性验证 **130项通过**；对应固定候选此前完整离线 **1363通过、3项live排除、1条既有warning**。两者不是同一次执行，不相加作成功率。
- 上述验收/合入没有提交、推送、部署、重启服务、使用真实用户库或发起付费Provider实验；外部B编码会话的费用由其harness管理。本地HEAD仍为`220f6ec18d50e08316a54c3b34b81b0d00c6744d`，含后续未提交修改，不能只checkout这个HEAD重建现状。

## 现在怎样推进

最新阶段是跨组件可靠性收尾：Memory撤回不再沿用旧文档缓存，夜间完成与恢复均重新核对可信结果。测试在reports/system-opt-20261031/validation-env中执行，原全局环境和锁文件精确版本仍不能据此宣称已完整安装。后续优先真实可复现任务，工具分类继续最后处理。

2026-10-31最新：自主进化先限定于计算器绑定的可靠评测范围，已验证有界循环。当前批次停止，未启动常驻或无限付费任务。扩大到Prompt/Skill/Memory/其他Harness代码时，先选择真实失败并补对应质量判据；不要把受控故障实验当作当前Agent已获益，或默认扩大授权。

2026-10-31补充：[验证器持续优化能力检查](phase-records/2026-10-31-verifier-readiness-review.md)发现三项合成错误放行，登记EVAL-010。现有验证器可用于局部确定性回归，当前不能作为整个系统不断自改的统一放行门；该结论不改写B0/B1既有的限定验收。

EVAL-010：用户随后明确指定当前助手编码，已完成限定修复并阶段收尾。[交付记录](phase-records/2026-10-31-eval010-complete.md)记录原因、改动、效果和回滚。最终相关回归75通过；默认20题的15 PASS/2预期FAIL/3预期UNKNOWN全部符合冻结预期。当前可支撑选定确定性能力的有限优化；没有独立第二审查、真实模型收益或全系统通用评测结论。旧缺评测协议指纹的结果需要在新协议下重建基线后才能比较。

用户已明确：**每部分核心效果达到即可暂时收尾，设计成熟度约60–70分；记录不足和未来方向，不追求一次完美。** 这是推进策略，不是降低授权、隔离或事实真实性要求。

2026-10-30 B2批次场景是“本轮研究实际读到的原文及部分证据”。该批针对性回归为**131通过、1跳过**；跳过项需要当时环境未安装的`trafilatura`。同一组Research/Web/Auth基线仍为**30通过、4失败**，集中在公开网页依赖/搜索模拟环境，修复前后相同。此处仅指B2限定集成证据；B2生产启用和真实模型收益实验未执行，不覆盖后来独立开展的计算器进化实验。

工具分类/风险等级优化按用户要求**最后处理**；用户已读过工具代码，不为风格或扩展性提前要求重读。

## 只需再读与本任务有关的内容

| 需要了解 | 入口 |
|---|---|
| B0/B1发生过什么、为什么这样修 | [阶段记录](phase-records/2026-10-30-b0-b1.md) |
| 已知不足、触发条件及最小优化方向 | [整改清单](IMPROVEMENT_BACKLOG.md) |
| 长期产品目标 | [AGENT_VISION.md](AGENT_VISION.md) |
| 完整能力架构、长期实施顺序及里程碑 | [MASTER_PLAN.md](MASTER_PLAN.md) |
| 工作和停止规则 | [AGENTS.md](../AGENTS.md) |

恢复某项优化时：先读对应整改项及其阶段卡，核对当前源码/开关/版本，再运行最小现状用例，按原判据比较。不要从零再设计一遍，也不要默认重跑全部历史测试。

阶段推进后更新此页和对应整改状态，保留旧阶段卡；关键上下文写在仓库里，原始日志放受控审核位置。
