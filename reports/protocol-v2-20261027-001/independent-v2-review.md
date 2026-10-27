## 代码审查结果

### 发现的问题

无未解决的 CRITICAL、HIGH、MEDIUM 问题。

### 总结

| Severity | Count |
|----------|-------|
| CRITICAL | 0 |
| HIGH | 0 |
| MEDIUM | 0 |

Verdict: PASS

本次为独立只读代码审查，仅写入本审查报告和对应 JSON；没有 Provider、Codex CLI、网络或付费生成调用。审查范围为 PROTOCOL_V2.md、REPORT.md、protocol.py、preflight.py、audit.py、两版 application.py/research_resources.py 及相关 ResearchService、ArtifactStore 调用上下文，同时核对发布复现脚本的具体遗漏及修复。没有重新进行无关旧代码审查。

实际执行：使用根 .venv/Scripts/python.exe、PYTHONPATH 指向 candidate/workspace/src 运行 test_resource_entry.py，结果 **3 passed in 1.56s**。本审查未重新执行全部 1268/1259 项测试，不将其他执行日志算作审查者自行运行。

独立核对 preflight-02 的 26 份只读 SQLite、model-visible.json、trace.json：任务归属一致，落盘 Trace 一致；实际 read_webpage 完成情况与记录一致；所有已给出的 Evidence 引文与实际 Artifact 来源、位置和正文逐字一致；预算未超限。按每次研究派发真实 BudgetRecord 的 child/祖先有效 remaining 与成功搜索、尚未读取条件独立重算 Reach，结果全部一致，判定未依赖 read_webpage Schema 是否可见。正常三题两版均 REACHED；基线没有实际读取，候选有实际读取。

独立重新计算契约及逐题 hash，通过；契约为 504ecefe65eb1be0f8abeee22a57d537b70b94f40f169c4c683ba7a4efa85318。13 组首轮 messages/schemas 一致，Session 附件随机引用仅按相对应对象归一化；首轮无三题正文。两版全部 src 文件哈希仅 research.py 不同，实际差异为工具收尾阈值 <=1 与 ==0。1212 个受保护历史文件及 main-worktree-before.json 的文件清单未变化。

目录确由公开 mount_research_collection/research_resources、真实 ToolRegistry 及 Session ArtifactStore 生成；目录只有实际挂载资料元数据，正文在读取后才写入 Artifact。PSF 是受控外部库的公开历史短片段，保修/API 明示合成性质。挂载与访问许可分离；未知 URL 拒绝且不回退网络，跨 Session 引用检查仍在。正常目标未要求工具顺序。Task、Reach、Repair 分开记录，离线固定参考策略不足以证明真实模型收益，Repair INCONCLUSIVE 合理。

审查时发现 prepare_publication.py 本批扩展名白名单遗漏 .ps1，发布快照缺少 reproduce.ps1。执行方已修复；本审查重新验证白名单含 .ps1，实际 publication/workspace 中脚本存在且与当前源脚本字节一致，publication-manifest.json 对应 SHA-256 匹配。脚本会复制三项入口测试及六项原研究回归测试到重建工作树。该问题已关闭，不计入未解决 finding。最新 publication-security.json 为 2934 个文件，凭据样式、非空 provider_state、数据库和 memory_v01 项均为空；这是指定扫描范围的结果，不代表任意个人信息已穷尽证明。

负例证据边界：现有空集合且无 Session 文档可证明该路径不强制 research；不能证明“已挂载无关文档但目标不存在”时仍能正确处理。ReferencePolicy 当前选首份目录文档，不进行自然语言目标匹配。因此该强负例应在声称覆盖该场景或开展相应真实先导前另立新 Task ID 回归，不修改本次冻结契约。本报告没有将其记作已覆盖，也不以此否定当前已限界的零生成预检。

指定现行 ~/.codex/rules/review-checklist.md 不存在；审查读取了迁移归档中的同名清单“代码审查”部分及 code-review 技能，按行号、具体失败路径、上下文和可辩护严重性门槛报告。
