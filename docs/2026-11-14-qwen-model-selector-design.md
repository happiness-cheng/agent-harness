# 2026-11-14：Web 端模型选择器（千问平台）设计

> **Task ID**：`QWEN-SELECTOR-001`　**Phase**：FEATURE
> **状态**：设计已由用户批准（A 方案 + 凭据文件方案），实现与验证结果见
> [IMPLEMENTATION_LOG](../docs/engineering-log/IMPLEMENTATION_LOG.md) / [VALIDATION_LOG](../docs/engineering-log/VALIDATION_LOG.md) 的同名条目。
>
> 本文件自足保存：要做什么、判据是什么、为什么这样切、明确不保证什么。

## 1. 背景与问题

- 生产 Web 端目前只能三选一，且在**进程启动时**定死：`--live`（DeepSeek 直连）/ `--ollama`（本机）/ `--demo`（离线）。
  见 `src/min_agent/web/__main__.py:81-115`、`src/min_agent/application.py:146`。
- 用户在本机 `.env` 里有千问平台按量付费 key（`sk-ws-…`），但 `src/` 中**没有任何代码读取它**
  （全仓 `grep` 无 `gemini|groq|qianwen|dashscope` 命中；`config.py:27-29` 白名单只有 `openai_api_key`/`deepseek_api_key`）。
- 2026-11-13 的千问接入（`docs/2026-11-13-qianwen-provider-integration.md`）只做在 bench 项目，生产主链**明确未接入**（§2、§6）。
- 用户诉求：在发送区旁边给一个选择模型的入口，能真正切到可用模型。

## 2. 已确认的外部事实（2026-11-14 只读探测）

| 项 | 结果 |
|---|---|
| key 类型 | `sk-ws-` 前缀 = 按量付费（非 Token Plan） |
| `GET https://dashscope.aliyuncs.com/compatible-mode/v1/models` | HTTP 200，182 个模型 |
| `GET https://maas.qianwenaiapi.com/compatible-mode/v1/models` | HTTP 200，同一批 182 个模型（两域名等价，本设计取前者） |
| 目标模型在列 | `qwen3.8-flash`、`qwen3.8-max`、`qwen3.7-plus`、`deepseek-v4.1-flash` 均在 `/models` 返回中 |
| `/models` 是否给上下文 | **否**，条目只有 `id/object/created/owned_by`，不能像 DeepSeek 那样从元数据拿窗口 |
| 外部请求成本 | 上述探测均为元数据查询，0 计费；本批**真实生成请求仅 1 次**（见 §7） |

## 3. 目标 / 非目标

**目标**

1. 发送区新增模型下拉，可选 4 个白名单模型（默认 `qwen3.8-flash`）。
2. 选择**按会话**保存：A 会话用 flash、B 会话用 max，互不影响。
3. 只有该会话**没有运行中任务**时才能切换；运行中切换返回 409。
4. 非白名单模型返回 400，且不发起任何外部请求。
5. 下一次运行真正使用所选模型；`trace` 与会话快照能看出用的是哪个。
6. 模型调用失败（402/401/429/400/网络）**不再被吞成笼统的“任务已暂停”**：横幅显示固定错误码与模型名。

**非目标（本批明确不做）**

- 不做按单条消息切换（会破坏工具调用与 `provider_state` 的连续性）。
- 不做自动故障转移/重试到另一个模型（欠费就明确失败）。
- 不接 Gemini / Groq；不改千问之外的任何 Provider。
- 不做费用换算（无价目表，只记 token）。
- 不改 `openai_protocol.py` 的 Responses 解析器，也不复用它解析 Chat Completions。

## 4. 架构

```
前端 index.html + app.js
   └─ <select id="model-choice">  ← 快照 saved.model.options / saved.model.current
                                  切换 → POST /api/sessions/{id}/model
Web 层 app.py
   └─ POST /api/sessions/{id}/model → harness.set_model()（白名单 + 非运行中 + 归属校验）
应用层 application.py
   ├─ set_model()：把选择写入会话扩展 model_choice
   ├─ model_status()：快照里输出 current/options/selectable
   └─ model_factory(context)（已存在，application.py:494）→ 每次运行取当前会话的模型实例
适配器层 llm/qianwen.py
   ├─ QianwenSettings：模型、端点、key、超时、请求上限、输出上限
   ├─ build_messages / build_tools：内部 Message → Chat Completions 结构
   ├─ parse_chat_completion：choices[0] → ModelResponse（含 tool_calls / length / usage）
   ├─ QianwenChatModel.generate()：一次非流式 POST，错误映射成固定 LLMError
   └─ QianwenCatalog：白名单、按需实例化并缓存、close()
```

**为什么是一个适配器而不是多厂商注册表**：这个平台一份 key 同时提供 Qwen 与 deepseek-v4.1-flash，
“多厂商”在演示上等价于“同一协议换 model id”，不需要维护三套协议与三套错误码。

**为什么复用 `model_factory`**：`HarnessApplication` 已支持按会话解析模型
（`raw_model = self.model_factory(context)`），主循环、预算、Trace 都不用改。
本批只增加“选择存哪里、谁校验、前端怎么显示”，属于最小侵入。

## 5. 数据流

1. 前端拿到会话快照 → 用 `saved.model.options/current` 渲染下拉。
2. 用户切换 → `POST /api/sessions/{id}/model {model}`。
3. `set_model` 校验：会话归属（`context()`）、白名单（catalog）、任务非 running。
4. 通过 → `sessions.set_extension(context,"model_choice",model)`；失败 → 400/403/409。
5. 下一次 `submit/resume` → `_worker` → `model_factory(context)` 取该模型的 `QianwenChatModel`。
6. 运行结束 → trace 记录本次 provider/model；快照 `model.current` 反映当前选择。

## 6. 错误处理与边界

| 情况 | 行为 | 证据形式 |
|---|---|---|
| 400/401/402/403/404/429/5xx | `LLMError(f"http_{code}", diagnostics={"http_status":code,...})`，重试 0 次 | trace `error_code=http_402`，横幅显示“余额不足/鉴权失败/限流” |
| 网络不可达 | `LLMError("network_error")` | 同上 |
| 缺 `choices` / 工具参数非法 JSON | `LLMError("invalid_response")` | 不猜、不重试 |
| 响应超 4MB | `LLMError("response_too_large")` | 同 client.py 口径 |
| 非白名单模型 | `ValueError` → 400 | 不发请求 |
| 运行中切换 | `RuntimeError` → 409 | 状态不变 |
| 会话不属于本 owner | `PermissionError` → 403 | 状态不变 |
| 上下文超限 | 沿用 `context_window()` 与表中声明值 | `source=declared:qianwen-table` |

**声明值（未实测，标 `declared_conservative`）**

| 模型 | context_window | max_output_tokens | 依据 |
|---|---|---|---|
| qwen3.8-flash | 1,048,576 | 32,768 | 平台文档标百万上下文；输出上限取保守值 |
| qwen3.8-max | 1,048,576 | 32,768 | 同上 |
| qwen3.7-plus | 131,072 | 16,384 | **未查到文档值，取保守值** |
| deepseek-v4.1-flash | 131,072 | 16,384 | **未查到文档值，取保守值** |

声明值只用于 ContextManager 的压缩阈值，**不等于实测容量**；实际请求超限由服务端 4xx 明确失败。

## 7. 验证方案（先定判据）

| # | 场景 | 输入 | 判据 |
|---|---|---|---|
| S1 | 解析器：普通回答 | 固定 JSON（`finish_reason=stop`） | `ModelResponse(finish_reason="stop")`，正文一致 |
| S2 | 解析器：工具调用 | 固定 JSON（两个 `tool_calls`，arguments 是 JSON 字符串） | 两个 `ToolCall`，参数可还原，顺序保持 |
| S3 | 解析器：截断 | `finish_reason=length` | 返回 `length`，不执行任何工具 |
| S4 | 解析器：坏响应 | 缺 `choices` / 空正文 stop / 非法 arguments | 抛 `ValueError`，不返回伪成功 |
| S5 | 消息构造 | 含 assistant 工具请求 + tool 结果的对话 | 输出含 `tool_calls`/`tool_call_id` 且配对正确；缺配对时抛错 |
| S6 | HTTP 402 | `MockTransport` 返回 402 | `LLMError.code=="http_402"`、`diagnostics["http_status"]==402` |
| S7 | 网络错误 | `MockTransport` 抛 `ConnectError` | `LLMError.code=="network_error"` |
| S8 | 用量记录 | 200 响应含 usage | `model.usage` 有一条 `prompt/completion/total` 记录 |
| S9 | 白名单 | catalog 传 `gpt-4o` | 抛 `ValueError`，不创建实例 |
| S10 | 会话绑定 | 两个会话各选不同模型 | `model_factory` 返回对应 model id，互不串 |
| S11 | 运行中切换 | 任务 running | 409，扩展值不变 |
| S12 | 真实烟测（有界） | 1 次「只回一个 OK」`max_output_tokens=32` | HTTP 200、非空正文、usage 有数；**仅 1 次，≤¥0.01** |
| S13 | 回归 | 既有 `pytest` | 失败数不超过基线 |

## 8. 凭据

- 复用项目外凭据文件 `%USERPROFILE%\agent-harness-credentials.json`，白名单新增 `qianwen_api_key`。
- 由实现方把 `.env` 中的值复制过去：**不回显、不进仓库、不进日志、不进命令行历史**。
- 适配器只接收 `SecretStr`；序列化时 `exclude=True`（沿用 `OpenAISettings` 口径）。

## 9. 明确不保证

- 4 个模型的上下文与输出上限来自声明值，**未逐个实测**；标 `declared_conservative`。
- 思考模型（`qwen3.8-max` 等）的 `reasoning_content` 本批**丢弃不展示**，兼容性 NOT VERIFIED。
- `deepseek-v4.1-flash` 是否需要先在控制台开通**未验证**；若未开通，选中后应看到明确 4xx 而不是“暂停”。
- 只验证“能选、能调通、错误可见”，**不证明**换模型后任务质量更好。
- 只做单机单用户场景；不改变已有的 owner 隔离、CSRF、CSP 边界。