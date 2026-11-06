# 最小只读 Activity Connector

2026-11-06；Task ID ADR-RO-001。M1–M4 限定机制通过后，用户明确授权只读真实历史；M5 暂缓、不单独启动 M6。

## 结果与使用

本批实施完成，等待独立审核。**222 passed / 0 failed / 0 skipped**（新 Connector 40、旧相关回归 182）；真实四文件 **264 parsed / 0 failed**，双遍稳定、重复结果一致、前后文件 hash 不变。不把四样本成功外推成全部平台兼容。

```powershell
.venv/Scripts/python.exe -X utf8 -m min_agent.activity_connector "$HOME/.codex/sessions" --max-files 5 --output reports/codex-activity.json
.venv/Scripts/python.exe -X utf8 -m min_agent.activity_connector "$HOME/.dsh/sessions" --max-files 5 --output reports/dsh-activity.json
```

安装、退出码、限制和字段解释见 [CLI 文档](../activity-connector/readonly-cli.md)。输出仅新文件，拒绝覆盖或写进输入目录。

## 实现与依据

- [activity_reader.py](../../src/min_agent/activity_reader.py)：rb 流式读取、zstd 帧完整性和串联、有界行/文件/记录数、双遍指纹与 stat。
- [activity_connector.py](../../src/min_agent/activity_connector.py)：实际头部格式识别、DSH seq/seq0 分离、白名单元数据、时间范围/时间线、provisional/unresolved、本地 CLI。
- [test_activity_connector.py](../../tests/unit/test_activity_connector.py)：固定脱敏合成正反例、真实 subprocess CLI、只读/隐私/资源边界。
- pyproject 添加 zstandard==0.25.0 的 activity 可选依赖及 dev 验收依赖。复用 identity/records，不修改 M1–M4 Store，不接 M3 写路径。M4 需要整份 bytes，流式 Connector 不调用其纯内存执行器，不冒称持久变化分类。

## 验证与失败

```powershell
.venv/Scripts/python.exe -X utf8 -m pytest tests/unit/test_activity_connector.py tests/unit/test_activity_m4.py tests/unit/test_activity_m3.py tests/unit/test_activity_identity.py tests/unit/test_activity_store.py tests/unit/test_activity_m2.py tests/unit/test_checkpoint_resume.py tests/integration/test_checkpoint_persistence.py -q --tb=short
```

最终实际运行另带 JUnit 参数；完整命令、逐项结果、代码指纹及初始/最终脱敏真实结果见[验收 JSON](../acceptance/activity-readonly-20261106.json)。耗时 22.66s；compileall 通过。全仓和独立 Type/Lint 未运行，独立审核待用户安排。

首轮真实读暴露 Codex world_state/token_usage_record 不支持，以及 DSH 流式 zstd 窗口被错误限制。保留失败统计并加入三个合成反例，实际 3 FAIL/31 PASS 后最小修复，最终通过。不改变冻结身份设计、不掩盖失败。

| 真实样本 | 成功记录 | 失败 | 只读/稳定/重复 |
|---|---:|---:|---|
| Codex JSONL | 31 | 0 | PASS |
| Codex zstd | 7 | 0 | PASS |
| DSH v4 zstd | 7 | 0 | PASS |
| DSH old/version 0 zstd | 219 | 0 | PASS |

DSH old 非头记录 seq=59、seq0=159，各自命名空间。全部样本共 260 个非头记录保持 provisional；没有确认 Fork 或合并副本。真实原文未保存、未上传；公开证据不含路径、原生 ID、正文、原始文件 hash 或活动时间线。

## 限制与停止点

只读报告允许 KNOWN_LIMIT，不代表 Event Store/Memory 准入。只支持已观察格式；未知版本/结构、半行、损坏流、读取变化均明确报告。双遍稳定不是 OS 锁定快照；报告有大小/展示上限；浮点超出既有 Fingerprint canonical 子集时标 unsupported。来源路径匿名化，诊断时可用单文件命令定位。

没有持久 Checkpoint、生产写入、自动身份合并、Hook/插件/服务、模型或 M5/M6 实施。下一步只做独立审核：固定测试、元数据脱敏和源文件不变验证。停在本批，不进入持久化或 Memory 集成。

工作分支 adrian/phase0b-m1；原 AGENTS.md 和无关资料保留，Git 实际结果随后追加。
