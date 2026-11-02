# 2026-11-02 Ollama 32K 默认值与长上下文验收

## 目标

- 在 32768 窗口下跑真实 20K–26K tokens 的多轮任务，检查内存、延迟、工具执行和截断日志。
- 稳定后把 `OllamaSettings` 和 CLI 默认值改为 32768，保留 `--ollama-num-ctx 16384` 低内存备用。
- 云端 Provider 不跟随该默认值，继续使用各自声明的窗口（DeepSeek 例如 1M）。
- harness 默认窗口改为 256K；有声明窗口的 Provider 优先。
- 按用户补充流程验证 131072：加载前后内存、分页状态、加载期最低内存，资源不足立即卸载。

## 改动

- `src/min_agent/llm/ollama.py`：`num_ctx` 默认 8192 → **32768**（上限仍 262144）。
- `src/min_agent/web/__main__.py`：`--ollama-num-ctx` 默认 8192 → **32768**，帮助文本标明低内存可用 16384。
- `src/min_agent/model_context.py`：无 Provider 声明的 Mock/Demo 默认窗口 32768 → **262144**；声明了 `context_limits()` 的 Provider 仍优先。
- `scripts/ollama_live_acceptance.py`：默认 `--num-ctx 32768`。
- 新增 `scripts/ollama_long_context_test.py`（32K 两轮长输入验收）。
- 新增 `scripts/ollama_128k_staged_test.py`（131072 分阶段资源验收）。
- 测试：默认值断言、模型窗口优先断言、8K 截断测试显式传 num_ctx=8192。
- 文档：README、`docs/OLLAMA.md`、上下文能力报告、验收 JSON。

未改 Runtime、ToolRegistry、Memory、ContextManager、数据库 Schema；未下载模型；未清理用户数据。

## 32K 真实多轮结果

报告：`docs/acceptance/ollama-32k-long-context-20261102.json`。

| 轮次 | 实际 prompt tokens | 工具 | 结果 | 耗时 |
| --- | --- | --- | --- | --- |
| 第一轮（约 19.1K 字符日志，检索 ZX-7391-KQ） | 17,541 / 17,698 | calculator | 703 | 247s |
| 第二轮（再追加约 9.3K 字符，检索 ZX-9999-AB） | 24,286 / 24,451 | calculator | 708 | 173s |

- 两轮均 `completed`，trace 含 model/tool/model/run，预算 4 次模型调用 / 2 次工具调用。
- Ollama 截断日志 0 条；内存最低 1.88GB（加载后 2.25GB，基线约 6.5GB）。
- 结论：32K 能承受 20K–26K tokens 的多轮真实输入，默认值改为 32768。

## 128K 分阶段结果

报告：`docs/acceptance/ollama-128k-staged-20261102.json`。

- 加载前：可用物理内存 7.93GB、可用提交 11.26GB、分页文件分配 12152MB/当前 2.47GB（峰值 4.88GB）。
- `num_ctx=131072`：加载 28.4s，进程 7.8GB（VRAM 0.92GB）。
- 加载期最低可用物理内存 **0.43GB**，加载后 **0.53GB**（低于 0.6GB 安全阈值）。
- 按流程立即卸载，标记“资源不满足”，未执行短任务或分级长输入；卸载后恢复 7.97GB。

## 决定与边界

- Ollama 默认 32768；16384 为低内存备用；65536 仅实验；131072/262144 本机不采用。
- 云端 Provider 不跟随：DeepSeek 继续读取自身 `/models` 元数据窗口（例如 1M）；没有声明窗口的 Provider 仍要求显式能力，不猜测。
- harness 默认 256K 只作用于没有 Provider 声明的 Mock/Demo；不覆盖云端或 Ollama 的声明窗口。
- 网页单条输入上限 20000 字符未改；32K 长测试用两条消息累计到 24K tokens。

## 验证

- `pytest tests/unit/test_ollama_provider.py tests/unit/test_model_context.py tests/integration/test_ollama_runtime.py -q`：70 passed（默认值变更后）。
- 全量回归：1499 passed，4 deselected（live），1 warning；`pip --isolated check` 无损坏依赖；`compileall -q src tests scripts` 退出码 0。
- 真实基准与长任务报告：见上。

## 回滚

把 `num_ctx` 默认改回 8192、CLI 默认改回 8192、`DEFAULT_HARNESS_CONTEXT_WINDOW` 改回 32768，并删除两个新脚本与对应文档/测试即可；数据库和用户数据不受影响。
