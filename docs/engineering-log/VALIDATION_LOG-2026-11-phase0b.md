# VALIDATION_LOG · 2026-11 Phase 0B 分卷

仅收录本轮可公开任务记录；沿用既有三册制度和 Task ID。

## ADR-ARCHIVE-001 · 2026-11-05 · Phase: Phase 0B 准备

- 已执行基线检查：`git status --short`、`git log -1`、`git branch -vv`、`git remote -v`；HEAD 和既存改动见 IMPLEMENTATION_LOG 同 Task ID。
- `git fetch origin`：FAIL，GitHub 443 经 127.0.0.1 代理连接失败；随后 `git rev-list --left-right --count HEAD...origin/main` 为 0/0，只是缓存引用，不证明远端最新状态。
- 仓库 docs 文件清单及关键词检索：未找到 Phase 0 至 0.1.2.4.2 和冻结基线。根目录 PROJECT_STATUS.md 不存在；实际入口为 docs/PROJECT_STATUS.md。已异步请求用户提供设计文件位置，不读取私人历史补齐。
- M1 A–E：NOT RUN，缺少冻结设计正文；不能声称基线通过或进入 M2。

### 本轮实际验证
- 环境：Windows / PowerShell，项目工作区；仅文档与协作规则变更。
- Python 内联检查（Path + re + git show/diff）：PASS；精确 8 个暂存文件、10 个演变阶段、14 个新增本地链接存在、旧受跟踪文本无删除；新增文本的常见凭据模式扫描未命中。另人工核对本轮归档及完整新增三册内容，不含私人对话正文/凭据。
- `git diff --cached --check`：PASS；`git diff --cached --stat` / `git status --short` / 既有三文件 staged diff 核对完成。AGENTS 暂存仅 2 行新增（规则与空行），既存工程制度修改留在工作区；职业资料、工具配置、旧日志未暂存。
- 初次 apply_patch：FAIL，PROJECT_STATUS 的 UTF-8 BOM 导致上下文不匹配，未修改文件；改为保留原始字节、在首行后插入，随后差异检查通过。没有覆盖历史正文。
- Build / Type / 应用测试：NOT RUN，无源码变更，不据文档检查声称程序通过。M1 A–E：新增测试 0、执行 0、通过 0、失败 0、跳过 0；5 类场景均 NOT RUN（冻结基线缺失）。
- 自检范围结论：归档独立单元 PASS；整体任务 NOT READY，缺失原阶段记录与冻结模型。没有证明三层存储、重建、损坏拒写或自然语言效果，不具备 M2 准入证据。
- Git 提交前门禁：差异、文件范围、适用检查、准确结果、日志与阶段入口均已核对；无业务代码和数据库操作。远端最新状态因 fetch 失败仍 UNKNOWN。

### Git 执行结果
- `git commit -m 'docs: archive Adrian evolution and establish work-branch sync rule'` 成功，提交 `35aa2da`，8 文件，226 行新增；仅本轮归档/规则/交接和三册分卷。
- `git push -u origin adrian/phase0b-m1` 失败：GitHub 443 经 127.0.0.1 代理无法连接。未改代理/凭据，未强推，未合并 main；本地提交保留。远端分支及上游关系未建立成功，服务器最新状态 UNKNOWN。
- 后续仅追加本次 Git 结果到日志与阶段记录；此追加不涉及代码，按同一 Task ID 收尾。需恢复 Git 网络后再次普通推送，不能把本地提交视为已同步。

## ADR-M1-001 · 2026-11-05 · Phase: 0B M1

- 现状：无 activity_identity/activity_store 实现；本轮新增行为需先运行失败基线。
- `git fetch origin`：FAIL，本机代理连接失败；没有修改 Git 网络配置。
- M1 验收冻结：合成 object logical_id `M1-Example`、branch `root`、初代逻辑内容 `first\n`，追加 `first\nsecond\n` 与 `first\nsecond\nthird\n`；Checkpoint 先 0 后 first/second 长度；状态 provisional→definitive 后逆转应报冲突。恢复到新路径逐项比较 object/member/stream/generation/snapshot/checkpoint/manifest ID、root/latest/fold。损坏分别改 revision_no、manifest_hash、parent_manifest_hash、manifest_id 与 head，并比较失败写前后逻辑导出。
- 证据边界：均为本地合成存储机制；不读取真实平台数据，不证明 Connector 解析、语义理解、M2/M3 或生产准入。

### 实际验证与失败证据
- 环境：Windows / PowerShell / 项目 .venv Python 3.13 / pytest 9.1.1；独立 pytest 临时目录与 `adrian-m1-*` 临时目录；0 模型调用。
- 首次 `pytest tests/unit/test_activity_identity.py -q`：1 个 collection ERROR（目标模块未实现）；实现后 9 passed。
- 首次 `pytest tests/unit/test_activity_store.py -q`：1 个 collection ERROR（目标模块未实现）；基础实现后两文件合计 26 passed。
- 增补损坏测试后 Store 为 20 passed / 2 failed：`duplicate_json_keys_are_corruption_even_if_last_value_matches` 和 `boolean_revision_rejected_even_with_recomputed_hash` 均 `DID NOT RAISE IntegrityError`。原因：json.loads 默认最后键覆盖；Python bool 是 int 子类且 True == 1。修复为唯一键解析和精确整数类型检查，两个文件 31 passed。原失败保留，不更改验收判据。
- 首次 `scripts/activity_m1_experiment.py --report docs/acceptance/activity-m1-20261105.json`：FAIL，退出清理阶段 WinError 32，sqlite3 连接上下文只提交不关闭。实际演示尚未输出报告，不能记为通过。修复脚本和测试使用 closing + 事务上下文，原命令重跑 exit 0，A–E 全通过，10 个 ID 对照相同，4 类损坏报错且数据库字节未改。
- 最终 `.venv/Scripts/python.exe -X utf8 -m pytest tests/unit/test_activity_identity.py tests/unit/test_activity_store.py tests/unit/test_checkpoint_resume.py tests/integration/test_checkpoint_persistence.py -q`：63 passed / 0 failed / 0 skipped，含新增 31 项与既有相关 32 项；不能当成全仓或模型成功率。
- `.venv/Scripts/python.exe -X utf8 -m compileall -q` 指定本轮 5 个 Python 文件：exit 0。
- RFC 8785 官方正文 §3.1/§3.2.2.2/§3.2.3 已核对，链接在设计来源 README。未引入新包；仅实现拒绝浮点等输入的子集。
- Build：语法编译 PASS；Type / 专用 Lint：NOT RUN，当前 pyproject 未配置此类工具；Test：相关回归 PASS；全仓：NOT RUN（无共享 Runtime 改动）；Security / Diff：提交前逐项检查，结果追加在下方。
- 已知限制：完整恢复包必须保留；哈希链不是签名/外部 anti-rollback 锚；单对象初代、未压缩、小样本 O(n) 验证；状态冲突只冻结本实例，冲突候选不落盘。没有证明 M2/M3、正式持久化准入或语义效果。
- 证据绑定：公开 JSON 保存运行时 HEAD 与 5 源码 SHA256（当时源码未提交），后续核对源码指纹一致后才提交；不将旧 HEAD 当作新实现版本。

### 提交前门禁与清理补记
- 已实际核对公开证据的 5 个源码 SHA256 与工作区完全一致；A–E 报告均 true；45 个相关文档本地链接存在。新增材料常见凭据模式未命中，人工审阅两份设计原文无私人对话正文；三份早期调查只归档去标识摘要，不上传原附件全文。
- 首次失败运行遗留独立合成目录 `adrian-m1-ww1gnk37`：已列出仅 5 个合成 db。拟执行含绝对路径/文件白名单/reparse 检查的 PowerShell 清理命令，被自动策略拒绝（blocked by policy），命令未执行；保留目录，不尝试其他工具绕过。此清理 NOT DONE，不影响成功重跑的隔离库结果。
- 自审：需求范围 PASS（M1 profile）；边界/损坏 PASS（限定测试）；安全 PASS（没有外部数据/生产入口）；可回滚 PASS（新增独立模块，无迁移）；完整生产/Connector 能力 NOT VERIFIED。无独立第二审查，不称独立审核通过。

### Git 与精确提交版本复验（追加纠正）
- 本地代码提交已创建：`aee274753071f65bb3e1f9d981d7ba7d22637466` — `feat: implement Phase 0B M1 identity stores and rebuild validation`，16 文件。
- 提交前暂存指纹检查实际 FAIL：`scripts/activity_m1_experiment.py`（及进一步核对的 test_activity_store.py）工作区 CRLF 与 Git blob LF 不同。前面“工作区指纹匹配”仍属实，但不能写成 Git blob 字节一致。执行流程错误：同一 PowerShell 命令在 Python 失败后仍继续 commit；未满足该项门禁就本地提交，明确保留此失败，不改历史。
- 定位验证：逐个读取 5 个 Git blob 与工作区字节比较，2 个文件仅 CRLF→LF 差异，另外 3 个完全相同；代码语义无差异。将工作区两文件按已提交 blob 的 LF 字节规范化，没有产生 Git 内容修改。
- 精确提交源码复验：同一 4 文件 pytest 命令再次 63 passed / 0 failed / 0 skipped（3.07s）；演示 `--report docs/acceptance/activity-m1-20261105-committed.json` exit 0，A–E 全 true，10 个 ID 一致。新报告绑定实际 `aee2747` 与 5 个提交 blob 的 SHA256，原运行报告保留。
- `git push -u origin adrian/phase0b-m1`：FAIL，经 127.0.0.1 代理连接 GitHub 443 失败；没有远端同步成功证据，未强推、未改凭据/代理、未合并 main。
- 后续提交命令单独执行；门禁命令失败时不再通过同一 PowerShell 脚本继续 commit。此次仅追加精确版本证据和失败说明，不改写提交历史。

### 最终门禁
- 实际验证成功：新证据绑定 aee2747；5 个工作区源码与提交 blob 字节完全相同，SHA256 全匹配；10 个重建 ID 比较相同，A–E 全通过，损坏均无写入；新增引用存在。`git diff --check` 通过。
- 补充提交仅包含精确版本证据、来源说明、阶段卡与本次实施/验证记录；原 AGENTS 工程制度修改及无关资料继续保留，不暂存。

## ADR-M2-001 · 2026-11-05 · Phase: 0B M2

- 现状基线：`.venv/Scripts/python.exe -X utf8 -m pytest tests/unit/test_activity_identity.py tests/unit/test_activity_store.py -q` → 31 passed / 0 failed / 0 skipped。
- Git 代理检查发现全局 http.proxy/https.proxy 均为 http://127.0.0.1:7890；按授权设置本仓库两项为 7897 后 `git fetch origin` exit 0。未改全局配置/凭据。
- 固定验收：快照 `one\ntwo\nthree\n` 的完整换行记录；先提交事件、位点后提交；新代 `new\n` 从 0 开始。事件 insert/before commit/after commit 与 checkpoint write/before commit/after commit 注入异常或进程强退后重新 open。转换 manifest/pending/旧closed/新active/checkpoint/commit 前后注入强退。另构造 pending、零/一/多 active、committed 不匹配、重复 transition_id 固定异常样本。
- 边界：真实 SQLite 文件/独立连接/子进程，不调用外部模型；不证明断电硬件或生产吞吐。

### 实际验证与失败记录
- 环境 Windows / 项目 .venv Python 3.13.16 / SQLite 3.50.4；全部合成输入，0 模型调用。
- 首次 M2 测试：1 collection ERROR，模块尚不存在。提取 M1 共用校验后首次合跑：46 failed / 10 passed，原因是遗漏局部变量 binding 的替换；改用 first["source_object_id"] 后同范围 56 passed。保留失败，不改变冻结设计或验收判据。
- 增补故障样本后 M2 33 passed；再补并发和迟到写入等回归后最终 M2 37 项。
- 最终命令：`.venv/Scripts/python.exe -X utf8 -m pytest tests/unit/test_activity_m2.py tests/unit/test_activity_identity.py tests/unit/test_activity_store.py tests/unit/test_checkpoint_resume.py tests/integration/test_checkpoint_persistence.py -q --tb=short --junitxml=docs/acceptance/activity-m2-20261105.junit.xml`：100 passed / 0 failed / 0 skipped，15.19s（M2 37、M1 31、既有 checkpoint 32）。原始 JUnit 随后移动至忽略的 reports/activity-m2-20261105/，公开 JSON 保留所有用例名和结果，剔除主机信息。
- 13 个 os._exit(73) 子进程用例：事件/位点 6 个、转换 7 个；7 个异常注入用例。每次通过新连接重新打开实际文件再断言。转换提交前完整导出等于原状态，提交后为完整新状态；独立读者未见中间状态。
- 重放、相同 transition_id、不同 transition_id 并发 CAS、旧代迟到写入、连续多代追加历史、恢复包重建均 PASS。Pending 未动旧代安全 aborted；初代零位点 root/backfill=0；有转换证据零 active、多 active、committed 错配均拒绝重开/写入且文件不变；篡改位点超前事件报 IntegrityError。
- 独立演示实际创建/消费/转换/重开/消费：旧 closed、新 active、新位点初始 0，最终 4 条事件/新位点 4。公开证据 docs/acceptance/activity-m2-20261105.json 绑定运行时 HEAD 和 6 个源码字节 SHA256；运行时 HEAD 尚不包含新代码，不能将其当代码提交。
- compileall 对 activity_store.py、activity_m2.py、test_activity_m2.py exit 0。全仓测试、独立 lint/type 工具 NOT RUN（范围为隔离实验模块）；生产/真实数据/断电行为 NOT VERIFIED。无独立第三方审核。
- 主要限制：单对象/member/stream、完整换行记录固定键、旧代全部消费后才能转换、新代从 0 开始；不支持旧库迁移。持久不一致采用拒绝打开与拒绝写入的冻结策略；不擅自修复。哈希链不是防恶意回滚签名。满足本轮 M2 机制门槛，不等于生产准入。

### 提交门禁与 Git 实际结果
- 精确 10 个本轮文件暂存；6 个公开证据源码 SHA256 同时匹配工作区和暂存 Git blob。diff --check、文档本地链接与常见凭据模式检查 PASS；AGENTS 与原无关文件未暂存。
- 代码提交 `29af4000354c2dbfbcf278328ba088fa22953b65`，`feat: implement M2 crash recovery and atomic generation transitions`。普通 `git push -u origin adrian/phase0b-m1` exit 0；git ls-remote 返回同一哈希，GitHub 同步已确认，含之前未推送的 M1 历史。未强推、未合并 main。
- 后续仅补齐此 Git 结果、实施记录与阶段卡，源代码没有再次变化；相关测试证据继续适用。

## ADR-M3-001 · 2026-11-05 · Phase 0B M3

- 固定验收：三种 profile（含 seq/seq0 分离）、同定位同/异指纹、异定位同指纹、多候选不映射、缺字段 provisional；显式证据映射、批次回滚、进程中断和重开后位点冻结，M1/M2 相关回归。仅本地合成数据，其他范围 NOT RUN。

### 实际结果与门槛
- 编码前 M1/M2 基线：`.venv/Scripts/python.exe -X utf8 -m pytest tests/unit/test_activity_identity.py tests/unit/test_activity_store.py tests/unit/test_activity_m2.py -q` → 68 passed，9.62s。
- M3 首次固定样本运行：1 collection ERROR（activity_records 尚未实现），不是已实现功能 PASS。首批实现合跑 76 passed；扩展 M3 单跑 35 passed。没有修改冻结判据来消除失败。
- 最终命令及逐项结果：docs/acceptance/activity-m3-20261105.json；pytest M3/M1/M2/既有 Checkpoint 六测试文件：141 passed / 0 failed / 0 skipped，20.77s；M3 41 项，原相关回归 100 项。Python 3.13.16、SQLite 3.50.4、Windows 项目 .venv。原 JUnit 在忽略的 reports 目录，公开 JSON 不含主机名/路径。
- M3 新增真实 os._exit(73) 进程中断 10 项：事件 SQL/记录日志/事件提交前后/位点 SQL与提交前后 7 项，冲突日志/冲突提交前后 3 项。关闭/重新打开数据库后验证回滚、完整提交、重放与位点冻结；另 1 项 Python 异常回滚完整导出相等。均 PASS。
- 五类 M3 场景 PASS；同定位不同 structure 亦冲突；seq/seq0 两库 stream/checkpoint 独立；跨代不凭 fingerprint 映射；M2 generation transition 在 M3 中正常且幂等；旧入口应用标记隔离/直接 base persist 的记录覆盖校验、已有连接冻结、冻结恢复包重建均 PASS。
- compileall 对 4 个新增/修改源码及 M3 测试 exit 0。仓库 pyproject 无独立 type/lint 配置，Type/Lint NOT RUN；全仓测试 NOT RUN（本次扩展了相关 Checkpoint 回归）。外部规则引用的 review-checklist.md 不存在，采用当前需求/事务/边界/数据隔离/差异手工自审；未调用子代理，不称独立审核通过。
- 证据 JSON 绑定 9 个源码 SHA256；运行时 HEAD 仍是 M2 文档提交，不声称该旧 HEAD 包含新源码。提交前核对 Git blob 指纹后再提交。
- 风险/边界：合成 envelope 非原生平台解析器；一个库一个 namespace；整个冲突 stream 库保守冻结，无解除冻结入口；映射证据仅校验结构与绑定，不证明外部事实真实；多候选不做自动消歧；哈希链不等于恶意回滚保护。只满足 M3 合成机制验收，独立审核及生产准入未执行。

### 提交门禁与同步结果
- 12 个精确本轮文件；9 个源码 SHA256 同时匹配工作区与暂存 Git blob；git diff --cached --check、文档本地引用、常见凭据模式检查 PASS。fetch 后本地与远端分支 0/0，既有 AGENTS 等未暂存。
- 代码提交 `75bec9dc75a7fb58f17f05f7049204fe4c5e8bb9`；普通 push 到 origin/adrian/phase0b-m1 exit 0；git ls-remote 返回相同哈希。无强推、main 合并或新配置变更。后续仅追加同步记录，源码与已验证字节未改变。

## ADR-M4-001 · 2026-11-05 · Phase 0B M4

- 固定验收：六类与 unresolved、两次读取抖动/不完整、schema 与 rewrite/append/压缩优先级、物理/逻辑长度分离、明确重排证据；检查 member/generation/snapshot ID、Checkpoint 位点与状态、观察历史和重复执行。M1–M3 及既有 Checkpoint 回归。真实 reader/解压器、生产接入、M5/M6 NOT RUN。

### 实际测试、失败与边界
- 基线：M1–M3 四个测试文件 109 passed，18.64s。M4 首次测试 1 collection ERROR（activity_changes 尚未实现）；首批实现 10 passed。
- 自审新增压缩→原表示回归后 1 failed / 10 passed：latest_snapshot_id 仍指向压缩快照。原因是错误地用最后注册项推断 latest；修复为独立指针，保持注册去重和 root 不变。扩展后 39 passed；加入固定 zstd raw-block 和未结束流样本。
- 最终七文件 pytest 精确命令、11 源码 SHA256 与逐项结果见 docs/acceptance/activity-m4-20261105.json：182 passed / 0 failed / 0 skipped，21.15s（M4 41、既有相关回归 141）。Windows / 项目 .venv Python 3.13.16。运行时 HEAD 是 M3 文档提交，不能据此声称该旧提交包含 M4。
- 六类分类及 unresolved、Schema 三维变化分别压过 append/rewrite/compression、unstable 压过 truncation/migration、物理长度收缩但逻辑 append、物理增长但逻辑 truncation、双读抖动/流不完整、重排多重集与缺证据均 PASS。
- 副作用断言 PASS：member/generation/snapshot 注册与派生、旧身份保留、Checkpoint 关闭/新建从 0、append/compression 不前进位点、未知/截断/不稳定不建身份、后续写入仍受阻、旧代未消费完不替换、重复 observation 幂等和陈旧基线拒绝、旧快照复用时 latest 正确。M1 未压缩 Snapshot ID 精确一致。
- compileall 对新增模块与测试 exit 0。独立 type/lint 未配置，NOT RUN；全仓测试 NOT RUN，按隔离模块边界运行完整相关回归。环境无 zstandard，未新增依赖；固定 raw-block 输入与已解压内容对照不等于已验证解压库。
- 自审由当前助手执行，无子代理或独立审核。限制：输入 Schema/完整性/重写标记来自调用方；纯内存合成状态，不证明持久原子性、实际 reader 稳定性、生产恢复或吞吐。M1–M3 存储/恢复/冲突由既有回归验证，未改这些模块。

### 提交门禁与 Git 结果
- 精确 9 个本轮文件暂存；11 个源码 SHA256 与工作区及暂存 blob 全匹配，diff --check、本地文档引用和常见凭据模式检查 PASS。M1–M3 源码无修改；原 AGENTS 与无关资料未暂存。
- fetch 首次 FAIL：TLS unexpected EOF；未改网络配置。代码本地提交 `70d57dd9cd231a5f4e6fea2aebf22db79672af55` 后普通 push 成功；git ls-remote 返回相同哈希。无强推、无 main 合并。后续只补记同步记录，不改已测源码。

## ADR-RO-001 · 2026-11-06 · 最小只读 Activity Connector

- 固定合成验收：Codex JSONL/zstd、DSH v4/old、seq/seq0、损坏/不完整流/缺字段、流式限制、脱敏输出、路径变化与重复扫描、原文件 hash 不变；M1–M4 回归。通过后才进行小批真实只读验收，公开证据仅统计与代码指纹。

### 实际验证与失败记录
- M1–M4 编码前基线五文件 150 passed，19.82s。Connector 初次测试 1 collection ERROR（模块尚未实现）；初始 8 passed，扩展 31 passed。
- 首轮真实只读样本：Codex JSONL 27 parsed/4 failed（world_state/token_usage_record 不在适配列表），Codex zstd 7 parsed/0 failed；DSH v4/old 均在解压阶段拒绝，非正常 EOF。原因：max_window_size=65536 实际限制拒绝样本 2 MiB 窗口；原源码不适用未知 content_size 流式帧。四文件均前后 hash 不变，重复结果相同。保留初始脱敏统计，不覆盖失败。
- 将问题转成固定合成反例：3 failed / 31 passed（未知大小 zstd 流、两个 Codex 元数据类型）。最小修复窗口设置和已观察类型白名单后 34 passed，再扩展资源/元数据稳定性/只读读长/隐私边界样本。
- 最终八测试文件：222 passed / 0 failed / 0 skipped，22.66s（Connector 40、既有相关回归 182）；命令、逐项结果、15 个代码/配置指纹见 docs/acceptance/activity-readonly-20261106.json。环境 Windows，项目 Python 3.13.16，zstandard 0.25.0。compileall 新两模块与测试 exit 0。
- 最终真实小批：4 文件 264 parsed / 0 failed；Codex JSONL 31、Codex zstd 7、DSH v4 7、DSH old 219。全部完整/双遍稳定/重复扫描一致/前后 SHA256 不变；old 的 seq 59、seq0 159。260 个非头记录全部 provisional。公开证据仅脱敏统计，未导出正文、Token、真实路径、原生 ID、原始文件 hash 或时间线。结构发现另读少量头部，不以旧报告替代当前文件证据。
- 合成验收包含 JSONL/zstd 逻辑一致、串联帧与未结束流、损坏/重复 JSON 键、缺字段/未知格式、seq/seq0 冲突、编码窗口、资源上限、双读之间文件变化、未知敏感 type 脱敏、输出路径重叠/拒绝覆盖、只读且所有 read 有界、路径变化 object ID 稳定、副本不合并。实际 subprocess CLI 测试通过。
- Type/Lint 无独立配置，NOT RUN；全仓测试 NOT RUN（隔离模块，已有相关回归全部运行）。本助手自审，未启动独立子代理审核。无真实写路径、M5/M6、性能或持续跟踪证明。采集稳定是双遍观察结论，不是 OS 锁定快照保证。

### 精确提交与 Git 同步
- 精确暂存 12 个本轮文件；15 个代码/配置 SHA256 同时匹配工作区和暂存 blob。diff --check、本地链接、常见凭据模式和真实统计字段白名单检查 PASS。原 AGENTS/其他资料未暂存，reports 原始运行文件未提交。
- fetch 成功且提交前分支 0/0；代码提交 `586b24970f7924859020f58816a28d3a36620a46`，普通 push 成功，ls-remote 核对同哈希。没有强推或 main 合并。后续仅补记此同步结果，不改已测源码。
