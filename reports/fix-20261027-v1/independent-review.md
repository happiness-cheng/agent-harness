# 代码审查结果（首次审查）

审查范围：evaluation/processes.py、_windows_job.py、runner.py 与对应进程/清理/Memory 测试。检查了 Codex、夜间回归和改进调用方。审查只读实现文件；仅新建合成探测目录及本记录。当前 `~/.codex/rules/review-checklist.md` 不存在，按传入审查角色的具体失败/准确位置/调用上下文/严重性四项门禁执行。

## 发现的问题

[HIGH] 进程枚举失败会在发起终止前退出，依赖析构才可能停止任务

File: src/min_agent/evaluation/_windows_job.py:147

Issue: `_process_handles()` 在 try/finally 外执行。任一清单或进程归属查询错误使方法直接抛出，既未调用 `TerminateJobObject`，也未关闭 KILL_ON_JOB_CLOSE 句柄。`stop_owned` 因而报错时，其公开返回的 Popen 与 Job 仍可由调用者/异常栈持有，自有任务继续运行与持锁；不应依赖对象随后被回收来发起停止。`review-query-failure/result.json` 实测 `stop_raised=true`、`root_still_running_after_stop_error=true`、`lock_still_held=true`。本次没有证实 subprocess._active 留存（实测 false），不作该推断。探测后正常终止并重新取得锁。

Fix: 将枚举、终止、等待的全流程纳入异常保护；即使不能取得完整句柄清单，也应尝试终止或关闭本次 Job，再保留 UNKNOWN/原错误。增加查询失败后命令不能继续运行的回归，而非仅检查异常类型。

[MEDIUM] 清单快照后新建的后代没有被等待，仍可在正常返回后占用资源

File: src/min_agent/evaluation/_windows_job.py:147-149；161-164

Issue: `_process_handles()` 与 `TerminateJobObject` 之间仍允许 Job 成员创建后代。新后代虽被 Job 终止，却不在待同步的句柄集合；该成员再次只依赖 `ActiveProcesses==0`，重现已观察到的异步清理窗口。合成探测在清单快照后允许根进程正常创建重定向输出、持锁的后代，并等待根进程退出，再继续原 stop。`review-late-child/result.json` 的 8 次受控调度中，2 次 stop_owned 正常返回后锁仍占用。该 2/8 是诊断样本，不是总体发生率。全部探测已结束，未操作现有服务。

Fix: 在获取同步句柄集合前建立不再新增成员的边界，或采用完整的生命周期句柄跟踪，然后终止并等待。单次清单、重复 ActiveProcesses 查询、固定 sleep 均不能补足成员集合的竞态证明。可评估限制本次 Job 的新增进程能力，但必须核实改变限额是否会提前异步终止现有成员，不能直接套用未经验证的冻结方案。

## 总结

| Severity | Count |
| --- | --- |
| CRITICAL | 0 |
| HIGH | 1 |
| MEDIUM | 1 |

Verdict: FAIL

这是首次实现版本的审查结果，后续修复应另附复验结论，不回写成原版本通过。Memory 修改为工程测试声明合成容量，并保留原 32K 安全暂停输入；未发现其修改生产窗口或冻结 Evaluation TaskSpec/Gold 的情况。
