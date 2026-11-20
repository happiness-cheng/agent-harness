# Adrian × Personal Memory：最小候选契约 v0.1

本契约仅用于 ADR-PM-ADAPTER-001 的合成隔离验证，不授权正式 Vault、插件、队列或 Memory 写入。依据 PM《核心数据模型v2》§2–4、§10、《增量导入设计v2》§2–5，以及 Pilot 01 用户最终审核决定 §1/§5；不复制其私人候选或原话。

## 字段对照

| 含义 | Adrian | 隔离候选 / PM 对照 |
|---|---|---|
| 平台 | codex/rollout、dsh/v4、dsh/old | platform=Codex/DSH；契约保留 ChatGPT/DeepSeek 枚举，但本批没有对应 Adrian 输入适配 |
| Source | source_ref 是匿名物理来源引用，object_id 是逻辑对象 ID | source_id=src_<隔离报告摘录实际字节 SHA256>，kind=aux；不将 source_ref/object_id 替换成 Source ID |
| Record | native_record_id / provisional_record_id | 原值写入 adrian_ids，Activity 新 act_<全 SHA256>；identity-map.json 显式关联，不改原身份 |
| Locator | line_index、logical_byte_offset、physical_byte_offset、seq_namespace/value | 保留同名字段及坐标空间；不把 JSONL 行号当 PM message_index，也不为压缩数据推造物理偏移 |
| 报告引用 | /files/i/timeline/j | 生成的 Source 卡 block anchor 有效；reference_validity=valid_report_reference |
| 原始引用 | 原路径/消息正文不在报告 | original_reference_validity=unverified 或 missing_locator；不生成虚假的原文 message_ref/quote/role |
| 活动时间 | 未提供现实活动证据 | event_at=null、event_at_basis=unknown |
| 对话时间 | 只有来源记录 timestamp | conversation_at=null；timestamp 原值单独映射 source_recorded_at，不自动视为会话/现实事件时间 |
| 观察时间 | 报告未提供明确 observed_at | 沙盒首次创建时间 observed_at，重复转换保持不变；不替代上述两个时间 |
| 活动证据类型 | 元数据，无正文 | activity_kind=conversation（仅候选预设），activity_subtype=agent_activity_observation；status=unknown；不允许 user_reported_event/externally_verified_event |
| 不确定性 | provisional/unresolved/KNOWN_LIMIT、稳定/完整状态 | 全部保留为身份状态和固定 known_limit；不因能渲染/能跳转而升格验证 |
| 审核 | 无用户审核证据 | review_state=draft、verification=unverified；不复制输入里的 confirmed/corrected |
| 敏感字段 | 可能有未来扩展 | 只取严格验证的元数据字段；正文、任意摘要/标题、凭据、健康/private/quarantined 标记的记录不输出 |

## 不兼容清单与裁决

1. PM v2 的 Activity evidence_refs 要求可回到消息；当前报告无正文、role 或 message_index。因此候选携带报告级引用扩展，`eligible_for_import=false`、`compatibility=blocked_missing_message_evidence`。它是待补证的 Agent 活动观察，不声称满足正式 Activity/Memory 准入。
2. PM Source 是内容身份，Adrian Locator 刻意不含 Fingerprint；两者必须共存。Source 仅代表实际生成的白名单报告摘录，不冒充原始 Codex/DSH 文件。
3. PM 模型列 DeepSeek/ChatGPT/unknown，新增 Codex/DSH 只能在本实验扩展字段表示，正式模型/插件未修改。ChatGPT/DeepSeek 本批仅保留契约与 ID 命名空间，输入适配 NOT IMPLEMENTED。
4. 现有插件审核队列使用 candidate_kind、needs_review/deferred 等字段，与 v2 draft/confirmed/corrected 并不完全同构。本批只渲染 Markdown，不写插件 review-queue，也不自动调用确认函数。
5. Pilot 01 历史质量报告中自动标记的 confirmed/“100% 人工通过”被后续用户决定明确否定；最终显式用户决定才有效，不追认旧标记。本批不复制任何已审核内容，不创建正式 Memory。

## ID、文件与权限

Activity ID 输入为契约版本 + platform + Source ID + 报告记录位置，平台域隔离；所有 Adrian ID 原样保留。相同输入生成同一候选，命中相同文件完全 no-op；任何已有不同内容都不覆盖，包括 draft/confirmed/corrected。

CLI 仅生成内置合成输入，通过现有只读 Connector 形成报告后转换；没有私人历史输入参数。输出只在自身新建的 OS 临时沙盒，保护正式 Vault。Source 卡使用 Obsidian 内部 block 链接，候选正文直接显示审核、三种时间与引用边界。正常 Obsidian 显示不等于插件导入成功或人工审核。
## 最小 CLI 与验收边界

在 Adrian 仓库运行（只生成内置合成数据）：

```powershell
.venv/Scripts/python.exe -X utf8 -m min_agent.activity_memory_adapter
.venv/Scripts/python.exe -X utf8 -m min_agent.activity_memory_adapter --sandbox "<上一条返回的 sandbox>"
```

第二次转换 `written=0`；目录内 `vault/Candidates` 是候选，`vault/Sources` 是报告摘录及证据卡，`identity-map-*.json` 是显式映射。观察时间取沙盒首次创建时间，不随重跑漂移。CLI 不接受实际历史路径；Python 转换函数是本实验的合成报告接口，不是自动敏感内容识别器。只复制白名单字段，未知限制文本不导出；已知 Connector 限制按固定常量保留。

本批自动验证 YAML、时间字段、来源 hash、链接目标和块锚存在。当前环境注册的 Obsidian 程序路径不存在，启动报 WinError 2，**实际 Obsidian 渲染未验证，整批验收 PARTIAL**。恢复有 Obsidian 的隔离环境后，用新临时配置打开本命令生成的 `vault`，检查候选页面三种时间、draft 和报告证据块跳转；不得用正式 Vault 作测试目录。

并发恶意文件系统替换、崩溃原子多文件提交、正式插件审核队列、原始消息回溯与真实导入均未实现。已有不同候选一律保护；证据文件不一致时拒绝本次写入。正常单进程重复运行幂等，不声称跨进程事务。
