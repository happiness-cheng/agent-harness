# 2026-11-02 本地 Ollama Provider 接入

## 目标与范围

让现有 Agent Harness 以本机 `qwen3.5:4b` 为决策核心完成真实任务；不重写 Runtime/Agent Loop，不改数据库 Schema、Memory、Checkpoint、预算和权限体系，不替换 DeepSeek/OpenAI Provider。

## 做了什么

- 新增 `src/min_agent/llm/ollama.py`：`OllamaSettings`（全部字段默认值+边界）、`build_messages`、`build_tools`、`parse_chat_response`、`OllamaModel.generate`、`context_limits`、`available_models`/`require_model`。
- 工具结果按历史 assistant `tool_calls` 把内部 `tool_call_id` 解析为 Ollama `tool_name`；未知/重复/未配齐直接失败，不猜工具名。
- 返回 ID 校验后使用；缺失/非法/同响应重复生成 `ollama-<uuid4>`；跨响应重复保留原 ID，交给 Runtime `duplicate_tool_call_id` 安全失败（避免换 ID 绕过副作用去重）。
- `finish_reason`：length 丢弃工具调用；tool_calls 优先；空正文为 error；未知结束原因为 error。
- 上下文：`context_window = num_ctx * 3` 字节（保守上界）；`prompt_eval_count > num_ctx-16` 时抛 `ContextLimit`，不把截断回答写成最终答案。
- 安全：只允许 loopback `base_url`，`trust_env=False`，无 API Key、无代理、无云端回退；启动前只读 `/api/tags` 核实模型，缺失即报错且不下载。
- CLI：`--ollama` 与 `--ollama-model/--ollama-url/--ollama-num-ctx/--ollama-num-predict/--ollama-keep-alive/--ollama-timeout`；`--demo`/`--live` 行为保持。
- 测试与验收：新增 44 项单元测试、6 项集成测试、1 项显式 live 测试和 `scripts/ollama_live_acceptance.py`；后续上下文能力批次把单元测试补到 48 项（见 2026-11-02-ollama-context-capability.md）。
- 文档：README 新增本地 Ollama 启动段；`docs/OLLAMA.md` 记录设计、协议、证据和限制。

## 为什么默认 num_ctx=8192（当时决定；后续批次已改为 32768，见 2026-11-02-ollama-32k-default.md）

实测完整系统提示 + 17 个工具 Schema 在 `num_ctx=4096` 时 `prompt_eval_count=4094`、`done_reason=length`、只输出 2 tokens，普通提问都无法完成。8192 时同一请求约 4094 tokens，尚有余量完成两轮工具回传。4096 仍可显式设置，但会得到 `context_limit`，属于“明确失败、不静默截断”。

## 验证（实际执行）

- `pytest tests/unit/test_ollama_provider.py -q`：44 passed。
- `pytest tests/integration/test_ollama_runtime.py -q`：6 passed。
- `pytest -q`：1498 passed，4 deselected（live），1 warning。
- `pip --isolated check`：No broken requirements found。
- `compileall -q src tests scripts`：退出码 0。
- 真实本机：`scripts/ollama_live_acceptance.py --allow-live --report docs/acceptance/ollama-live-20261102.json`，A/B/C 全部 completed；B 工具结果 703；C 两轮工具结果 42→84；耗时 11.93s / 19.93s / 39.95s。
- 网页工作台：`--ollama --port 8766 --database data/ollama-test.sqlite3`，真实 calculator 任务 20.22s 完成，trace 含 model/tool/model/run，预算 2 模型调用/1 工具调用，identity 报告 provider=ollama。

## 未做/限制

- 只做非流式；没有 Token 级流式输出。
- 8192 窗口历史余量小，长对话会较快压缩或 `context_limit`；未做 16384 长稳测试。
- 字节换算是保守代理，不是精确 token 计数。
- OpenAI 的 CLI 选择入口未新增（保持原状）。
- 未验证并发推理、GPU 层数或长时间稳定性；4B 模型能力有限，错误走原修复轮。

## 回滚

删除新增文件（`src/min_agent/llm/ollama.py`、两个离线测试、live 测试、验收脚本、`docs/OLLAMA.md`、本记录、验收 JSON），还原 `src/min_agent/web/__main__.py`、README 与 `docs/PROJECT_STATUS.md` 对应段落。未改数据库 Schema，既有数据不受影响。
