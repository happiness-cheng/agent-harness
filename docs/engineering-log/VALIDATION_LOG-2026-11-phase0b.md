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
