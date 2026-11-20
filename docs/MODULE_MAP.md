# 模块地图

> 2026-11-15（PKG-LAYOUT-001）后，`src/min_agent/` **根目录只保留** `__init__.py` 与组合根 `application.py`，
> 其余 59 个模块按职责收进 11 个子包。门禁见 [`tests/unit/test_package_layout.py`](../tests/unit/test_package_layout.py)：
> 根目录多出任何 `.py`、或子包缺一句话说明，测试都会失败。
> 本文件是「要改某个东西该去哪个包」的第一入口；旧路径对照在文末。

## 子包职责

| 子包 | 职责 | 常见入口 |
|---|---|---|
| `contracts/` | 跨模块数据契约与基础设施：消息、trace、时钟、策略钩子 | `Message` / `ModelResponse` / `ToolCall`、`TraceRecorder`、`Clock`、`HookRegistry` |
| `ledger/` | 预算、资源与检查点账本；只记账，不执行业务动作 | `BudgetSegment`、`BudgetLimits`、`SessionBudgets`、`ResourceLedger`、`CheckpointPolicy` |
| `runtime/` | 一轮执行的引擎：薄主循环 + 阶段组件 | `Runtime`、`RunResult`；内部结构见下节 |
| `session/` | 会话、任务状态机、SQLite 持久化、幂等操作、执行归属 | `SessionStore`、`SQLiteStorage`、`TaskController`、`SessionEvents`、`ArtifactStore` |
| `context/` | 提示词上下文装配、状态栏投影、模型窗口声明、组件提示词段 | `ContextManager`、`ManagedModel`、`ContextLimit`、`StatusBuilder`、`SystemPrompt`、`context_window` |
| `compression/` | 历史压缩、证据选择与技能披露 | `Compactor`、`GroundedSelector`、`SkillCatalog`、`SkillRetentionSelector` |
| `memory/` | 记忆日志、记忆记录与会话状态表 | `MemoryJournal`、`SourceReference`、`SessionStateTable`；长期记忆仍是 `long_memory/` |
| `goals/` | 公开目标绑定、反馈投影与进展评分 | `PublicGoalRegistry`、`PublicFeedbackProjector`、`ProgressScorer`、`TodoObserver` |
| `agents/` | 多 Agent 编排：管理者、worker、契约、邮箱与依赖图 | `Manager`、`WorkerRuntime`、`MultiAgentService`、`PointToPointMailbox`、`SubagentContract`、`TaskStore` |
| `research/` | 研究服务与观察投影 | `ResearchService`、`ResearchObservationStore` |
| `mcp/` | MCP 服务器配置、stdio JSON-RPC 客户端、按需连接与工具并入 | `MCPServerCatalog`、`StdioMCPClient`、`connect_mcp`、`register_mcp_tools` |
| `notify/` | 通知、桌面通知与定时调度 | `Notifications`、`Scheduler`、`DesktopChannel` |
| `activity/`（**WIRING-004 已删除**） | 历史：Windows 活动采集：身份、存储、变更与记忆适配 | 已删除，命令不再可用 |
| `tools/` `llm/` `web/` `evaluation/` `long_memory/` | 既有子包，本次未重排 | 工具、模型适配器、Web 入口、评测、长期记忆 |

## `runtime/` 内部结构（RUNTIME-SLIM-001）

| 文件 | 职责 | 不做什么 |
|---|---|---|
| `loop.py` | `Runtime` 类、构造校验、会话任务闭合、约 20 行主循环 | 不判断错误、不拼工具消息 |
| `state.py` | `RunResult`、`Model`、`RunState`、`ModelRound`、`ToolRequest` | 不调用模型、不执行工具 |
| `stages.py` | `ControlGate` → `RoundLimit` → `ReserveGate` → `ModelCall` → `ResponseRouter` | 不执行工具、不做收尾 |
| `tool_loop.py` | 工具批次：门控、执行、错误映射、进度纠错、`propose_goal` | 不组装提示词、不写会话存储 |
| `errors.py` | trace 错误码映射 + 所有工具结果/错误消息构造（纯函数） | 不读全局状态 |
| `prompt.py` | `PromptView`：系统提示词 + 历史副本 + 可刷新状态尾部 | 不缓存、不写历史 |
| `closing.py` | `CheckpointCloser`：软阈值收尾，最多一次模型调用 | 不执行任何工具 |
| `progress_guard.py` | 重复调用检测（pause/correct） | 不决定状态码 |
| `streaming.py` | 会话级流式显示缓冲与模型包装 | 不改变 Runtime 拿到的完整响应 |

主循环的判据固定在 [`tests/unit/test_runtime_shape.py`](../tests/unit/test_runtime_shape.py)：
≤ 35 行、无 `try/except`、无 `match`、≤ 2 个 `return`。

## 旧路径 → 新路径

迁移是「纯搬家」：模块内容不变，只换位置与导入路径。历史 `docs/` 中的旧路径不追改。

- `models` → `contracts.models`；`trace` → `contracts.trace`；`clock` → `contracts.clock`；`hooks` → `contracts.hooks`
- `budget` → `ledger.budget`；`budget_persistence` → `ledger.budget_persistence`；`resource_budget` → `ledger.resource_budget`；`checkpoints` → `ledger.checkpoints`
- `progress_guard` → `runtime.progress_guard`；`streaming` → `runtime.streaming`
- `sessions` → `session.store`；`storage` → `session.storage`；`task_state` → `session.task_state`；`operations` → `session.operations`；`artifacts` → `session.artifacts`；`events` → `session.events`
- `context` → `context.manager`；`state_bar` → `context.state_bar`；`model_context` → `context.model_context`；`structured_context` → `context.structured_context`
- `compaction` → `compression.compaction`；`compression_evidence` → `compression.evidence`；`compression_text` → `compression.text`；`compression_selector` → `compression.selector`；`compression_summary` → `compression.summary`；`skills` → `compression.skills`；`skill_retention` → `compression.skill_retention`
- `memory` → `memory.journal`；`memory_records` → `memory.records`；`memory_state` → `memory.state`
- `public_feedback` → `goals.public_feedback`；`public_goal_registry` → `goals.public_goal_registry`；`public_goal_adapter` → `goals.public_goal_adapter`；`progress_scoring` → `goals.progress_scoring`；`todo_observation` → `goals.todo_observation`
- `manager` → `agents.manager`；`multi_agent_service` → `agents.multi_agent_service`；`agent_messages` → `agents.agent_messages`；`context_policy` → `agents.context_policy`；`subagent` → `agents.subagent`；`subagent_review` → `agents.subagent_review`；`subagent_state` → `agents.subagent_state`；`subagent_collab` → `agents.subagent_collab`；`parallel` → `agents.parallel`；`task_graph` → `agents.task_graph`
- `research` → `research.service`；`research_observation` → `research.observation`
- `notifications` → `notify.notifications`；`desktop_notifications` → `notify.desktop_notifications`；`scheduler` → `notify.scheduler`
- `activity_identity` → `activity.identity`；`activity_records` → `activity.records`；`activity_store` → `activity.store`；`activity_m2` → `activity.m2`；`activity_m3` → `activity.m3`；`activity_changes` → `activity.changes`；`activity_reader` → `activity.reader`；`activity_connector` → `activity.connector`；`activity_memory_adapter` → `activity.memory_adapter`（**WIRING-004 已删除整个 `activity/` 包，以上仅历史映射**）

唯一保留的兼容门面是 `min_agent.runtime`：`from min_agent.runtime import Runtime, RunResult, Model` 仍可用，
其余子包 `__init__.py` 只放一句话说明、不导入子模块（避免包级循环导入）。

## 对外可见变化

- `python -m min_agent.activity_connector` / `-m min_agent.activity_memory_adapter`
  → `python -m min_agent.activity.connector` / `-m min_agent.activity.memory_adapter`（**WIRING-004 已删除 `activity/` 包**，以上入口均不可用，仅历史说明）。
- `min_agent.web`、`min_agent.evaluation`、`min_agent.application` 等入口路径不变。
- 新增可选 `--mcp-config path.json`（MCP-001）；不提供时不启动任何 MCP 子进程，默认行为不变。
