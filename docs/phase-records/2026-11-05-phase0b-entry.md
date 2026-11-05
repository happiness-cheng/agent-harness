# Phase 0B 入口：归档与缺失基线

日期：2026-11-05。Task ID：ADR-ARCHIVE-001。状态：PARTIAL；M1 输入阻塞。

## 本批目标与边界

按用户任务先保存设计演变、处理独立 Git 提交，再实施 M1 三层存储与重建实验。必要验收为 A 身份稳定、B root/latest 分离、C 状态 fold/冲突、D 重建 ID 一致、E 损坏后拒写。仅合成数据，不做完整 Connector、M2/M3、Hook、插件、真实 Memory 或模型调用。

## 完成与未做

- 完成十阶段 [ADRIAN_EVOLUTION](../ADRIAN_EVOLUTION.md)，增加 AGENT_VISION 和 PROJECT_STATUS 入口，AGENTS 仅新增用户指定的长期同步规则。保留原历史正文；后续历史来源标为本次用户说明。
- 核对 Git 起点 `50b27bfab099e7e4b3f085a9468d0b8bc6b44c4b`，创建工作分支 `adrian/phase0b-m1`。旧 AGENTS 工程制度修改及旧未跟踪资料保留，不整仓暂存。
- 未找到 Phase 0–0.1.2.4.2 原记录和冻结模型正文。M1 源码、测试、重建 ID 展示、损坏检测均未实施，A–E 为 NOT RUN。没有将缺少材料当成测试失败或架构缺陷。
- 远端读取受本机代理连接失败阻碍，缓存 origin/main 与初始 HEAD 一致不证明服务器没有新提交。

## 选择与影响

现有 AGENT_VISION 主要解释早期单 Agent 设计，新增跨阶段归档比重写历史更能区分时点。确定性 ID 与状态冲突依赖冻结契约，缺失时自行猜测会造成虚假的设计一致性，因此 M1 在拿到正文前暂停；归档与独立提交继续完成。

本轮工程记录使用既有三册的 Phase 分卷，以便只公开本轮记录，不夹带旧的未审核日志。没有更改工程日志制度。

## 证据与续接

- [决策分卷](../engineering-log/DECISION_LOG-2026-11-phase0b.md) → ADR-ARCHIVE-001。
- [实施分卷](../engineering-log/IMPLEMENTATION_LOG-2026-11-phase0b.md) → ADR-ARCHIVE-001。
- [验证分卷](../engineering-log/VALIDATION_LOG-2026-11-phase0b.md) → ADR-ARCHIVE-001，记录实际检查、提交与推送结果；未跑项目测试，不沿用旧测试数。
- **启动条件**：提供冻结设计及相关阶段文档路径。**第一步**：核对 ID 输入/规范化、Manifest revision/hash/parent、StateEvent 合法转换及哪些持久状态不可丢失；然后冻结 A–E 合成输入、运行现状基线、最小实现并验证。
- 当前不满足进入 M2 的条件。需要用户提供的是缺失的设计文件位置，不是再次批准已授权工作。

## 本地提交与同步结果

归档提交：`35aa2da` — `docs: archive Adrian evolution and establish work-branch sync rule`。
普通 push 已尝试，因 GitHub 443 经本机 127.0.0.1 代理无法连接而失败；未强推、未修改代理/凭据、未合并 main。提交保留于本地 `adrian/phase0b-m1`。连接恢复后可执行 `git push -u origin adrian/phase0b-m1`。本段及工程日志为随后追加的同步结果记录。
