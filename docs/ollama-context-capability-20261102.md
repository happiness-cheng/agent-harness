# Qwen3.5 4B 上下文能力实测（16K–256K）

更新：2026-11-02，Asia/Shanghai。原始数据：`docs/acceptance/ollama-context-20261102.json`；实测脚本：`scripts/ollama_context_benchmark.py`。

## 结论

- **默认已改为 32768**：在 16K–256K 容量扫描之后，又用 32K 做了真实两轮长上下文任务（第一轮 17,541 prompt tokens、第二轮 24,286/24,451 tokens，长日志检索 + calculator 703→708），两个任务均 completed、截断日志 0 条、内存最低 1.88GB，因此把 Ollama Provider 与 CLI 默认值从 8192 提升到 32768；低内存可用 `--ollama-num-ctx 16384`。
- **262144 可以加载，但不能作为默认值**：加载耗时 40.6s，Ollama 进程占用 12.8GB（其中 VRAM 1.8GB），加载期间系统可用物理内存最低到 **0.00GB**，加载后仅剩 **0.17GB**。按预设的 0.6GB 安全阈值，未继续执行真实任务。这不是“256K 可用”的证据。
- 131072 也做了分阶段测试：加载 28.4s、进程 7.8GB，加载期最低可用 **0.43GB**、加载后 0.53GB，触发安全阈值后立即卸载，未执行短任务或长输入。
- 16384 / 32768 / 65536 在容量扫描中完成了 calculator 任务和约 3.7K–12.4K tokens 的检索；32K 另有 24K tokens 的多轮验收。16384/65536 仍是显式选项，不是默认。
- harness 默认窗口（无Provider声明的Mock/Demo）已改为 262144；能声明窗口的Provider始终优先使用自己的（DeepSeek 读取自身元数据，例如 1M）。
- 未下载模型、未改 Ollama 安装/模型目录、未清理用户数据；每个窗口测完都用 `keep_alive=0` 卸载，测试库使用临时目录。

## 环境

| 项目 | 值 |
| --- | --- |
| OS | Windows 11 |
| CPU | AMD Ryzen 5 5600U（6 核 / 12 线程） |
| 内存 | 物理 16GB，系统可见 13.86GB；模型卸载后可用约 6.2–6.6GB |
| GPU | NVIDIA MX450 2GB + AMD Radeon 集显 |
| Ollama | 0.40.13，`127.0.0.1:11434` |
| 模型 | `qwen3.5:4b`，Q4_K_M，约 3.34GB，已存在（未下载） |
| 模型上下文声明 | 262144 |
| 结构 | 32 层；8 层 full attention、24 层 recurrent/SSM |
| KV | 4 个 KV heads，key/value 长度 256，F16 → **32768 bytes/token** |
| KV 估算 | 16K:0.5GB，32K:1.0GB，64K:2.0GB，256K:8.0GB（另加权重 3.34GB 与运行时缓冲） |

## 方法

1. 每个 `num_ctx` 先卸载已加载模型，记录基线可用内存。
2. 用最小请求触发冷加载，读取 `load_duration`、`/api/ps` 的 `size`/`size_vram`/`context_length`，并每 0.5s 采样系统可用物理内存与提交空间。
3. 真实任务：通过现有 `HarnessApplication → Runtime → ToolRegistry` 执行“请使用 calculator 计算 37×19”，检查状态、工具结果与 trace。
4. 长上下文：生成含唯一编号的约 3.7K–12.4K tokens 文档（按字节窗口上限收敛），要求只回答编号；同时验证 Provider 的截断保护。
5. 加载后可用内存低于 0.6GB 时跳过真实任务，避免系统抖动；256K 因此只测了加载。
6. 每个窗口测试后卸载，确认内存恢复。

## 结果

| num_ctx | KV 估算 | 加载(s) | 进程占用 | VRAM | 加载后可用RAM | 加载期最低RAM | calculator 任务 | 长上下文检索 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 8192 | 0.25GB | 28.4 | 3.11GB | 0.10GB | 2.95GB | 2.34GB | completed，51.2s，703 | 正确，3727 tokens，34.9s |
| 16384 | 0.50GB | 20.6 | 3.42GB | 0.16GB | 1.98GB | 1.96GB | completed，58.2s，703 | 正确，7940 tokens，79.7s |
| 32768 | 1.00GB | 21.6 | 4.05GB | 0.26GB | 1.93GB | 1.86GB | completed，52.4s，703 | 正确，12384 tokens，131.9s |
| 65536 | 2.00GB | 21.6 | 5.30GB | 0.48GB | 1.14GB | 1.11GB | completed，53.3s，703 | 正确，12384 tokens，131.0s |
| 262144 | 8.00GB | 40.6 | 12.80GB | 1.80GB | **0.17GB** | **0.00GB** | 未执行（安全跳过） | 未执行（安全跳过） |

补充：

- 8K–64K 的 Agent 任务都走了完整链路（2 次模型调用、1 次工具调用、trace 四个事件），工具结果均为 703。
- 检索速度约 94–107 tokens/s（CPU 推理，受内存压力影响）；更大的 64K 窗口并未带来更快速度。
- 加载时间 20–41s；窗口越大，KV 分配与初始化越久。
- 256K 的进程占用 12.8GB ≈ 权重 3.34GB + KV 8.0GB + 运行时缓冲，接近整机可见内存上限。

## 32K 长上下文多轮真实任务（决定默认值）

`scripts/ollama_long_context_test.py`，报告 `docs/acceptance/ollama-32k-long-context-20261102.json`。

- 第一轮：约 19.1K 字符 CSV 日志（17,541 prompt tokens），要求找到 `ref=ZX-7391-KQ` 的 amount=37 并调用 calculator 计算 `37×19`；工具结果 703，任务 completed，耗时 247s。
- 第二轮：再追加约 9.3K 字符补充日志（累计 24,286 / 24,451 prompt tokens），要求找到 `ref=ZX-9999-AB` 的 amount=5 并用 calculator 加上上一轮结果；工具结果 708，任务 completed，耗时 173s。
- 全程 trace 为 `model:completed,tool:completed,model:completed,run:completed`，预算记录 4 次模型调用 / 2 次工具调用，Ollama 截断日志 0 条，内存最低 1.88GB（加载后 2.25GB）。
- 结论：32K 窗口在本机可以承受 20K–26K tokens 的真实多轮输入；因此默认值改为 32768。16384 保留为低内存备用（此前验证可完成短任务）。

## 128K 分阶段测试（资源不满足）

`scripts/ollama_128k_staged_test.py`，报告 `docs/acceptance/ollama-128k-staged-20261102.json`。

- 加载前：可用物理内存 7.93GB，可用提交 11.26GB，分页文件已分配 12152MB、当前使用约 2.47GB（峰值 4.88GB）。
- `num_ctx=131072` 加载 28.4s，进程 7.8GB（VRAM 0.92GB）。
- 加载期可用物理内存最低 **0.43GB**，加载后 0.53GB（低于 0.6GB 安全阈值，接近 0.3GB 加载期下限）；按流程立即卸载并标记“资源不满足”，未执行短任务或分级长输入。
- 卸载后可用内存恢复到 7.97GB；截断日志 0 条（未发生真实推理）。

## 为什么 256K 在本机不可用

1. **物理内存不足**：模型权重 + KV 需要约 11.4GB，进程实际 12.8GB；系统可见内存仅 13.86GB，模型卸载后基线可用约 6.2GB。
2. **加载期耗尽内存**：采样到的最低可用内存为 0.00GB，意味着 Windows 已在使用交换/压缩；即使返回成功，也无法保证后续任务稳定。
3. **显存同步吃紧**：VRAM 占用 1.8GB（共 2GB），桌面和其他 GPU 应用会受影响。
4. **真实任务未通过**：按安全阈值未执行；因此不能把“加载成功”表述为“256K 可用”。

## 一致性检查（代码层）

- `OllamaSettings.num_ctx` 边界：2048–262144；默认 32768（本机 32K 长上下文多轮验收通过）；低内存可显式 16384。
- harness 默认窗口（无Provider声明的Mock/Demo）：262144（`DEFAULT_HARNESS_CONTEXT_WINDOW`）；能声明窗口的Provider始终优先使用自己的窗口（DeepSeek 读取 `/models` 元数据，例如 1M）。
- `context_limits()`：`context_window = num_ctx × 3` 字节；32768 → 98304 字节，262144 → 786432 字节；`max_output_tokens=1024`。
- `ContextManager`：`output_reserve = 1024`、`safety_margin = 1024`，与 Provider 的 `num_predict=1024` 对齐。
- **发送前字节闸门**：Provider 在 POST 前检查请求体，超过 `num_ctx × 3` 字节直接抛 `ContextLimit`，与 `ContextManager` 使用同一预算，避免把超大请求交给 Ollama 静默截断。
- **截断保护**：`prompt_eval_count > num_ctx − 16` 时抛 `ContextLimit`，Runtime 返回 `partial/context_limit` 并保存检查点。
- 测试：`tests/unit/test_ollama_provider.py` 48 项（含 262144 边界、字节预算、截断阈值、32768 默认值）、`tests/unit/test_model_context.py`（harness 256K 默认与 Provider 窗口优先）、`tests/integration/test_ollama_runtime.py` 6 项。

### 实测发现：Ollama 会“提前截断”

`num_ctx=8192` 时向 Ollama 发送约 12K tokens 的 prompt，Ollama 日志为：

```
level=WARN msg="truncating input prompt" limit=4098 prompt=12384 keep=4 new=4098
```

即提示被截到 4098 tokens，而不是 8190。此时响应仍是 `done_reason=stop`，检索答案错误。由于 `prompt_eval_count=4098` 远低于 `num_ctx−16`，仅靠事后检查无法识别。新增的发送前字节闸门会在常规文本路径阻止这类请求；但极端 tokenizer 密度仍可能绕过，属于已知残留风险，不能宣称“截断已被完全消除”。

## 复现命令

```powershell
# 全量五档（会依次卸载/加载模型，256K 档可能显著占用内存）
.\.venv\Scripts\python.exe -X utf8 scripts\ollama_context_benchmark.py --allow-live --contexts 8192 16384 32768 65536 262144 --report docs\acceptance\ollama-context-20261102.json

# 只测某个窗口
.\.venv\Scripts\python.exe -X utf8 scripts\ollama_context_benchmark.py --allow-live --contexts 16384

# 显式用更大窗口启动工作台（自行承担内存与耗时）
.\.venv\Scripts\python.exe -X utf8 -m min_agent.web --ollama --ollama-num-ctx 32768 --database data/ollama-test.sqlite3 --port 8766
```

## 限制与建议

- 每个窗口只做了一轮短任务与一次检索；32K 另做了一轮两回合长输入，仍不代表长时间稳定运行；内存结果受浏览器等后台程序影响。
- 65536 在容量扫描中可完成任务，但只剩约 1.14GB 可用内存；在其他后台负载下可能进入交换，不建议作为默认。
- 本机建议：日常使用默认 `32768`（已完成 24K tokens 多轮验收）；内存紧张时显式 `--ollama-num-ctx 16384`；`65536` 仅作实验；`131072`/`262144` 不建议在本机使用。
- 网页单条输入上限是 20000 字符（`events.py`/`web/app.py` 的既有约束）；32K 长测试用两条各不超过 2 万字符的消息累计到 24K tokens。需要单条更长输入时要先调整该上限，本次未改。
- 若未来要在本机认真使用 128K–256K：需要更大的物理内存（估算 32GB 以上）或更低精度的 KV cache/更小的模型；本次未修改 Ollama 安装配置，也未启用 KV 量化。
