# 2026-11-13：千问AI平台接入说明（QWEN-PROVIDER-001）

> 状态：**评测层已接入并离线验证；生产 Agent 主链未改；真实模型调用 NOT RUN（本机缺 key）**。
> 本文件自足保存结论、原因与下一步。工程日志见 `engineering-log/` 三册的 `QWEN-PROVIDER-001`。

## 1. 平台是什么

阿里云「千问AI平台」（Model Studio 体系）的 OpenAI 兼容网关，**不是第三方野鸡中转**：
使用 `DASHSCOPE_API_KEY` 命名惯例、阿里云账号登录、地域化端点。它同时聚合第三方直供模型
（DeepSeek / Kimi / GLM / MiniMax / Tripo 等），所以「第三方」指的是上面的模型厂商，不是平台本身。

| 项 | 值 |
|---|---|
| OpenAI 兼容 base_url | `https://maas.qianwenaiapi.com/compatible-mode/v1` |
| 协议 | OpenAI 兼容 Chat Completions、OpenAI 兼容 Responses、Anthropic 兼容 Messages、DashScope |
| Function Calling | 全线支持（含第三方文本模型），另支持结构化输出、上下文缓存、MCP |
| 计费 | 按百万 token，输入/输出分别计价；思考 token 计入输出；Function Calling / MCP 不收工具费 |
| 新人免费额度 | 实名认证后每模型约 100 万 Token / 90 天，**各模型独立不互通** |
| 关键风险 | 「免费额度用尽即停」**默认关闭**，用尽后自动按量扣费，可能欠费 |

**两种 key 不可混用**（决定能调哪些模型）：

| Key | 前缀 | 端点 | 范围 |
|---|---|---|---|
| 按量付费 | `sk-ws-`（早期 `sk-`） | 上表 base_url | 平台全部模型，自动优先抵扣免费额度 |
| Token Plan | `sk-sp-` | **套餐专属地址**（不是上表） | 仅套餐模型列表内；`qwen4-max-preview` 明确「仅 Token Plan」 |

本轮脚本只按「按量付费 + 通用端点」路径实现；Token Plan 标为未适配。

## 2. 本轮接入到了哪一层（不要误读）

| 层 | 状态 |
|---|---|
| **评测系统 `agent-framework-bench`** | **已接入**：新增 `scripts/qianwen_provider.py`（配置 + `probe`/`smoke`/`models`/`bench-env`）、`tests/test_qianwen_provider.py`（19 项离线契约）。**未修改** `agent_bench/` 任何既有源码 |
| **生产 Agent `agent-harness` 主链** | **未接入**：`src/` 零改动。原因见 §6 |
| 真实模型清单 / 最小烟测 | **NOT RUN**：本机 `.env` 只有 `GEMINI_API_KEY`、`GROQ_API_KEY`，本平台 key 未配置 |

无凭据连通性探测已通过：`GET /compatible-mode/v1/models` 返回 **HTTP 401 / 0.92s**，
明确要求 `Authorization: Bearer`，证明网络、代理与路径都正确，只差凭据。该请求不带 key、0 费用。

## 3. 你要做的唯一一件事

在生产仓库的 `.env`（已被 `.gitignore` 忽略）增加一行，**不要把 key 贴进聊天、报告或命令行历史**：

```
QIANWEN_API_KEY=<你的按量付费 Key>
```

然后（工作目录 `D:\agent-framework-bench`）：

```bash
# 1) 只读列出该 key 实际能调用的模型（元数据查询，不产生推理费用）
./.venv/Scripts/python.exe -B -X utf8 scripts/qianwen_provider.py probe \
  --out reports/qwen-models.json

# 2) 一次极小烟测（合成 prompt，max_tokens=16，不启用任何收费工具）
./.venv/Scripts/python.exe -B -X utf8 scripts/qianwen_provider.py smoke \
  --out reports/qwen-smoke.json

# 3) 查看 5 个可用列（模型 ID、用途、方言标记）
./.venv/Scripts/python.exe -B -X utf8 scripts/qianwen_provider.py columns

# 4) 生成指定模型的环境变量模板（--model 可换成 §4 中任一模型 ID）
./.venv/Scripts/python.exe -B -X utf8 scripts/qianwen_provider.py bench-env --model deepseek-v4-flash
```

`probe` / `smoke` 同样支持 `--model`：「五个白名单」已作为**可逐个切换的列配置**写入脚本，
不再是只有一个默认模型的字符串列表。列定义只描述可用模型与环境变量，**不代表已跑过任何真实请求**。
本轮没有改动历史 `reports/full-model-rerun-20261110/run_model_column.py`，因此不会影响历史三列实验口径。

跑通 bench 场景（沿用既有 `model_endpoint.py` 的 `openai_compatible` 模式，**零源码改动**）：

```bash
export QIANWEN_API_KEY='<你的 Key>'
export AGENT_BENCH_ENDPOINT_MODE=openai_compatible
export AGENT_BENCH_BASE_URL=https://maas.qianwenaiapi.com/compatible-mode/v1
export AGENT_BENCH_API_KEY_ENV=QIANWEN_API_KEY
export AGENT_BENCH_MODEL=qwen3.8-flash
./.venv/Scripts/python.exe -B -X utf8 -m agent_bench run --scenario calculator_only \
  --frameworks openai_agents pydantic_ai --out reports/<新目录>
```

注意：`own_harness` 在云端直连模式明确 `UNSUPPORTED`；跨模型 `model_digest` 是端点+模型哈希，
`comparison_gate` 会拒绝与 Groq / Gemini / 本地 qwen 列做排名，只能并列展示。

## 4. 推荐模型（5 个可切换列，`QWEN-COLUMNS-001`）

| 列名 | `--model` 值 | 用途 | 备注 |
|---|---|---|---|
| `qwen38max` | `qwen3.8-max` | 旗舰：复杂推理、编程、长程任务 | 100 万上下文；输入 12 / 输出 36 每百万 token |
| `qwen37plus` | `qwen3.7-plus` | 质量、速度与成本均衡 | 思考模式下 `tool_choice` 仅支持 `auto`/`none` |
| `qwen38flash` | `qwen3.8-flash` | **默认列**：快速、高性价比 | 百万上下文；兼容 OpenAI / Anthropic 协议 |
| `dsv4pro` | `deepseek-v4-pro` | 第三方直供：深度推理对照 | 可能需先在控制台开通 |
| `dsv4flash` | `deepseek-v4-flash` | 第三方直供：低成本高并发对照 | 轻量 MoE |

五列 `dialect` 均为 `none`（无需模型特定 `extra_body`）、`function_calling=True`。
用 `columns` 查看完整定义，用 `bench-env --model <模型 ID>` 生成对应环境变量。
**精确 ID 以配好 key 后 `probe` 返回的 `/v1/models` 为准**；带日期后缀的快照与不带后缀版本
是两个独立模型，价格与免费额度分开，不可混用。

平台公开文档还可见：图像 `qwen-image-3.0-pro` / `wan2.7-image-pro`，视频 `wan3.0-video` / `wan3.0-video-prime`，
语音 `qwen-audio-3.1-tts-flash` / `qwen-audio-3.1-asr-flash-filetrans`，向量 `text-embedding-v4` / `qwen3-rerank`，
3D `Tripo/Tripo-H3.1`；第三方文本 `kimi/kimi-k3`、`ZHIPU/GLM-5.3`、`MiniMax/MiniMax-M3`、`decision-model-preview` 等。
**精确 ID 以 `probe` 返回的 `/v1/models` 为准**；带日期后缀的快照与不带后缀版本是两个独立模型，
价格与免费额度分开，不可混用。

## 5. Function Calling 的已知偏差（接入 bench 前必读）

`sanitize_payload()` 已剥离 `parallel_tool_calls` / `reasoning_effort` / `strict` 等、归一化 `max_tokens`，
对 Qwen 系列可用且**保持历史三列口径不变**，本轮刻意不改它。以下模型有额外要求，
**不在推荐白名单内**，若要用需另做方言适配并单独标注为已知偏差：

- GLM 系列：需 `extra_body={"tool_stream": True}`，否则不返回 `tool_calls`；
- Kimi 思考模式：每轮 assistant 消息必须保留 `reasoning_content`，且不支持 `parallel_tool_calls`；
- MiniMax：只支持 `tool_choice="auto"`，`parallel_tool_calls=false` 也不保证串行；
- Qwen 思考模式：`tool_choice` 只支持 `auto` / `none`；
- 工具描述计入输入 token；内置联网搜索（4 元/千次）等收费工具**本轮一律不启用**。

`chat-response-v1` 响应保护只支持单 choice 的文本 / function 调用形状；思考模型额外的
`reasoning_content` 字段兼容性**尚未实测**，标 NOT VERIFIED。

## 6. 生产 Agent 主链接入方案（本轮未执行）

生产运行时现状：

| 位置 | 约束 |
|---|---|
| `src/min_agent/llm/config.py` | `OpenAISettings.model` 是 `Literal["gpt-6.1-sol"]`；`from_credentials_file` 白名单只有 `openai_api_key`/`deepseek_api_key` |
| `src/min_agent/llm/client.py` | `OpenAIResponsesModel._endpoint` = `https://api.openai.com/v1/responses`、`_model_id` 硬编码，模型不匹配直接抛错 |
| `src/min_agent/llm/openai_protocol.py` | `parse_response` 只接受 `openai.responses` / `deepseek.responses`，解析 Responses 的 `output` 数组与 `function_call` 条目 |
| `src/min_agent/models.py` | `ProviderState.provider` 是闭合 Literal |

因此**不能直接把千问塞进现有 Provider**。MFAB 任务书 §164–165 已明确要求：
「不得把 Gemini/Groq 伪装成现有 Provider，或把该 parser 原样用于 Chat Completions；
在新的隔离 test harness 中实现兼容 `Model.generate(messages, tools=...) -> ModelResponse` 的 Adapter」。

最小路径（建议另起一批，需明确授权）：

1. 在隔离测试目录新增 `QianwenChatAdapter`，实现 `generate(messages, tools=...) -> ModelResponse`，
   覆盖 Tool Call ID、参数 JSON、回执配对、上下文、异常码与资源计数；**不改生产 `src/`**。
2. 先跑 Adapter 契约测试与 capability gate（协议、工具调用、结构化输出、上下文、错误码、usage）。
3. 再决定是否扩展 `ProviderState` / `OpenAISettings` 并接入 `HarnessApplication`。该平台也提供
   Responses API，可能复用部分 `openai_protocol.py`，但 `function_call` / `function_call_output`
   条目形状与现有 parser 是否一致**未验证**，不能假设。
4. 接入后按项目规则补：有界请求/费用/时间上限、独立复验、同条件单 Agent 前后对照；
   未验证项标 NOT RUN，不把「链路通」说成「能力变强」。

## 7. 授权、成本与未完成项

本批自设有界边界（用户给出 300 代金券构成费用授权）：**≤10 次外部请求、≤5 分钟、
总费用在 300 代金券内且本轮预期 < ¥0.01、仅合成 prompt、不启用收费内置工具、不传任何私人数据**。
实际只发了 1 次无凭据探测（HTTP 401，0 费用），**0 次模型调用**。

**NOT RUN / 未完成**：

- `probe` 真实模型清单、`smoke` 最小烟测、35 场景全量、生产全量 1758 项；
- Token Plan 专属 key 路径、GLM/Kimi/MiniMax 方言适配、思考模型 `reasoning_content` 兼容性；
- 生产 Agent 主链 Adapter（§6）、独立 Agent 审查、真实任务收益对照；
- 免费额度「用尽即停」与余额预警需在平台「权益」页自行开启（本脚本无法代操作）。

**下一步**：按 §3 配置 key 后，先跑 `probe` + `smoke`（合计 ≤2 请求、预期 < ¥0.01），
把结果另存新目录不覆盖历史；确认通过后再决定是跑 bench 单场景，还是启动 §6 的生产 Adapter 批次。

## 8. 证据索引

| 内容 | 路径 |
|---|---|
| 本说明 | `C:\Users\陈独秀\Documents\agent-harness\docs\2026-11-13-qianwen-provider-integration.md` |
| 接入脚本 | `D:\agent-framework-bench\scripts\qianwen_provider.py` |
| 离线契约测试 | `D:\agent-framework-bench\tests\test_qianwen_provider.py` |
| 首次失败 / 最终输出 | `reports/qwen-provider-20261113/tests-first-run.txt`、`tests-final.txt`（`/reports/` 被 gitignore 忽略，文件在磁盘） |
| 决策 / 实施 / 验证 | `docs/engineering-log/{DECISION_LOG,IMPLEMENTATION_LOG,VALIDATION_LOG}.md` → `QWEN-PROVIDER-001` |
| 平台公开文档 | `https://platform.qianwenai.com/docs`、`/docs/api-reference/preparation/api-key`、`/docs/resources/free-quota`、`/docs/developer-guides/tool-calling/function-calling` |
