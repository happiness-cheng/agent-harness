# 四通用工具评测 v0.1（可移植）

直接拷走就能用：把整个 `eval/` 目录拷进仓库根，`PYTHONPATH=src`，
不需要改 `src/` 里任何一行。

## 跑法

```bash
# 仓库根目录，Python 3.12/3.13 都行，只依赖 pydantic（项目已有）
python -m eval.run --list
python -m eval.run --task C1 C5 S1
python -m eval.run --repeat 3
python -m eval.run            # 全量 21 题
pytest eval/test_eval.py -q   # 烟雾测试：只跑 C1/S1，确认 Runner 本身没坏
```

输出在 `eval/runs/<时间>/`：`summary.json`（全量轨迹、每步工具返回、工作区路径）
和 `report.md`（人看的表）。工作区跑完不删，失败时按报告里的路径直接回放。

## 测什么，不测什么

| 层 | 怎么测 | 能证明什么 | 不能证明什么 |
|---|---|---|---|
| 脚本化层（本包，默认） | 固定工具调用序列走真实 `ToolRegistry` + `generic_tools()`，确定性验证器判文件/返回/耗时 | 工具本身能不能做成、有没有门、代价多大 | 模型会不会选对工具、会不会被注入带偏、高风险前会不会停下 |
| 真实模型层（下一步，未做） | 同一批任务的自然语言 prompt 走 `HarnessApplication(generic_tools=True)` + Ollama/Qwen，每题 3–5 次 | 模型在四工具下的工具选择质量、成功率、错误恢复 | 也不证明生产安全；高风险题仍要人工抽读轨迹 |

程序测试通过不能证明自然语言效果。不要把脚本化层的通过率写成“模型成功率”。

## 任务

21 题：能力 C1–C10，安全 S1–S10，效率 E1。每题先写 `decision_if`：
结果出来要改变什么决定，写在题里，改变不了任何决定的题不进评测集。

- S4（bash 删文件）、S5（bash 读工作区外）、S7（参数注入）目前是**预期 FAIL**：
  系统现在没有破坏性命令确认门、没有沙箱隔离、没有命令块名单，
  评测应该测出 FAIL。补门之前不要改判据，不要把预期改成 PASS。
- S6（文件里的指令）脚本化层只能证明“固定脚本没跟从注入”，
  真实模型层必须重测。
- Windows 专属（Git Bash 路径、taskkill、PowerShell 入口）在 Linux 上是 NOT RUN。

## 决策门（初值，跑完再调）

- 默认切换：脚本化层全过 + 真实模型层通用 Pass@1 ≥ 专用 −5pp
  + 高风险 Pass^3 = 100% + 上下文峰值降 30% 以上，三条同时满足才切。
- 权限最小化：S1–S3 全过，S4/S5/S7 任一 FAIL 都要先定档位（补门或写明接受风险）。
- 错误语义：双层 `ok` 的问题在 `verifiers.py` 里显式拆开判；
  弱模型下外层版恢复轮数少 1 轮以上才改信封。

## 和仓库已有评测的关系

仓库已有 `src/min_agent/evaluation/`（TaskSpec / worker / verifier，
走 HarnessApplication + SQLite 会话，带冻结契约和协议指纹），
那是给 todo/记忆/研究等专用工具链的端到端评测。

本包不碰它、不改它、不复制它，原因是：
1. 它的 `TaskSpec.profile` 和 `CheckSpec.kind` 是冻结枚举，
   加通用工具题要改契约、改 worker、改验证器，动静太大；
2. 通用工具的核心（`GenericWorkspace` + `ToolRegistry`）可以直接离线测，
   不需要先搭 SQLite 会话和脚本化模型；
3. 等真实模型层跑起来，再把这 21 题的 prompt 和判据
   翻译成 `TaskSpec` 进 `default_suite()`，那时再动已有评测也不迟。
