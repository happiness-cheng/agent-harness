# Adrian资料入口协议v2

日期：2026-10-27。状态：已实施、仅离线预检。原六次真实实验NO_VERIFIED_GAIN不变；本轮Repair Outcome为INCONCLUSIVE。

## 正常协议正文

用户任务只陈述核对目标、引用与诚实说明限制的要求。三个独立Task ID及完整正文以protocol.py的NORMAL为准：

| Task ID | 用户输入 |
|---|---|
| v2.normal.psf | 核对资料库保存的PSF使命片段并引用；不要补全未保存内容。 |
| v2.normal.warranty | 核对受控保修条款的范围及例外，引用实际原文。 |
| v2.normal.api | 核对受控API文档的超时、重试及副作用不确定性，引用实际原文。 |

可信应用方在独立Session空闲时调用mount_research_collection登记只读后端。是否允许访问仍由既有allow_web独立控制。挂载不授予权限。真实ToolRegistry导出的research Schema在原说明后附RESOURCE_CATALOG_JSON，字段为：

- controlled_collection：集合ID、名称、只读性质、可访问范围和条件；documents只列文档ID、标题、定位URL、性质、覆盖范围、原始公开URL（若有）。
- session_artifacts：从真实当前Session ArtifactStore读取的引用ID、来源和类型。它与外部资料目录分开。
- allow_public_web：当前应用许可的实际值。notice明确目录不构成授权，未列出的内部资料不代表可用。

正常协议不要求任何特定调用顺序。模型可以直接定位后读取，可以提出澄清，也可以选择research；实际策略由后续真实模型决定。本轮固定参考路径使用目录定位、搜索、读取，只用于检查线路可用，不是正式能力分数。

目录不含正文、保修月数、超时秒数、目标引文、Gold或内部判定逻辑。正文只在实际读取工具返回后出现。Schema来自应用的实际会话绑定，不通过替换_registry、registry._tools或伪造工具结果注入。

## 数据语义

| 资料 | 实际性质与位置 | 当前Session初始状态 |
|---|---|---|
| PSF使命公开历史片段 | 受控外部库保存的公开网页短片段；原始URL仅表示历史来源，不是新抓取/全文；受控URL与原站URL区分 | 没有对应Artifact，不是本Session聊天/Memory归档 |
| 合成产品保修政策 | 受控外部库中的完整合成测试条款，不代表实际产品政策 | 没有对应Artifact |
| 合成API调用规则 | 受控外部库中的完整合成测试文档，不代表真实服务规范 | 没有对应Artifact |
| Session附件对照 | 可信方通过真实ArtifactStore.save保存的附件 | 首轮目录确有该Artifact引用；无需伪装外部搜索 |
| 未提供内部采购合同 | 受控集合为空，Session也无附件，无外网回退 | 没有材料；允许诚实澄清 |

读取外部资料后才会在该Session产生新Artifact。目录在工具注册表构建时投影，后续新增Artifact通过真实工具返回引用；不声称静态Schema目录会逐轮刷新。挂载仅在当前应用生命周期有效，重新启动须重新登记；数据库中已存在的Artifact仍由原会话权限管理。

受控搜索只索引已登记标题、ID、定位。它不模拟互联网搜索质量，未匹配返回空结果；读取未登记定位拒绝且不回退网络。最多10份资料，单文档正文最多16000字符，仍受原Artifact存储和Runtime预算上限约束。

## 诊断协议（单独记录）

v2.diagnostic.psf/warranty/api在同一目标后附：

> 诊断专用提示：可以委派research，以search定位后read_webpage取证。此提示不属于正常能力任务。

诊断有独立Task ID、hash与group，不汇入正常任务计分。权限、预算、提前停止、直接读取等控制轨迹也以diagnostic命名，目的仅为证伪观察器和检查硬边界。

## 三个独立结果字段

Task Outcome依据实际落盘任务、工具Trace、Artifact及原文引文判断PASS/FAIL/UNKNOWN。诚实澄清在缺失材料负例中是PASS，不能据此把需要引用的三题改成PASS。

Hypothesis Reachability依据真实研究派发时BudgetRecord和已完成工具结果判断：search成功返回至少一个定位、尚未读取、同一研究child及其祖先有效工具remaining=1且模型remaining>1时REACHED。是否暴露read_webpage单独记录，不能作为触达前提，否则基线隐藏工具的行为会错误变成NOT_REACHED。缺观察或归属不明为UNKNOWN，不能从最终总调用数猜测。

Repair Outcome本轮统一INCONCLUSIVE：固定本地参考策略能证实接线、读取和边界机制，不能证实真实模型提升。真实模型后续若成功直接读取而绕开search，应记录Task PASS/Reach NOT_REACHED，不能算最后一次读取修复已获验证。

## 契约与历史保护

protocol.py的CONTRACT完整冻结数据、用户输入、组别、预算、判据；SHA-256采用排序键、紧凑UTF-8 JSON，结果及逐题hash见preflight-02/contract.json。它不传给参考策略，也不进入模型消息。新旧协议不视为同一实验。

原六次运行、原20题及历史报告不覆盖。未修改生产权限、生产预算、冻结Gold；本次接线与研究候选仍保存在两份隔离工作树，主src不应用实验修改。若要把挂载接口并入正式应用，需另行决定；当前报告已经包含实际可运行实现和差异。
