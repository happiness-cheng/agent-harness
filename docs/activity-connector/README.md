# Activity Connector：设计来源与 M1 边界

2026-11-05。用户提供五份历史材料，并明确确认 **144814 的 Final Errata 是本轮最终基线**。不再等待单独名为 Errata 2 的文件。

## 原文与溯源

两份设计文件原样存档，原文中的“本轮”“等待审核”“不实施”属于历史语境，不构成本轮操作指令；原文称 `[LOCAL]` 的调查结果也不等于本助手重新验证。

| 原文件时间戳 | 内容 | 仓库处理 | 原文件 SHA-256 |
|---|---|---|---|
| 20261104-110902 | Phase 0 环境调查 | 下方摘要；不复制个人设备路径/运行状态 | `a99091dfd5fcbab4499088916895fe5037e4ff4fedc937cd5c6d313bbc19f04e` |
| 20261104-112506 | Phase 0.1 数据一致性调查 | 下方摘要；不复制活动记录 | `d2e5e26a89d9220ff8b7113062d1e2d29e5a979a80b7d389e4a6b6bfc75e641b` |
| 20261104-132246 | Phase 0.1.2 身份与语义闭环 | 下方摘要；不复制私人 thread 标识 | `6c065eddf20bac8925996e1d0eb646c1e187aa7dbb94dc0ed6f420b889a4186a` |
| 20261104-143536 | v0.2.4.2 修正报告 | [旧设计原文](uem-v0.2.4.2.md)，被 Final Errata 修正处不再采用 | `422480d00f9a4eb22b23c1407e5b26ec2d7bca5450d021ef419e92908394725b` |
| 20261104-144814 | v0.2.4.2-final Final Errata | [本轮冻结基线](uem-v0.2.4.2-final.md) | `327c23cb707ae18e07540be1236f9ec2000acdd29de543a0ffe7e0fd9213c1e3` |

## 历史调查中的因果与纠正

以下数字仅转述用户提供的历史报告，未在本轮重新扫描原始数据；不据此证明当前平台格式或版本行为。

- **Phase 0** 从本机格式调查出发，将历史补录与实时 Hook/插件分开。代码里存在事件字符串不等于运行时接口可靠，实时接入当时未验证。
- **Phase 0.1** 报告 213 个 Codex 文件、46 个 DSH 文件解析完成。`213` 已包含归档目录，不应再加一次归档数量。Codex 物理路径失效可能源于归档/压缩，不能直接判为数据丢失；偏移需要区分物理字节与解压字节。
- **DSH seq/seq0**：历史旧格式模拟仅按 seq 续读漏掉 401 条，说明两个命名空间不能折为一个序号；双游标能解决部分续读问题，但不能独自表达修订关系。
- **Phase 0.1.2 修正 Codex 分类**：16 个双 UUID 文件是续写片段，17 个多 metadata 文件是另一组 Fork；先前混为一类的判断被纠正。9 对文件出现 109 个 ordinal 碰撞且 payload 不同，否定跨片段直接用 `(thread_id, ordinal)` 去重。单文件连续不等于跨文件唯一。
- **相同内容不等于同一事件**：历史报告区分 29 种内容与 119 条记录；后者 ordinal/时间不同，应保留来源身份。最终设计进一步把 fingerprint 移出 record locator ID，避免内容变化生成新 ID 而绕过冲突检测。
- **DSH old/v4**：两种日志有共享记录，也分别保留过程层与结构化层；v4 不是全部事件的唯一权威源。`isSeeded=false` 不独自证明迁移。Phase 0.1.2 纠正了特定 v4 `sourceEventSeqs` 指向旧 seq0 的旧判断，不能外推所有格式的引用空间。
- **工具结果修订**：报告对 76 条额外 result 逐条检查，判为紧邻 prune 的 replace 写入，不是 76 次新调用。配平数量仍不证明唯一配对，报告保留 1,313 个调用关联为 AMBIGUOUS；不强行关联、不删除历史。
- **throughSeq**：从早期“可能是持久化水印”修正为下游确认信号；不能用它替代本地补录游标或磁盘持久化证明。

这些反例引出稳定身份、来源定位、内容完整性、运行位点和语义关联的分离。旧调查提出的哈希/指纹去重方案是演变过程，最终规则以 Final Errata 为准。

## Final Errata 直接修正了什么

1. 可变 Snapshot Head 改为不可变 Root；latest 和快照链移到 Observation。
2. 身份注册中移除 status；状态通过 append-only StateEvent 与 fold 表达，状态变化不增加 Manifest revision。
3. root_evidence 从 root ID 输入移出；身份映射必须显式追加，无法证明 root 时不猜。
4. fork_anchor 排除 snapshot ID 等物理表示，改用逻辑内容、流和位置。
5. migration 在 semantic rewrite 之前分类；Schema 变化不能判为同代语义重写。
6. Checkpoint 代际转换需要原子性和明确恢复状态；这属于 M2，本轮未实现。

同时补上 locator 必填项、多候选禁止自动映射、manifest ID/hash 绑定和 A/B/C 权限阶段。不是推翻整体架构，而是修正内部不一致。

## M1 实验 profile

实现：[activity_identity.py](../../src/min_agent/activity_identity.py)、[activity_store.py](../../src/min_agent/activity_store.py)。这是一套独立实验 API，无生产入口、无 Connector。

| 项目 | 当前支持及限制 |
|---|---|
| 存储 | 一个独立 SQLite 文件，Manifest / Observation / Checkpoint 分表；不修改 SessionStore schema |
| 身份 | 单 object、明确原生 branch key 的 root member、初代 generation；可追加 stream 注册以验证 Manifest revision 链 |
| 初代 generation | 实验字段 `{source_member_id, generation_epoch: "INITIAL"}`；不是完整多代协议 |
| stream | `{source_member_id, seq_namespace}`；初始 `synthetic.ordinal` 有 Checkpoint，额外 stream 只测试注册 |
| Snapshot | generation + 内容/文件 SHA256 + compression + 两种长度；本轮仅 `compression=none`，保存合成 bytes 的 hex |
| 状态链根 | `state_event_log` 域 + manifest_id，作为实验 profile 的确定性逻辑日志根；不由可变首个事件内容派生 |
| 状态 | identity_status、checkpoint_status、root_status 的限定单向转换；逆转终态报 `state_fold_conflict`，回滚且冻结当前写入实例。没有持久化冲突候选；重新打开只可基于未冲突的已提交链继续 |
| provisional | 不创建 unresolved/provisional member，不实现 IdentityTransition 映射；这里的 identity_status 测试不代表身份映射已完成 |
| latest / fold | 从经验证的 Observation 追加链派生，Manifest 不保存可变 latest/status |
| Checkpoint | 初始位点、单调字节位置与 revision/hash CAS；仅对已保存合成快照定位，不证明业务事件提交顺序 |
| 恢复 | 保留完整恢复 JSON 后重建全新 SQLite schema/行；不是复制数据库，不从最终哈希反推 Root |
| 校验 | 每次读写检查全部持久链、归属、确定性 ID、状态与位点。小样本 O(n) 验证；不承诺大日志吞吐 |
| 哈希边界 | 能检测不一致/损坏；不是数字签名。全部数据和 head 一起被替换为自洽旧备份时，缺外部可信锚，不能声称防恶意回滚 |

JCS 仅实现本轮需要的字符串/整数子集，拒绝 float、大于安全整数范围的数、重复 JSON 键和孤立 surrogate；只对 logical_id 预先做 NFC，其他字符串保持原值。依据 [RFC 8785 §3.1、§3.2.2.2、§3.2.3](https://www.rfc-editor.org/rfc/rfc8785.html#section-3.2.3)，不是完整浮点 JCS 实现。

可复现命令：

```powershell
.venv/Scripts/python.exe -X utf8 -m pytest tests/unit/test_activity_identity.py tests/unit/test_activity_store.py -q
.venv/Scripts/python.exe -X utf8 scripts/activity_m1_experiment.py
```

脚本仅在自动清理的临时目录创建合成库；`--report <新文件路径>` 可保存公开结果，已有路径拒绝覆盖。阶段交付、失败历史和 M2 启动条件见 [M1 阶段记录](../phase-records/2026-11-05-phase0b-m1.md)。

原文件 SHA256 针对用户提供的原始字节；Git 可能规范化 CRLF/LF，因此读取仓库 blob 时不能把换行差异误判为设计正文变更。原文的文字和规则未改写。
