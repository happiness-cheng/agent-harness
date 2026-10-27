# Adrian 统一验证 V1：复验与进程退出修复

日期：2026-10-27。结论：已有V1实现通过新版离线复验；本次发现并修复Windows子进程退出缺陷。真实模型及自主修复的旧证据保留，新版只进行离线验证，新增外部模型调用0。

本次附件与上次任务书逐字相同，SHA256均为 `fa0436ad9cc7e094f6673f01e2781be4706507614915a50f023d948d2c80841b`。因此复用已有Evaluation Harness，不再搭建另一套Agent或重复付费生成。

完整设计、组件数据流与Trade-off见[架构说明](../../../docs/EVALUATION_V1.md)；之前的模型原始回答、版本和成本见[原始交付报告](../REPORT.md)。本报告是追加记录，不替换旧报告。

## 1. Token与费用策略

已核对实际代码，Adrian网页入口和本评测live入口使用 `max_output_tokens=None`：

1. Provider从官方模型metadata取得 `context_window` 和 `max_output_tokens`，不猜固定窗口。
2. 每次原生请求构建时，根据当前请求剩余空间计算动态输出上限，取模型上限与可用空间的较小值。不是统一限制4096。
3. 输入按保守UTF-8字节估算并预留协议余量，不冒充精确Token计数。
4. 显式指定较小输出的调用者仍可使用自己的配置；旧SDK默认4096为兼容保留，但不是Adrian网页/评测真实入口所用配置。
5. 模型最大容量、费用、请求次数、工具次数和超时仍是独立边界；不能把“不统一限制Token”实现成无限运行。

本轮调用次数由执行者控制，不逐次请示。已有真实证据足够检验主链路，本次新增调用为0，没有以花完余额为目标。未请求外部Judge、未传输私人资料。当前运行的既有网页进程未被重启，代码配置需正常重启后加载，不声称旧进程已热更新。

相关源码：[动态输出](../../../src/min_agent/llm/deepseek.py:65)、[网页入口](../../../src/min_agent/web/__main__.py:62)、[评测修复入口](../../../src/min_agent/evaluation/__main__.py:41)。

## 2. 本次复验发现的问题

第一次针对性复验：26通过、1失败，原始输出保留在[tests.txt](tests.txt)。失败是：

`test_actual_scheduler_process_crash_does_not_replay_running_job`

测试调用 `process.terminate(); process.wait()` 后，重新启动调度器仍得到 `DatabaseInUse`。没有忽略该失败或直接用旧的通过记录代替。

根因：本机Windows虚拟环境的 `Popen.pid` 是启动器，真正执行Python的解释器是其子进程。结束启动器、等待其句柄结束，不等于持锁的解释器已经退出。

先进行了一个探测，再进行五次受控重复探测，记录见[process-probe/repeated.json](process-probe/repeated.json)：五次均观察到启动器退出时子解释器仍存活，其中四次文件锁仍被占用；子进程退出后锁全部可重新获取。单次探测也可能直接获得锁，所以这是一种退出时序问题，不能靠“再跑一次通过”认定修复。

## 3. 最小修复及核心代码

新增 [processes.py](../../../src/min_agent/evaluation/processes.py)，统一管理本次创建的进程树。生活类比：关闭接单窗口不能证明后厨已经停止工作；超时需要停止本次任务的整组工作进程，再确认其输出管道关闭。

关键代码摘录，原文件18行起：

```python
def stop_owned(process):
    if process.poll() is None:
        if os.name == 'nt':
            taskkill = Path(os.environ['SystemRoot'])/'System32'/'taskkill.exe'
            stopped = subprocess.run(
                [str(taskkill), '/PID', str(process.pid), '/T', '/F'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
            if stopped.returncode and process.poll() is None:
                raise RuntimeError('无法确认本次子进程树已停止，保留未知结果')
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    return process.communicate(timeout=10)
```

Windows使用系统自带taskkill，仅针对自己创建且仍活动的PID及其后代；不使用进程名匹配，不结束其他Python或现有Adrian服务。非Windows为每次任务新建进程组，停止时终止该组；本机没有实际运行非Windows分支，标记NOT_RUN。

四个调用点接入同一个辅助模块：评测worker、Codex候选适配器、隔离项目pytest、改进闭环的计算器pytest。Codex超时或停止无法确认时记录失败类型、保留调用槽位、关闭失败路线，不自动再试。没有关闭预算或修改权限。

修复修改四份既有evaluation源码、两份工程测试，新增一个辅助模块和一个测试文件。对本次开始时210份工程文件的快照核对，其余204份逐字未变；包括原Runtime、Session、Memory、权限、Provider和工具实现。准确增量见[fix.patch](fix.patch)，自审与保护文件结果见[fix-review.json](fix-review.json)。未改变TaskSpec评分字段、Gold、测试任务或预期答案。测试中的Codex模拟边界从subprocess.run调整为run_owned，原有安全断言保留。

这是进程生命周期修复，不是新的通用沙箱。若父进程被外部直接强杀，不能保证其清理代码执行；无法验证的旧操作仍需挂起核对。结束本地进程也不能撤销已经到达远端的操作，原UNKNOWN和预算预留规则保留。

## 4. 实际执行结果

| 验证 | 结果 | 原始材料 |
| --- | --- | --- |
| 初次V1针对性复验 | 26 passed / 1 failed，保留锁问题 | tests.txt |
| 新增进程边界RED | 3 failed，缺少目标辅助模块 | process-red.txt |
| 停止后代、超时、正常退出、原中断恢复 | 4 passed，15.90秒 | process-green.txt |
| Codex参数/调用上限/费用前置/超时与未知停止 | 4 passed，1.19秒 | codex-boundary.txt |
| 新版完整pytest | **1247 passed / 1 failed / 3 deselected / 1 warning，154.17秒** | full-tests.txt |
| 冻结20题重放 | **15 PASS / 2 FAIL / 3 UNKNOWN**；全部符合原预期 | offline-suite/summary.json |
| 已保存Codex候选重放 | 加法FAIL→PASS；减法与乘法仍PASS；61 passed，0.88秒 | candidate-replay/improvement.json |
| 编译、pip check、wheel构建 | 通过，无缺失依赖 | build.txt |
| 独立安装wheel中的新模块与实际子进程 | 通过 | wheel-smoke.txt |
| 差异空白、自审及保护文件 | 通过 | diff-check.txt / fix-review.json |

全量唯一失败仍为改动前已有的：

`tests/integration/test_memory_tools.py::test_explicit_source_read_and_structured_recall_are_not_silently_head_truncated`

长原文读取后任务状态为partial而非completed，本轮没有扩大范围修它。不能宣称全量绿色。类型检查器及Python linter未配置，标记NOT_RUN，没有为通过检查安装新工具或更改依赖。

冻结20题中的FAIL是故意虚假成功和错误引文；UNKNOWN是超时、未知副作用和待语义审查。脚本模型调用50次，外部生成0。它证明工程机制，不是自主模型任务质量或泛化成绩。

新候选重放使用旧真实Codex生成的candidate.json，本次 `generator=SUPPLIED_CANDIDATE_REPLAY`、`model_invocations=0`，没有伪装成新模型生成。冻结三题契约digest仍为 `618fb6fd42b57564a35470388e6e8919e53e4bb417b50e9bccd0c72e86354aa8`；78份非目标源码受保护，修复没有自动合并。

相关Memory集成也已在隔离测试库复验：91项针对性测试通过，浏览器完成跨会话回忆、真实来源弹窗、Memory开关、普通任务和服务关闭，前端错误0，外部模型请求0。见[界面运行收据](../../adrian-integration/recheck-20261027-063256/browser/result.json)。这不是私人历史导入或真实模型语义评测。

## 5. 旧真实模型证据与本次边界

此前真实DeepSeek：三项合成任务，待办与跨会话Memory通过，研究冲突失败；六次生成请求，输入15306、输出1292、缓存输入12032，历史价格快照估算0.001302396～0.002604792美元，不是账单。本次没有重新查询价格或追加请求，不将历史价格称为当前报价。

研究失败仍保留：模型要求补充资料，没有调用研究工具。旧的独立输入变更提案仍未作为通过依据，未修改原判据。

此前真实Codex：一个CLI执行，受控隔离修复通过；CLI内部API请求数、费用与真实剩余额度不可观测，不把“一次CLI”冒充“一次API”。原始回答和收据见[旧报告](../REPORT.md)，本次未覆盖任何旧实验材料。

新版没有重新进行真实模型实测：**NOT_RUN**。此次改动只影响进程管理，已有生成内容通过离线重放检验。语义支持、通用代码修复、恶意代码OS隔离和真实10小时值守仍不是已验证能力。

## 6. 版本与复现

复验开始时源码SHA256：`5705d3e66d442d4dc7973b15e8a16e4d62753ebc8ace34678f31cbf482a74280`。

修复后源码SHA256：`f135af28a876b9c1d6660c1b87a2d1ed1dd3ebbcb00c3436176b112fda6cb4d7`。

源码快照位于 `source/src/`，增量为fix.patch。算法为worker.source_version，按路径和生产源文件正文计算；不能仅凭Git HEAD代替包含既有本地修改的实际代码。Git版本、逐文件SHA256和标准未修改检查分别见fix-review.json、artifact-hashes.json。原来336项记录在修复前核对全部一致；修复后只允许上述六份既有代码/工程测试变化，旧协议及实验收据不改写。

项目目录PowerShell可复现，输出目录必须使用新的名称：

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -X utf8 -m pytest -q
.\.venv\Scripts\python.exe -X utf8 -m pytest -q tests/unit/test_evaluation_processes.py tests/integration/test_evaluation_v1.py::test_actual_scheduler_process_crash_does_not_replay_running_job
.\.venv\Scripts\python.exe -X utf8 scripts/run_adrian_frozen_eval.py --source reports/eval-v1/recheck-20261027-063608/source --protocol reports/eval-v1/acceptance-protocol.json --out reports/replay-after-process-fix-001
.\scripts\evaluate-adrian.cmd improve --out reports/repair-after-process-fix-001 --candidate reports/eval-v1/codex-improvement/candidate.json
```

本次停止扩展。没有付费追加、生产数据库迁移、私人资料导入、自动部署、提交或远端推送。原Memory集成和启动功能保留，后续是否开展真实模型新批次或修复独立长原文问题，由下一轮目标决定。
