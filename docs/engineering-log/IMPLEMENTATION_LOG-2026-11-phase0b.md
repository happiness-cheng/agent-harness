# IMPLEMENTATION_LOG · 2026-11 Phase 0B 分卷

仅收录本轮可公开任务记录；沿用既有三册制度和 Task ID。

## ADR-ARCHIVE-001 · 2026-11-05 · Phase: Phase 0B 准备

### 执行前
- 状态：IN PROGRESS。目标：归档十阶段因果演变，追加用户指定 Git 规则，按独立工作单元提交；获得冻结基线后实施 M1 A–E 合成实验，完成后停止。
- 基线：HEAD `50b27bfab099e7e4b3f085a9468d0b8bc6b44c4b`，分支 main，追踪 origin/main。已有 ` M AGENTS.md`；未跟踪 `.workbuddy-ai/`、`.workbuddy/`、`docs/career-positioning-2026-11-05/`、`docs/engineering-log/`、`docs/tech-audit-2026-11-05/`。全部属于本任务之前的工作，保留，不整体暂存。
- 预期修改：ADRIAN_EVOLUTION、AGENT_VISION 的引用、AGENTS 的单条同步规则、PROJECT_STATUS 的新入口、阶段短记录及本任务工程日志；M1 源码/测试路径待冻结基线核对后确定。
- 依据：本轮用户任务书、README、AGENT_VISION、已公开设计讨论、现行工程日志制度。决策见 DECISION_LOG → ADR-ARCHIVE-001。
- 执行顺序：先归档与核对 Git → 明确冻结模型/验收输入 → 运行现状基线 → 最小三层存储 → 同输入 A–E 验证 → 记录/提交/尝试推送。无需新增文档审批。
- 约束：只使用合成数据；不读私人对话/凭据、不动生产库/Hook/插件、不调用模型、不实现 M2/M3、不合并 main、不覆盖历史。缺失设计材料时不伪造设计一致性。

### 执行中与阶段收尾
- 实际文件：`docs/ADRIAN_EVOLUTION.md`（十阶段因果与证据边界）；`docs/AGENT_VISION.md`（引用）；`docs/PROJECT_STATUS.md`（当前入口与输入缺口）；`docs/phase-records/2026-11-05-phase0b-entry.md`（阶段交接）；`AGENTS.md`（仅新增用户指定的一条规则）；本分卷与同周期 DECISION/VALIDATION 两分卷（三册职责分开）。原三册追加执行前快照及分卷指针，保留本地，未整体暂存。
- 原因与取舍：见 DECISION_LOG-2026-11-phase0b → ADR-ARCHIVE-001；实现过程未发现可据以修改冻结模型的实验证据。
- 检查：见 VALIDATION_LOG-2026-11-phase0b → ADR-ARCHIVE-001。
- 未完成：Phase 原记录来源核对、M1 全部源码与自动化测试、重建 ID 比较、损坏演示、M2 准入。冻结基线缺失，已请求设计文件路径；不扩大到私人历史搜索。
- 副作用：创建工作分支，新增/追加上述文档及 Git 暂存对象；无数据库、依赖、Hook、插件、生产配置或模型调用。原工作区修改保留。网络仅尝试 Git 远端操作。
- 状态：归档独立单元完成，整体任务 PARTIAL；M1 BLOCKED（所需输入缺失）。提交/推送执行结果随后追加，不预记成功。

### Git 与交付补记
- 归档已独立提交；推送结果见 VALIDATION_LOG-2026-11-phase0b 同 Task ID 的 Git 执行结果。
- 实际追加文件：本实施分卷、验证分卷、阶段短记录。用于记录同步失败和续接动作，未改其他源码或规则。
- 整体仍为 PARTIAL：需要冻结设计文件才能继续 M1；需要 Git 连接恢复才能同步 GitHub。没有需要用户重新审批的设计或权限变化。

## ADR-M1-001 · 2026-11-05 · Phase: 0B M1

### 执行前
- 状态 IN PROGRESS。目标：按用户确认的 144814 Final Errata 实现独立三层存储实验，验收 A–E 后停止，不开展 M2/M3。
- 基线 HEAD `3400eb2`，分支 `adrian/phase0b-m1`；既存修改 ` M AGENTS.md` 与 .workbuddy*/职业资料/旧工程日志/技术审计未跟踪目录均保留，不整体提交。fetch 再次因本机代理不可连接失败，远端状态 UNKNOWN。
- 用户已确认五份输入中的 144814 为最终基线，不再等待另一个 Errata 2。143536 为被修正的上一版；其他三份为历史调查材料，其原执行结果不当成本轮复验。
- 预期模块：新增 `src/min_agent/activity_identity.py`（确定性 ID 与受限 JCS）；`src/min_agent/activity_store.py`（三层实验存储与重建）；`tests/unit/test_activity_identity.py`、`tests/unit/test_activity_store.py`（固定合成验收）；`scripts/activity_m1_experiment.py`（可重现 ID/损坏展示）；必要设计来源、阶段记录、状态及现有三册分卷。
- 设计依据与取舍：DECISION_LOG-2026-11-phase0b → ADR-M1-001。验收输入/限制将固定在测试和阶段记录；首次运行缺实现应 FAIL，再实现同一判据。
- 禁止：读源平台私人历史/凭据、修改生产 schema/数据库、Hook/插件、模型调用、M2/M3 完整实现、合并 main。仅用户明确授权的独立合成数据库创建/重建。

### 执行中与收尾
- 实际实现：`src/min_agent/activity_identity.py`（JCS 子集与域隔离 ID）；`src/min_agent/activity_store.py`（Manifest/Observation/Checkpoint 三层、恢复验证）；两个 `tests/unit/test_activity_*.py`（固定验收与反例）；`scripts/activity_m1_experiment.py`（独立演示）。无生产模块接线。
- 归档文件：`docs/activity-connector/README.md`（五份来源指纹、调查因果摘要、实现 profile）；`uem-v0.2.4.2.md` 与 `uem-v0.2.4.2-final.md`（用户提供的设计原文原样存档，不复制原始私人活动日志）；`docs/ADRIAN_EVOLUTION.md`（追加材料补齐与实际编码结果）；`docs/PROJECT_STATUS.md`（最新状态）；`docs/phase-records/2026-11-05-phase0b-entry.md`（解除旧阻塞）；`docs/phase-records/2026-11-05-phase0b-m1.md`（交接）；`docs/acceptance/activity-m1-20261105.json`（可公开合成证据）；本周期三册追加本 Task ID。
- 实施偏差：初始采用默认 JSON 解析与宽松整数相等比较，发现与规范输入约束不一致；按已复现反例收紧。实验脚本显式关闭 sqlite 连接以支持 Windows 自动清理。设计依据/取舍见 DECISION 同 Task ID，具体失败及结果见 VALIDATION，同一事实不抄录测试明细。
- 未做：完整 Connector、多代 transition、业务事件与 locator、M2/M3、物理 instance/Fork/压缩、Memory 集成、模型效果。未声称完整 UEM 已实现。
- 副作用：独立临时合成数据库、一个公开 JSON 报告和上述源码/文档；测试缓存/pyc 由工具生成，不提交。首次演示清理失败遗留一个独立合成临时目录，后续成功运行已自动清理自身目录；未删除用户文件。原工作区未提交资料保留。无凭据、Hook、插件、原始历史、生产 schema 或依赖变更。
- 阶段状态：M1 限定 profile COMPLETED；可进入下一批 M2，本轮停止。GitHub 同步状态待实际普通 push，失败则保留本地提交，不视为同步完成。

### 副作用续记
- 首次演示失败遗留的独立合成目录清理未执行（自动策略拒绝）；保留 5 个合成数据库。具体检查/拒绝见 VALIDATION 同 Task ID。不影响之后成功运行，其他目录未动。

### 提交与精确版本证据续记
- 代码提交已创建，随后补齐提交 blob 对应的复验；实际验证及提交门禁失败纠正详见 VALIDATION 同 Task ID。
- 新增 `docs/acceptance/activity-m1-20261105-committed.json`，追加阶段记录与本分卷/验证分卷；设计来源 README 补充原始文件与 Git 换行规范化边界。保留旧证据，不覆写。
- 未完成项更新：GitHub 未同步；首次失败实验的独立合成目录清理被策略拒绝，仍保留。核心 M1 范围已完成，其他既定排除项不变。

## ADR-M2-001 · 2026-11-05 · Phase: 0B M2

### 执行前
- 状态 IN PROGRESS；目标：合成事件先持久化后位点、真实 SQLite 原子 Generation Transition、重开连接及进程中断恢复，完成 M2 后停止。
- 基线 HEAD `3c3d82f5aa81ebd374708964a2b991a273e7a3c8`；分支 `adrian/phase0b-m1`。既存 ` M AGENTS.md`；未跟踪 .workbuddy-ai/、.workbuddy/、career-positioning、tech-audit、旧 engineering-log 六文件，均非本轮所有，不清理/暂存。
- 预期模块：M1 Store 必要的多代校验复用、新 M2 Store、合成故障注入测试、阶段证据及三册；activity_identity 保持现有 ID 规则。Git 仓库级代理按用户授权 7890→7897，禁止改凭据或全局网络行为。
- 依据：Final Errata P0-6 和 M2 表、M1 阶段卡和当前源码；取舍见 DECISION 同 Task ID。先实际跑现状 M1，再写失败 M2 样本。
- 范围：只新建独立合成库；不自动迁移既有 M1/生产库，不读平台历史，不改 Hook/插件/Memory，不做 M3 Locator/Fingerprint、多用户、真实模型；不合 main，不强推。

### 实施与收尾
- 实际源码：activity_m2.py 新增独立 M2Store、事件消费、SQL 事务转换及启动恢复；activity_store.py 仅提取共用 Manifest 校验；test_activity_m2.py 新增合成重开连接/子进程故障和非法状态样本。修改为满足事件提交先行与原子转换，不接入 Runtime。
- 记录：新增 docs/acceptance/activity-m2-20261105.json、M2 阶段卡；更新 PROJECT_STATUS、activity-connector/README 和本周期三册。设计理由及替代方案见 DECISION 同 Task ID；验证与失败见 VALIDATION 同 Task ID。
- 实施偏差：共用校验提取时遗漏变量引用，已最小修复；没有据此修改冻结架构。自审由当前助手执行，无独立第二审核。
- 未做：M3–M6、完整 Locator/Fingerprint、旧库迁移、多对象/多流、真实 Connector、生产负载与掉电验证。阶段功能完成，Git 执行结果另补记。
- 副作用：隔离测试数据库/pytest 缓存，公开脱敏 JSON，原始 JUnit 保留于忽略的 reports/activity-m2-20261105/；仓库级代理改为 7897。无全局配置、凭据、生产数据或 Memory 修改；既有无关文件保留。

### Git 收尾
- M2 代码已提交为 `29af400`，工作分支普通推送完成；远端核对见 VALIDATION 同 Task ID。此次补记仅追加本分卷、验证分卷和阶段卡，不改代码。M2 状态 COMPLETED；本轮停止，不启动 M3。

## ADR-M3-001 · 2026-11-05 · Phase 0B M3

### 执行前
- 目标：三种 Locator Profile、独立三层 Fingerprint、幂等/持久冲突冻结和显式身份映射。仅合成独立库；不做 M4–M6、生产 Connector、真实历史或模型调用。
- 基线 HEAD 300d45f49e0fa90cca3b0f92faecfe239e8d92b0，分支 adrian/phase0b-m1。既存 AGENTS.md 修改、.workbuddy*、职业/技术审计和旧工程日志六文件均保留，不纳入提交。
- 预期修改：新 activity_records.py、activity_m3.py 和 M3 测试；M1/M2 仅必要的 stream namespace 参数与校验复用；阶段卡、状态、验收证据与本周期日志。
- 依据 Final Errata P1-1/P1-2/M3，原 v0.2.4.2 的定位字段公式。设计取舍见 DECISION 同 Task ID。执行顺序：现状基线→固定失败样本→实现→故障与回归→记录→精确提交/普通推送。

### 实际实施与收尾
- activity_records.py：三种冻结 Profile、DSH seq/seq0 区分、required 缺失降级、三层 Fingerprint 与确定性记录 ID；activity_m3.py：复用 M2 事务的记录/映射/冲突追加日志、启动校验、批次预检、持久冻结、恢复包；test_activity_m3.py：固定正反例及真实进程中断验收。
- activity_store.py 仅给初始 stream namespace 增加默认参数；activity_m2.py 仅将应用标记/版本/校验函数作为可覆写入口，并给内部校验增加 namespace 参数。默认 M1/M2 行为与固定键协议保持原样；不复制事务实现。设计理由见 DECISION 同 Task ID。
- 新增公开验收 JSON 和 M3 阶段卡，更新 PROJECT_STATUS、设计来源 README 和本周期三册。未修改 AGENTS.md，也未提交既有无关资料。实际结果与证据见 VALIDATION 同 Task ID。
- 实施偏差：无架构偏离；DSH 两条 stream 复用已有单流模型，分别使用独立实验库。Codex jsonl/zst 只接收已解压坐标的合成 envelope，不实现读取/解压器。缺少可选 old offset 使用 null；恢复 offset 后可能产生另一个 locator，保留历史，不隐式重写。
- 未做：真实格式解析、生产/旧库迁移、证据真实性外部验证、自动多候选消歧、M4–M6、吞吐/断电验证。冲突冻结该库全部写入，范围比单 locator 保守。独立审核待用户执行；本轮完成后停止。
- 副作用：隔离测试 DB、pytest/编译缓存、忽略的 reports/activity-m3-20261105/junit.xml 和公开 JSON；无真实历史、Memory、生产配置、模型或新依赖操作。未修改代理配置。

### Git 收尾
- 代码提交 75bec9d 已普通推送，精确门禁与远端证据见 VALIDATION 同 Task ID；本补记仅三份文档。实施/本地验收 COMPLETED，独立审核 PENDING，本轮停止，不启动 M4–M6。

## ADR-M4-001 · 2026-11-05 · Phase 0B M4

### 执行前
- 目标：冻结六类变化顺序、证据不足 unresolved、独立合成执行验证身份/Checkpoint 副作用。基线 fc8d76cfe75b52df28534db0bcdeffc45d91d39f，分支 adrian/phase0b-m1；既有 AGENTS.md 与 .workbuddy*/职业/审计/旧日志等未提交资料保留。
- 预期新增 activity_changes.py、test_activity_m4.py、验收 JSON 和阶段记录；更新本周期三册、PROJECT_STATUS 和来源 README。无需改 M1–M3 Store；依据 Final Errata 六类变化与 M4。
- 仅合成 bytes/临时路径，不读取真实历史、不改生产 schema/数据库/Hook/插件/Memory、不调用模型、不做 M5/M6。不修改已有未提交资料。验收先跑 M1–M3 基线，然后固定样本与副作用测试。

### 实施与收尾
- 新增 src/min_agent/activity_changes.py：显式 Schema/两次读取证据、六类优先分类、Snapshot 物理/逻辑字段、不可变合成状态执行器；新增 tests/unit/test_activity_m4.py：固定样本、组合优先级和身份/位点副作用。
- 未修改 M1–M3 任何源码。新增 M4 阶段卡与验收 JSON；更新 PROJECT_STATUS、activity-connector/README 及本周期三册。设计取舍见 DECISION 同 Task ID；实际测试与失败见 VALIDATION 同 Task ID。
- 自审发现压缩表示切回旧表示时 latest 不能从最后注册的 Snapshot 推断；独立保存 latest_snapshot_id，复用旧快照身份并保留观察历史，不改写根快照。不是冻结架构矛盾；符合 root/latest 分离。
- 未做：M3 Store 集成、多 Member 持久化、消费/位点推进 API、自动解冻、真实 reader/解压器、M5/M6、外部模型和生产入口。合成状态仅内存，不能把本模块的冻结说成跨进程持久冻结。
- 副作用：只在 pytest 独立临时路径写合成原始/编码样本；源码、文档、公开 JSON、忽略的 reports JUnit 与编译缓存。未改生产 schema/数据库、原文件、代理或依赖；既有无关未提交内容保留。

### Git 收尾
- 代码提交 70d57dd，工作分支普通推送成功，门禁/网络失败和最终核对见 VALIDATION 同 Task ID。补记仅三份文档。M4 实施及限定合成验收 COMPLETED，独立审查 PENDING；本轮停止，不进入 M5。

## ADR-RO-001 · 2026-11-06 · 最小只读 Activity Connector

### 执行前
- 用户本轮明确授权少量真实 Codex/DSH 历史只读验证，覆盖先前合成限定；不授权 Event Store/Memory/Checkpoint 写路径。HEAD 2a3d3a1854453ca59238806f84dce9fdbaa0440b，adrian/phase0b-m1；既有 AGENTS 和无关未提交资料保留。
- 搜索 src/scripts/依赖未找到平台读取或 zstd 解压复用代码；现有 compression_* 为语义压缩，不是文件 codec。已确认 Codex .zst magic 为 zstd，DSH sessions 文件实际扩展 .zstd；仅打印字段结构，未导出历史正文。
- 预期新增只读 reader/parser/report CLI 和合成测试；添加必要的可选 zstandard 依赖，复用 identity/records 规则；阶段卡、脱敏统计和日志。不修改 M1–M4 Store、原文件/配置/数据库，不做 M5/M6、Hook、模型或上传私人数据。

### 实施与收尾
- activity_reader.py 新增 rb 流式读取、zstd 帧结束/串联校验、有界行切分、双遍物理 hash/stat 稳定检查；activity_connector.py 新增实际格式适配、元数据脱敏统计/时间线、provisional/unresolved 标记和 CLI。test_activity_connector.py 新增固定合成验收。
- pyproject.toml 新增 activity 可选 zstandard==0.25.0；dev 同版本用于验收。项目 .venv 安装该必要依赖，无全局环境修改。M1–M4 源码未改，没有接入真实持久写路径。
- 新增 readonly-cli.md、阶段卡、公开脱敏验收 JSON；更新 README/PROJECT_STATUS 和本周期三册。设计取舍见 DECISION 同 Task ID，失败与实际测试见 VALIDATION 同 Task ID。
- 真实结构核对发现 Codex 还有 world_state/token_usage_record，补白名单仅统计其记录类型；DSH 流式帧解压窗口限制错误已按合成反例修复。未据此推断调用次数或完成状态。
- 未做：复杂 Fork/副本确认、共享 Checkpoint、生产 Event Store/Memory、模型调用、Hook/服务、M5/M6、完整平台兼容或大规模性能验证。未知类型/字段保守输出错误，全部记录保持 provisional；对象 native id 可证明时仅派生 object ID。
- 副作用：新代码/文档、可选依赖、隔离合成临时文件/测试缓存、忽略的 reports 验证结果。真实目录只读 rb；未复制或保存真实历史正文，公开证据不含真实路径、原生 ID、原始 hash、时间线或对话。原用户未提交资料保留。
