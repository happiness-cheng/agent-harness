# Adrian × Personal Memory 第一批 · ADR-PM-ADAPTER-001

日期：2026-11-06。状态：**原型已实现；整批验收 PARTIAL，等待 Obsidian 实机显示验证及独立审核**。

## 完成和取舍

- 使用现有只读 Connector 扫描内置 Codex/DSH 合成样本，读取其 JSON 报告，生成独立临时沙盒候选 Markdown、报告证据卡与显式身份映射。正式 Vault 和插件不写入、不初始化 Git。
- [字段契约与使用](../activity-connector/personal-memory-contract-v0.1.md)区分 Source 摘录身份、原 Adrian ID、Locator 和引用有效性；来源记录时间、现实活动时间、对话时间、观察时间分开。只生成 draft/unknown 的 Agent 活动观察，不推断用户完成现实事件、偏好或知识。
- Source hash 对应实际写出的元数据摘录字节；不冒充原历史 hash。保留原 ID，不替换或合并。缺消息正文使 `eligible_for_import=false`；当前 PM 插件审核队列与该候选契约不兼容，未改队列。
- 重复转换不改已有文件；任何既有不同候选都保护，包括 confirmed/corrected。不读取健康隔离历史，不调用模型，不导入私人历史，不创建正式 Memory。

## 验证和边界

- 自动测试与脱敏证据见[验收 JSON](../acceptance/personal-memory-adapter-20261106.json)。包含幂等、四平台 ID 隔离、引用诚实性、敏感字段白名单、隔离标记拒绝、保护笔记、合成输入不变、CLI 与 M1–M4/Connector 回归。
- 实际 Obsidian 启动失败：注册路径对应可执行文件不存在，WinError 2。YAML/链接/块锚结构检查不能替代实际渲染，故整批不标 COMPLETED；没有启动或修改正式 Vault。
- 只对事前允许的 12 个 PM 文档/插件/非健康已审核笔记做前后指纹核对，12/12 不变；这不是全 Vault 字节审计。真实平台原始文件未访问；原始文件不变自动测试使用合成哨兵。
- ChatGPT/DeepSeek 只定义平台命名空间，输入适配未实现。正式消息引用、队列导入、并发写事务、复杂 Fork/副本确认、M5/M6 均未实现。无独立子代理审核。

## 下次启动条件

仅在独立审核或恢复隔离 Obsidian 实机环境后继续本批：先重跑合成 CLI，再用独立配置打开生成的临时 vault 验证 draft、时间和链接，不打开正式 Vault。禁止自动进入正式同步、Memory 导入或仓库合并。

代码：`src/min_agent/activity_memory_adapter.py`；测试：`tests/unit/test_activity_memory_adapter.py`。工程三册使用 Task ID ADR-PM-ADAPTER-001。提交版本见 Git 日志与本阶段后续追加记录。

## 提交与停止

源码提交 `c556a515ac303e50596e466f20d58171bb6503a5` 已同步 GitHub `adrian/phase0b-m1`，远端哈希核对一致。259 passed/0 failed/0 skipped（37 Adapter + 222 回归）；本追加不改已测源码。整批 PARTIAL：仅 Obsidian 实机显示验收尚未完成；等待独立审核，不启动正式导入。
