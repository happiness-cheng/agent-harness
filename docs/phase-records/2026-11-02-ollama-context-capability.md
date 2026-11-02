# 2026-11-02 Ollama 上下文能力验证（16K–256K）

## 目标

- 把 Ollama Provider 的 `num_ctx` 可配置上限放宽到 262144，保留 `--ollama-num-ctx` 显式覆盖。
- 在本机独立验证 16384 / 32768 / 65536 / 262144 的加载、内存、耗时和真实任务表现。
- 只有当 262144 能稳定运行时才把默认值改为 262144；否则保留已验证的安全默认值并报告限制。
- 检查 `context_limits()`、字节预算、输出预留和截断保护与新上限的一致性。

## 改动

- `src/min_agent/llm/ollama.py`
  - `OllamaSettings.num_ctx` 上限从 65536 放宽到 262144；默认仍为 8192（后续批次已改为 32768，见 2026-11-02-ollama-32k-default.md）。
  - 新增发送前字节闸门：请求体超过 `num_ctx × 3` 字节时抛 `ContextLimit`，不交给 Ollama 静默截断。
- `tests/unit/test_ollama_provider.py`：新增 4 项（262144 边界、默认值保护、最大窗口截断阈值、字节预算与闸门）。
- `scripts/ollama_context_benchmark.py`：新增分窗口基准脚本（加载、内存采样、真实任务、长上下文检索、自动卸载）。
- `docs/ollama-context-capability-20261102.md`、`docs/acceptance/ollama-context-20261102.json`：证据与结论。
- README、`docs/OLLAMA.md`：更新上限、默认值解释与实测链接。

未改 Runtime、ToolRegistry、Memory、ContextManager、其他 Provider、数据库 Schema；未下载模型；未改 Ollama 安装或模型目录；未清理用户数据。

## 实测结果（摘要）

| num_ctx | 加载(s) | 进程占用 | 加载后可用RAM | 真实任务 | 12K级检索 |
| --- | --- | --- | --- | --- | --- |
| 8192 | 28.4 | 3.11GB | 2.95GB | completed 51.2s | 正确 |
| 16384 | 20.6 | 3.42GB | 1.98GB | completed 58.2s | 正确 |
| 32768 | 21.6 | 4.05GB | 1.93GB | completed 52.4s | 正确 |
| 65536 | 21.6 | 5.30GB | 1.14GB | completed 53.3s | 正确 |
| 262144 | 40.6 | 12.80GB | 0.17GB（最低 0.00GB） | 安全跳过 | 安全跳过 |

硬件：Ryzen 5 5600U / 16GB（可见 13.86GB）/ MX450 2GB；Ollama 0.40.13；模型 `qwen3.5:4b` Q4_K_M，声明上下文 262144，KV 估算 32KB/token（8 层 full attention × 4 KV heads × 256+256 × F16）。

## 决定

- **262144 不作为默认**：虽可加载，但加载期耗尽物理内存（最低 0.00GB）、加载后仅 0.17GB，无法稳定执行真实任务。
- **默认保持 8192**（后续批次改为 32768）；`--ollama-num-ctx` 可显式设置到 262144。
- 16384/32768/65536 属于可实验档位，内存余量 1.1–2.0GB，耗时明显增加，不建议默认。

## 额外发现

`num_ctx=8192` 时 Ollama 会把约 12K tokens 的 prompt 截到 4098（日志 `truncating input prompt limit=4098`），响应仍为 `stop`。仅靠 `prompt_eval_count` 事后检查无法识别这种提前截断；已用发送前字节闸门在常规路径阻止，但极端 tokenizer 密度仍可能绕过，作为已知残留风险记录。

## 验证

- `pytest tests/unit/test_ollama_provider.py -q`：48 passed（含新增 4 项）。
- `pytest tests/integration/test_ollama_runtime.py -q`：6 passed。
- `pytest -q`：1498 passed，4 deselected（live），1 warning。
- `pip --isolated check`：No broken requirements found；`compileall -q src tests scripts`：退出码 0。
- 真实基准：`scripts/ollama_context_benchmark.py --allow-live --contexts 8192 16384 32768 65536 262144`，报告 `docs/acceptance/ollama-context-20261102.json`。
- 回归复核：新增字节闸门后重跑 `ollama_live_acceptance.py --cases B`，真实 calculator 任务仍 completed（70.5s，含冷加载）。

## 回滚

把 `num_ctx` 上限恢复为 65536、删除发送前字节闸门与新增测试，删除基准脚本和两份文档即可；默认值 8192 未变，数据库和用户数据不受影响。
