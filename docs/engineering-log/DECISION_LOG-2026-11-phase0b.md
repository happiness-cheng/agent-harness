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
