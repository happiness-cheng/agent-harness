# V1 复验问题修复计划

目标：修复已确认的进程退出盲区、长原文读取回归与 lock 行尾；保持已有 V1 契约、权限、预算和历史证据。

用户已授权连续修复与离线验收，不追加实现审批、不提交、不调用真实模型。本轮按 brainstorming / writing-plans 梳理设计，按 TDD 保存 RED/GREEN。

## 已有系统与复用

已读 AGENT_VISION、DESIGN、ACCEPTANCE 与当前 evaluation、application、进程管理、测试。早期设计文档保留历史描述；V1 的当前事实以 EVALUATION_V1.md、源码与独立复验为准。

TaskSpec → runner → 隔离 worker → HarnessApplication → 原 Runtime/Session/Registry/Research/Memory → 父进程只读业务快照 → Verifier → result/compare/improve。无需新增 Agent Loop。五类任务、人工语义审查、受限候选、同契约比较均已运行；本次只修复失效边界。

## 评价场景与固定判据

1. 父进程自然退出，后代重定向标准输出后继续持锁：stop_owned 返回前必须释放锁。
2. run_owned 正常返回时也必须清理其残留后代；保留根进程 stdout 与退出码。
3. 原超时、仍活动父进程终止、调度器恢复仍通过；并行独立任务不得互相终止。
4. Windows 建立进程所有权失败时，命令不得开始执行；不能回退为不受管理的进程。
5. 长原文完整性仍要求完整送达后 completed；输出预留、生产窗口发现与压缩策略不变。调查后发现原 32K Mock 连当前完整轮都无法装入，新增显式 64K 合成能力夹具验证完整送达，并保留原 32K 输入验证 partial/context_limit 与完整原文留存。配置变化单独记录，不宣称同输入下 Memory 能力提升。
6. 完整 pytest 无未解释失败；固定 20 题仍为 15 PASS / 2 FAIL / 3 UNKNOWN；旧候选重放仍通过。

## 进程方案与取舍

采用每次调用独立 Windows Job Object，设置 KILL_ON_JOB_CLOSE。先以 CREATE_SUSPENDED 创建子进程，成功加入 Job 后才恢复其主线程；由 Job 管理后代，终止后查询 ActiveProcesses=0，再返回。ctypes 使用标准 Win32 接口，无新增依赖。Job 不开放 breakaway。

不采用 taskkill 加重试：父 PID 已退出时无法可靠定位其后代；不采用周期扫描 PID 树：容易错过极短生命周期的父进程并引入 PID 复用风险。新增 `_windows_job.py` 隔离平台实现，`processes.py` 保留 start/stop/run 接口，runner 正常结束也清理。

失败时保留 UNKNOWN/异常与证据，不将“管道已关闭”作为退出证明。此方案管理本次可信进程生命周期，不是恶意代码沙箱，也不能撤销远端请求。非 Windows 保留专用进程组，相关本机未实测项明确列出。

官方依据：Microsoft Learn Job Objects §Managing Processes in Jobs / §Terminating Jobs；Process Creation Flags §CREATE_SUSPENDED；AssignProcessToJobObject §Remarks；ResumeThread §Return value。URL 将列入交付说明。

## 执行步骤

- [x] 在 tests/unit/test_evaluation_processes.py 增加自然退出、正常返回、同级隔离的可观察进程测试，先跑 RED 并保存日志。
- [x] 实现 evaluation/_windows_job.py、接入 processes.py；最多三个文件一组，运行原进程与新增测试 GREEN。
- [x] 接入 runner 正常退出清理与无法确认清理的 UNKNOWN 记录；运行 evaluation 边界测试。
- [x] 独立子任务按原失败测试诊断 Memory 长原文问题；证实容量假设过时，只改工程夹具、保留原 32K 暂停回归与所有原文断言；生产源码和冻结 TaskSpec 不变。
- [x] 只规范化两份 lock 的 CRLF，保持依赖内容不变。
- [x] 对变更进行独立代码审查并处理发现，冻结最终源码后运行完整 pytest、20 题、旧候选重放、构建和独立安装。
- [x] 保存源码增量、哈希、报告与 Windows 操作说明；列出当前实现、离线实测、历史真实模型证据及未验证项。

回滚依据：before/ 保留本轮前源码和测试；不自动执行回滚或删除。不把旧报告改成新的通过记录。

调查证据补充：memory/current-only-diagnosis.json 中 current_only_bound=28008，而原配置允许输入27648。最初“只需压缩旧历史”的假说已被否证，因此不实施该生产修改。子任务诊断及工程fixture已交付，随后因代理额度停止；主执行者接管后续验证。
