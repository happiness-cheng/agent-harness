# DECISION_LOG · 2026-11 Phase 0B 分卷

仅收录本轮可公开任务记录；沿用既有三册制度和 Task ID。

## ADR-ARCHIVE-001 · 2026-11-05 · Phase: Phase 0B 准备

- 规则决策：只追加用户指定的一条独立提交/工作分支同步规则，不改造工程记录制度。理由是可回滚工作单元应及时保留，不与无关旧修改混合；替代方案整仓提交被拒绝，因为工作区含既存私人/职业资料与未审核材料。
- 归档决策：保留 AGENT_VISION 作为早期作者设计说明，新增 ADRIAN_EVOLUTION 记录跨阶段演变并互相引用。替代方案重写原文会混淆历史时点，故不选。阶段 2–10 的后续叙述依据本次用户说明，明确未核实的历史证据；不编造引用与测试结果。
- 缺失信息：当前仓库未找到 Unified Event Model v0.2.4.2-final + Errata 2，不能判断具体字段/算法/状态机是否一致。先完成独立归档单元，向用户索取设计文档位置；不以自行推定的实现替代冻结设计。这是输入缺口，不是架构修订证据。
- 提交取舍：AGENTS 只暂存本轮新增行；既存日志按周期另开本任务三册分卷，现有三册只追加本轮入口，避免将整份旧的未跟踪日志一起公开。分卷保留同一 Task ID，不改写旧条目。
- 追溯：IMPLEMENTATION_LOG / VALIDATION_LOG → ADR-ARCHIVE-001。

## ADR-M1-001 · 2026-11-05 · Phase: 0B M1

- 基线选择：用户确认 144814 Final Errata 为最终基线；143536 中可变 Snapshot Head、身份内 status、root_evidence 入 ID 等旧规则被明确覆盖。不将附件里的历史“等待审核/不实施”当作本轮命令。
- 存储选择：单个独立 SQLite 文件，三个逻辑 Store 各自表及哈希/版本；Manifest 注册修订 append-only，Observation 快照/状态 append-only，Checkpoint 位点 CAS。复用标准库，无新增依赖。不选三个文件：跨文件事务额外复杂，不属于 M1 证明目标；不改现有 SessionStore/生产库，避免与运行时权限和 schema 耦合。
- 恢复选择：导出包含 Manifest 完整已提交链、Observation 历史及 Checkpoint 持久位点的显式 JSON recovery bundle；验证后逐行导入全新数据库，重算 latest/fold 与 ID。不是从最终快照猜最初 root，也不是直接复制数据库。全部源持久状态丢失的恢复与备份外部可信锚定不属于本次证明。
- ID profile：遵守 uem-id/1 与全 SHA256、字段内 domain_tag；仅 logical_id 做 NFC。M1 限定 JSON null/bool/安全范围整数/string/list/string-key object；按 UTF-16 code units 排序，不接受 float、孤立 surrogate、大整数。这是可验证的 JCS 子集，不声称完整浮点 JCS 支持。
- 未给定的具体初代 generation/stream/snapshot 字段组合将在 M1 模块中明确作为实验 profile，不能声称已覆盖完整 Connector wire contract。仅单对象、明确原生 branch key、初代 generation、未压缩合成字节流；无可靠原生 root 证据时拒绝初始化 definitive 身份。
- 状态范围：支持 identity_status 的 provisional→definitive、checkpoint_status 的 active→closed、root_status 的 root_unresolved→root_resolved；终态逆转/未知状态拒绝并冻结本 Store 实例写入。状态只有 Observation 事件，不改身份。其他维度及 provisional→definitive 映射不在 M1 profile，不提供未经验证的入口。
- 恢复/写入校验：每次事务读取最新哈希链与归属，损坏则报错，不写入；并发写依赖 SQLite BEGIN IMMEDIATE + Checkpoint revision/hash CAS。M2 代际转换/故障注入留待后续，不把此处普通事务测试冒充 M2。
- 追溯：同周期 IMPLEMENTATION_LOG / VALIDATION_LOG → ADR-M1-001。

## ADR-M2-001 · 2026-11-05 · Phase: 0B M2

- 复用选择：M2 使用现有 ID/JCS、Manifest/Observation/Checkpoint 结构，扩展多代校验；独立 M2 Store 增加合成事件表与转换操作。新建 M2 实验库使用单独格式/应用标记，M1 API 不能绕过 M2 事件先提交门；不迁移生产或旧实验库。
- 事务：事件幂等入库独立事务提交；Checkpoint 后续事务校验覆盖区间事件已存在。Generation Transition 在一个 BEGIN IMMEDIATE/COMMIT 中注册新代、写 root snapshot、追加 transition pending、旧代/位点 closed、新代/位点 active、transition committed；故障注入点位于真实 SQL 写之后。
- 恢复：正常转换不产生持久 pending/部分状态；单独构造自洽的遗留/异常状态检验启动策略。pending 且旧 active 无变更则追加 aborted；有转换证据但零 active、或多个 active、committed 与 active 不一致则明确冻结，不猜测恢复。无转换且初代未初始化时从 root 位点 0 初始化，标 backfill。选择保守冻结替代不充分证据的自动修复，符合本轮异常状态期望。
- 合成事件：使用当前快照中完整换行记录的 ordinal 固定键及字节区间，按 generation 隔离；只验证重读幂等，不实现 M3 通用 Locator Profile。新代默认位点 0，不继承旧位点；非零迁移计划暂拒绝。
- 故障证据：包括 SQL 写中途异常、提交前中断、提交后未返回，以及子进程 os._exit 强制退出。独立读连接应只见全旧或全新状态，不见转换中间状态；不将进程终止测试宣称为掉电/磁盘损坏验证。
- Git 代理：本仓库 http/https.proxy 设置 7897，优先于全局旧 7890；不改全局，避免影响用户其他仓库。提交/推送依旧精确路径与普通 push。

## ADR-M3-001 · 2026-11-05 · Phase 0B M3

- Locator 身份只包含冻结规定的 member/stream/generation、format_version、seq_namespace、seq_value、line_index、解压偏移；fingerprint 独立 struct/payload/record 哈希。optional metadata 不进入身份；DSH old 可选偏移未知用 null，不伪造零。缺 required 时用 observation_run_id 与顺序占位派生 provisional。
- 复用 M2 单 stream 多代事务模型：一个独立 M3 库对应一个命名 stream；DSH old seq 与 seq0 显式创建两个库/stream/checkpoint，不能混写。使用合成 JSONL envelope 作为测试传输，不编写真实 Connector/解压器。M3 新 application_id 阻止 M1/M2 旧入口绕过。
- 冲突在事件批次预检发现，整个事件批次不落盘；同一写锁事务仅追加冲突证据，提交后报告 record_conflict。任何后续写入检查持久冲突记录，重启也冻结。不是只设置 Python 标志，也不在回滚后另开无锁事务标记。
- 映射仅对同 member/stream/generation 的内容候选，唯一候选还需调用方显式 native_id/lineage/phase0b_confirmation 证据（绑定 source/target/reference），追加 IdentityTransition；多个候选返回无序集合，不选第一条。保留原记录，映射不删除、覆盖或合并。
- 不迁移旧数据库；新库初始化复用已有结构，新表限定本轮隔离合成库。选择独立扩展而非改变 M2 固定键协议，保持旧测试有效。

## ADR-M4-001 · 2026-11-05 · Phase 0B M4

- 分类器比较两次完整读取证据和已接受基线，按 unstable→truncation→migration→compression→append→rewrite，无法证明时 unresolved。Schema 是 format_version、结构键集合、seq_namespace 的显式结构，不将 compression/物理长度混入。
- 读取层提供 physical/logical bytes、完整性和写入中标记；本模块据 bytes 独立计算两种长度/hash，比较两次读取。不是生产 reader/zstd 解压器，输入证据真实性由调用方负责。重写允许明确平台标记，或完整换行记录多重集相同但顺序改变的可计算合成证据。
- 独立不可变合成状态执行器复用 M1 derive/object_identity，执行真实 ID 注册、快照追加、代际派生与 Checkpoint 关闭/新建，不接入 M3 Store。原因：现有 Store 为单 Member/未压缩；接入将扩大到生产式多成员/压缩持久化，不是 M4 最小边界。测试不只检查分类字符串，也逐项比较状态历史和身份/位点。
- 分类本身不推进消费位点；append/compression 保留 Checkpoint，migration/rewrite 新 Checkpoint 从 0 开始、保留关闭的旧 Checkpoint。旧代/成员未消费完时仅返回分类和阻塞观察，不执行替换，防止漏消费。unstable/truncation 持有阻塞原因，后续不得自动切换身份；解冻机制不在本轮。
- 合成执行采用不可变值一次返回：错误/陈旧基线不修改原对象。状态仅内存，不声称 SQLite/跨进程持久冻结或新的 M2 原子性证明；M2/M3 持久机制由其既有回归验证。

## ADR-RO-001 · 2026-11-06 · 最小只读 Activity Connector

- Adopt python-zstandard 可选依赖（项目 Python 3.13 无标准库 zstd），Build 薄层格式适配/报告；不使用一口气解压大型文件，不自写 codec。参考官方 Decompression APIs / ZstdDecompressionObj 的 eof/unused_data，显式验证帧结束；https://python-zstandard.readthedocs.io/en/latest/decompressor.html#zstandard.ZstdDecompressionObj 。
- 实际读取器两遍流式 hash/元数据核对，逐行有界解析；报告只输出白名单类型、合法时间、位置、计数、匿名来源引用和确定性派生标识，不输出原生正文、任意字段值或异常原文。只读不能提供 OS 锁定快照保证，变化则标 unstable，不作为正常末尾。
- 不执行 M4 内存状态机、不接 M3 真实写入。只读不能证明 root/generation/fork 时保持 provisional/unresolved，物理副本分开统计，不按路径或 NTFS ID 判逻辑身份。可证明的 object/locator 组件复用既有 derive；M5/M6 相关决策跳过。

- M4 当前接口接收整份 physical/logical bytes；为保持大文件流式读取，不强行调用其内存执行器。沿用 unstable 优先原则；无持久前态时变更类型明确 unresolved_no_persistent_baseline，不猜 append/rewrite，也不产生身份变更。复用 activity_records 的 Locator Profile 与受限 Fingerprint；浮点超出既有 JCS 子集时明确 unsupported_canonical_value，不篡改原始值求哈希。
- DSH 当前实际头部 version=4/0，旧版确有 seq/time 和 seq0/time0。以头部结构识别来源，不按文件名猜；重复 session 头停止后续身份归属，多个文件同 object 仅报告关联 unresolved，不做合并。报告类型采用白名单，未知原始 type 不直接回显。

## ADR-PM-ADAPTER-001 · 2026-11-06 · 跨项目集成第一批

- PM Source 是文件字节 src_sha256，Adrian object/record 是不同身份；建立显式映射，绝不互换。Source 取隔离生成的白名单报告摘录文件，原始历史引用始终未验证；PM message_index/role/quote 不凭 JSONL line_index 推造。
- 没有消息正文的报告只能生成 Agent 活动观察候选：draft、status unknown、event_at/conversation_at 留空，source_recorded_at 与 observed_at 分离。conversation 作为候选 kind，并标正式导入受缺消息证据阻塞；不生成真实活动/偏好/知识/Memory。
- Pilot 01 用户最终决定明确不追认旧自动 confirmed 和所谓 100% 人工通过率；本轮不使用旧标记作为确认来源。插件队列使用另一组字段/状态，原样列为不兼容，不修改插件或写其队列。
- CLI 仅合成 demo：自己生成报告并读取；输出只允许自身新建、带标记的 OS 临时沙盒，不接受正式 Vault 输出路径。Markdown 只白名单元数据，原文/健康/私有扩展不复制。既有任何不同内容均保留，不覆盖 confirmed/corrected，甚至不覆盖 draft。
