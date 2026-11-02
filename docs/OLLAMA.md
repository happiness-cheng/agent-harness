# 本地 Ollama Provider（qwen3.5:4b）

更新：2026-11-02，Asia/Shanghai。本文记录本次接入的设计、协议映射、实测证据与限制；不把 Mock 测试当成真实模型验收。

## 1. 范围与结论

- 目标：在现有 `agent-harness` 里增加一个可独立切换的 Ollama Provider，让本地 `qwen3.5:4b` 成为 Agent 的决策核心。
- 未改：Agent Loop、Runtime、上下文架构、Memory、Checkpoint、预算、权限、数据库 Schema、DeepSeek/OpenAI Provider。
- 已实测（真实本机 Ollama，早期为 `num_ctx=8192`，当前默认 32768；32K 验收见下，非流式）：普通对话、calculator Tool Calling、三轮两工具的多轮反馈全部完成；经网页工作台真实执行一次 calculator 并留下 trace。
- 结论边界：这是少量固定任务上的可用性证据，不证明复杂任务成功率、稳定性或 4B 模型的通用能力。

## 2. 调用链与数据类型

```
用户（网页）
  → web/app.py（HTTP + CSRF + 会话归属）
  → HarnessApplication（会话/预算/检查点/后台线程）
  → Runtime._run（Agent Loop：模型请求、工具执行、结果回传）
  → ManagedModel + ContextManager（上下文投影与窗口检查）
  → OllamaModel.generate(list[Message], tools=Schema)
  → POST http://127.0.0.1:11434/api/chat
  → ModelResponse(finish_reason, content, tool_calls)
```

工具轮：

```
ModelResponse(finish_reason="tool_calls", ToolCall[])
  → Runtime 追加 assistant 消息（含 tool_calls）
  → ToolRegistry.execute(ToolCall)（Pydantic 参数校验、权限、确认、预算、operation_id 防重）
  → Message(role="tool", tool_call_id, content=JSON结果)
  → 下一轮 OllamaModel：按历史 tool_calls 把 tool_call_id 解析成 tool_name 后回传
  → 模型最终回答 ModelResponse(finish_reason="stop")
```

职责边界：

| 层 | 负责 | 不负责 |
| --- | --- | --- |
| `llm/ollama.py` | 消息/工具/结束原因转换，一次本机 HTTP 请求，截断检测 | 执行工具、权限、压缩、重试其他模型 |
| `Runtime` | 循环、工具调用编排、错误反馈轮数、结果落库 | 协议细节、提供商鉴权 |
| `ToolRegistry` | 参数校验、权限与用户确认、预算扣减、防重、trace | 生成工具调用 |
| `ContextManager` | 字节窗口预算、投影/压缩 | 精确 token 计数 |

## 3. 协议映射

内部契约（`src/min_agent/models.py`）保持不变：`Message`、`ToolCall`、`ModelResponse`、`tool_call_id`。

| 内部 | Ollama `/api/chat` |
| --- | --- |
| `system` / `user` / `assistant` 纯文本 | 同名字段，原文保留 |
| assistant + `tool_calls` | `{"role":"assistant","content":...,"tool_calls":[{"type":"function","function":{"name","arguments":{...}}}]}` |
| `role="tool"` + `tool_call_id` | `{"role":"tool","content":...,"tool_name":...}` |
| `ModelResponse.finish_reason` | 由 `done_reason` 与 `message.tool_calls` 推导 |
| `ProviderState` | 不生成、不发送；历史中的其他提供商原生状态被忽略，只回放可见正文 |

具体规则：

1. **工具名称**：只从同一历史中更早的 assistant `tool_calls` 建立 `id → name` 映射；未知 ID、重复结果、结果未配齐一律抛错（转成 `invalid_request`），不猜测工具名。
2. **调用 ID**：Ollama 返回的 `id` 先按 `^[A-Za-z0-9_.:-]{1,128}$` 校验；缺失、非法或同一响应内重复时生成 `ollama-<uuid4>`。跨响应重复的 ID 不重新生成，交给 Runtime 的 `duplicate_tool_call_id` 安全失败，避免换 ID 绕过副作用去重。
3. **参数**：必须是 JSON 对象。模型把对象编码成字符串时用严格 `json.loads` 解析（拒绝重复字段、NaN、非对象）；其他类型直接视为协议错误，不执行。
4. **多工具调用**：按返回顺序全部转换，逐个执行；一条响应内的 ID 保证唯一。
5. **结束原因**：`done_reason="length"` → `length`（丢弃可能不完整的工具调用，不执行）；有工具调用 → `tool_calls`；有正文 → `stop`；空白正文 → `error`；未知 `done_reason` → `error`。
6. **工具结果**：Runtime 仍以 `tool_call_id` 关联；适配器在发请求时替换为 `tool_name`，绝不把内部 ID 当工具名。

## 4. 配置与默认值

`OllamaSettings`（`src/min_agent/llm/ollama.py`，全部字段有默认值和边界校验）：

| 字段 | 默认 | 边界/约束 |
| --- | --- | --- |
| `model` | `qwen3.5:4b` | 1–128 字符模型名 |
| `base_url` | `http://127.0.0.1:11434` | 仅 `127.0.0.1`/`localhost`/`::1`，不允许路径、查询串、账号密码 |
| `num_ctx` | `32768` | 2048–262144（上限对齐模型声明；32K 已做长上下文多轮验收，16K 为低内存备用） |
| `max_output_tokens` | `1024`（同时作为 `num_predict`） | 64–8192 且不超过 `num_ctx` |
| `temperature` | `0` | 0–2 |
| `keep_alive` | `30m` | 时长格式 |
| `think` | `False` | 布尔 |
| `stream` | 固定 `False` | 本阶段只做非流式 |
| `timeout_seconds` | `180` | 1–600 |
| `max_requests` | `100` | 1–1000，与 CLI `--max-requests` 一致 |

安全边界：`httpx.Client(trust_env=False)`，不读取 `HTTP(S)_PROXY`，不接受 `--proxy` 作为模型代理，无 `Authorization` 头，无 API Key；任何错误都只映射为固定错误码，不自动改发云端。

**为什么默认是 32768**：实测把完整系统提示（约 8.7KB）+ 17 个工具 Schema（约 9.9KB）发给 `qwen3.5:4b`，`num_ctx=4096` 时 `prompt_eval_count=4094`、`done_reason=length`、只生成 2 tokens——固定开销已吃满窗口，普通提问无法完成；8192 只能算最低可用。32768 已用真实两轮任务验证：第一轮约 17.5K、第二轮约 24.3K prompt tokens 的长日志检索 + calculator，工具结果 703→708，内存最低 1.88GB，无截断。低内存环境可显式 `--ollama-num-ctx 16384`（本机也验证可完成短任务）；262144 不作为默认，原因见[上下文能力报告](ollama-context-capability-20261102.md)。

## 5. 上下文与截断

现有 `ContextManager` 的窗口预算是 **UTF-8 字节** 上界，Ollama 的 `num_ctx` 是 **token** 窗口，二者不等价。适配器用保守换算：

```
context_window = num_ctx * 3 字节     # 中文约 3 字节/字符，按最多 1 token/字符的上界
output_reserve = max_output_tokens (1024)
safety_margin  = 1024
```

`num_ctx=32768` → 字节窗口 98304，扣除输出预留与安全余量后可请求 96256 字节。实测普通首轮请求约 20230 字节；32K 长上下文验收中，第二轮请求携带约 28KB 日志与工具历史，实际 prompt 约 24.3K tokens，仍在窗口内并完成工具调用。`num_ctx=8192` 时可用请求预算为 22528 字节；`num_ctx=262144` 时字节窗口为 786432 字节。历史更长时会先进入现有结构化压缩/LLM 压缩，而不是丢消息。

**发送前字节闸门**：Provider 在发送前检查实际请求体，超过 `num_ctx * 3` 字节直接抛 `ContextLimit`，不把超大请求交给 Ollama 去静默截断。这与 `ContextManager` 使用同一个保守窗口，属于双保险；正常 Harness 路径在 `ContextManager` 阶段就会压缩或失败。

**与 harness 默认窗口的关系**：harness 对没有 Provider 声明的 Mock/Demo 使用 262144（`DEFAULT_HARNESS_CONTEXT_WINDOW`）；Ollama 通过 `context_limits()` 声明 `num_ctx × 3` 字节，DeepSeek 通过 `/models` 元数据声明自己的窗口（例如 1M）。Provider 的声明始终优先，不套用 harness 的 256K 默认值；没有声明能力的真实 Provider 仍要求显式配置，不猜测窗口。

**截断保护**：适配器检查 `prompt_eval_count > num_ctx - 16`，命中即抛 `ContextLimit`，Runtime 返回 `partial/context_limit`，应用保存事实检查点；被截断的正文不会作为答案写入会话。系统指令和工具结果不会被静默裁掉。

局限：字节换算是保守代理，不是精确 token 计数。实测发现 Ollama 在 `num_ctx=8192` 收到约 12K tokens 的 prompt 时会截到 4098 tokens（日志 `truncating input prompt limit=4098`），这种“提前截断”发生在远低于 `num_ctx-16` 的位置，事后检查无法可靠识别；发送前字节闸门已阻止常规文本路径产生这类请求，但极端 tokenizer 密度仍可能绕过，详见[上下文能力报告](ollama-context-capability-20261102.md)。

## 6. 安全与权限保持原状

Ollama 只“提出”工具调用，不执行任何工具。以下机制均在原位置生效，并有离线测试：

- `ToolRegistry` + Pydantic 参数校验（非法参数返回 `invalid_arguments`，不执行）。
- 工具权限 / Plan / Build 限制 / 用户确认（`todo_add` 在未授权时返回 `waiting_user`，不写库、不再请求模型）。
- 预算扣减与 `BudgetPersistence`（测试中两次模型调用、一次工具调用被记账；预算边界处停止）。
- 工具 `operation_id` 与防重（跨响应重复 call id 直接 `duplicate_tool_call_id` 失败，不二次执行）。
- Trace（model/tool/run 事件、固定错误码，不含请求或响应正文）。
- 现有 Session / Checkpoint / 恢复（`context_limit` 生成检查点后可定向恢复）。

## 7. 测试与实测证据

命令（项目根目录，PowerShell）：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest tests\unit\test_ollama_provider.py -q
.\.venv\Scripts\python.exe -X utf8 -m pytest tests\integration\test_ollama_runtime.py -q
.\.venv\Scripts\python.exe -X utf8 -m pytest -q
.\.venv\Scripts\python.exe -X utf8 -m pip --isolated check
.\.venv\Scripts\python.exe -X utf8 -m compileall -q src tests scripts
```

- 离线单元测试：`tests/unit/test_ollama_provider.py`，48 项通过（覆盖文本映射、工具 Schema、单/多工具调用、缺 ID、结果回传、空响应、超时、非法 JSON、上下文过大、262144 边界与字节闸门、Provider 边界）。
- 离线集成测试：`tests/integration/test_ollama_runtime.py`，6 项通过（应用级工具循环、授权边界、截断检查点与恢复、重复 ID、预算边界、CLI 模式互斥）。
- 全量回归：**1499 passed，4 deselected（live），1 warning**；无既有测试被修改（新增测试后另行全量复跑，见阶段记录）。
- 真实本机验收（`scripts/ollama_live_acceptance.py --allow-live`，报告 `docs/acceptance/ollama-live-20261102.json`）：

| 案例 | 任务 | 结果 | 耗时 | 模型调用 | 工具 |
| --- | --- | --- | --- | --- | --- |
| A | 用两句话介绍什么是 Agent。 | completed，中文回答 | 11.93s | 1 | — |
| B | calculator 计算 37×19 | completed，工具返回 703，回答含 703 | 19.93s（首轮 11.4s / 二轮 8.2s） | 2 | 1 |
| C | 12+30 → 结果×2（两轮） | completed，工具结果 42、84，回答 84 | 39.95s（三步） | 3 | 2 |

- 网页工作台真实任务（`--ollama --port 8766 --database data/ollama-test.sqlite3`）：页面 200、identity `provider=ollama`、任务 completed、消息链 user→assistant(tool_calls)→tool(703)→assistant、trace 4 个事件（model/tool/model/run）、预算 model_calls=2/tool_calls=1、总耗时 20.22s。
- 32K 长上下文多轮验收（`scripts/ollama_long_context_test.py --allow-live`，报告 `docs/acceptance/ollama-32k-long-context-20261102.json`）：第一轮 17,541 prompt tokens、第二轮 24,286/24,451 tokens，两轮各自 calculator 工具调用（703→708），任务 completed，内存最低 1.88GB，截断日志 0 条，耗时 247s / 173s。
- 上下文能力实测（`scripts/ollama_context_benchmark.py --allow-live`，报告 `docs/acceptance/ollama-context-20261102.json`）：16384/32768/65536 完成了 calculator 与长上下文检索，但内存余量 1.1–2.0GB；262144 加载后仅 0.17GB、加载期最低 0.0GB，未执行真实任务。对比与结论见[上下文能力报告](ollama-context-capability-20261102.md)。
- 128K 分阶段测试（`scripts/ollama_128k_staged_test.py --allow-live`，报告 `docs/acceptance/ollama-128k-staged-20261102.json`）：加载 28.4s、进程 7.8GB，加载期最低可用 0.43GB、加载后 0.53GB，触发安全阈值后立即卸载，未执行真实任务。
- 冷启动：首次加载模型约 20–41s（取决于窗口大小与内存压力）；`keep_alive=30m` 后连续请求约 8–50s（含约 4K prompt eval）。

## 8. 尚未解决的限制

1. 只实现非流式请求。网页仍靠会话轮询展示最终结果，没有 Token 级流式输出。
2. 默认 `num_ctx=32768` 时字节窗口 96256 字节；长对话会先走压缩。低内存环境可显式 `--ollama-num-ctx 16384`（已做短任务验证），更大窗口（65536）本机只剩约 1.1GB 可用内存，耗时明显增加（见[上下文能力报告](ollama-context-capability-20261102.md)）。
3. 262144 可配置、可加载，但在本机加载期可用物理内存最低到 0.0GB、加载后仅 0.17GB；131072 加载期最低 0.43GB、加载后 0.53GB，均未通过“能稳定执行真实任务”的验收，因此默认不采用，也不建议在本机使用。
4. 字节窗口是保守上界，不是精确 token 计数；Ollama 的“提前截断”无法仅靠 `prompt_eval_count` 事后识别，常规路径靠发送前字节闸门防护。
5. 4B 模型可能选错工具或参数；错误会走现有 `invalid_arguments`/修复轮机制，最多 2 轮，不做能力兜底。
6. 未实现 Ollama 原生状态回放（`ProviderState` 保持为空）；切换提供商后只回放可见正文。
7. 未验证并发多会话同时推理、GPU 层数或长时间稳定性；本机为 CPU/小显存环境，速度不代表其他硬件。
8. OpenAI 的 CLI 选择入口本次未新增（保持原状）；本次只新增 `--ollama`，不影响 `--demo` / `--live`。

## 9. 启动、切换与回滚

```powershell
# 启动本地 Ollama 工作台（默认 num_ctx=32768，独立测试库）
.\.venv\Scripts\python.exe -X utf8 -m min_agent.web --ollama --database data/ollama-test.sqlite3 --port 8766

# 低内存备用：16K 窗口
.\.venv\Scripts\python.exe -X utf8 -m min_agent.web --ollama --ollama-num-ctx 16384 --database data/ollama-test.sqlite3 --port 8766

# 打开网页
Start-Process http://127.0.0.1:8766/

# 切回离线 Demo
.\.venv\Scripts\python.exe -X utf8 -m min_agent.web --demo --database data/demo.sqlite3 --port 8765

# 切回 DeepSeek（凭据在项目外；云端使用自身声明的窗口，不套用本地32K）
$credentialsFile = Join-Path $env:USERPROFILE 'agent-harness-credentials.json'
.\.venv\Scripts\python.exe -X utf8 -m min_agent.web --live --credentials-file $credentialsFile --proxy http://127.0.0.1:7890 --database data/workspace.sqlite3 --port 8765
```

回滚：删除 `src/min_agent/llm/ollama.py`、`tests/unit/test_ollama_provider.py`、`tests/integration/test_ollama_runtime.py`、`tests/live/test_ollama_live.py`、`scripts/ollama_live_acceptance.py`、`scripts/ollama_long_context_test.py`、`scripts/ollama_128k_staged_test.py`，还原 `src/min_agent/web/__main__.py`、`src/min_agent/model_context.py` 与 README 的对应改动即可；未修改数据库 Schema，既有会话和预算不受影响。
