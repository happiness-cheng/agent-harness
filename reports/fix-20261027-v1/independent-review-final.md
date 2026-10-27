# 代码复审结果

Verdict: PASS

此结论针对 `review-final-hashes.json` 记录的六份文件；是首次审查两个具体问题修复后的代码复审，不替代全量测试、冻结任务验收或跨平台实测。首次 FAIL 记录保留在 independent-review.md。

## 发现的问题

当前审查范围没有新增 CRITICAL、HIGH 或 MEDIUM finding。

### 首次 HIGH：查询失败未发起终止 — 已修复

`WindowsJob.stop` 现在把停止新增、句柄枚举、终止、等待全流程置于异常保护中。任一错误会显式关闭 KILL_ON_JOB_CLOSE 句柄并继续抛出，`drained` 不置为 true。上层仍保存 UNKNOWN，不能将错误关闭当作成功确认。

已独立执行新增 `test_job_query_failure_still_initiates_owned_tree_shutdown`，通过。

额外 Windows API 路径探测见 `review-openprocess-paths/result.json`：

- 对属于本 Job 的已知后代注入 OpenProcess error 5：stop 抛错，Job 关闭，drained=false，最后确认锁释放。
- 对已知后代在 OpenProcess 前发出自然退出标记，等待其确实消失：真实 OpenProcess error 87；stop 正常返回，Job 关闭，drained=true，锁已释放。

两条路径应有所区别：error 5 是不能完成归属/退出确认，需要 UNKNOWN；error 87 此处表示原枚举成员已消失，在停止新增边界及其他句柄等待、最终活动数核对都成立后，可以完成清理，不必伪造失败。

### 首次 MEDIUM：清单快照后的新后代 — 已修复

`stop` 在枚举前调用 `_prevent_new_processes`，读取并保留原扩展 limits，仅在停止阶段追加 ACTIVE_PROCESS 并把 ActiveProcessLimit 设为 1。已有成员数至少为 1 时，再建后代会越限；成员数为 0 时已没有生成者。该门在执行阶段不启用。

此前独立 API 实测保留在 `review-active-limit-probe/result.json`：1 个未设门对照正常执行新命令；2 个门控重复均在原 9 成员不被终止、原锁持续持有的同时，拒绝新创建（WinError 1816）。全部已清理。这是本机 Windows 的有限实测，不据此声明所有 Windows 版本已验证。

已独立执行新增的受控快照后创建测试，确认创建被阻止；同级 Job 隔离测试也通过。没有再次运行旧的必须等待 child-ready 的历史探测脚本，因为门控后的正确行为是 spawn-blocked。

## 独立执行结果

| 范围 | 结果 | 证据 |
| --- | --- | --- |
| 查询失败、快照后新建、同级隔离、登记失败不执行 | 4 passed / 9 deselected，36.72 秒 | review-final-tests.txt |
| Runner 正常清理、清理不能确认、启动失败 | 3 passed，31.84 秒 | review-runner-tests.txt |
| OpenProcess 5 / 真实退出后的 87 | 两条路径符合预期，探测锁均已释放 | review-openprocess-paths/result.json |

额外阅读了 CodexCandidateModel、night_executor、improvement 的调用与失败传播。Memory 变更仅为工程测试明确合成容量并保留原 32K 暂停用例，未修改生产窗口、权限、原文读取实现或冻结 TaskSpec/Gold。

## 总结

| Severity | Count |
| --- | --- |
| CRITICAL | 0 |
| HIGH | 0 |
| MEDIUM | 0 |

非 Windows 分支本次未实际执行，不列为通过。外部模型请求为 0；本审查没有读取凭据、正式 Memory 或生产数据库，也未修改项目源码与测试。
