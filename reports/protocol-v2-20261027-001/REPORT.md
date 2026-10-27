# Adrian V1.1 资料入口协议v2：零生成预检

日期：2026-10-27。协议预检PASS；Repair Outcome **INCONCLUSIVE**。原六次真实实验的**NO_VERIFIED_GAIN保持不变**。本轮真实Provider生成0、Codex CLI生成0，没有复用旧额度。完整协议见PROTOCOL_V2.md；先导只申请不执行，见PILOT_BUDGET_REQUEST.md。

## 实际改了什么

旧实验受控资料存在于测试边界，真实模型首轮却不知道资料标题、定位或该后端存在；固定离线策略直接使用内部case.query，因而并不等价。这是实验输入配置问题，不能根据六次未调用research就断言研究候选无效或已提升。

本次在两份隔离工作树共同增加ResearchCollection及HarnessApplication公开mount_research_collection/research_resources接口。真实_registry读取已挂载的后端，在research的真实Schema中公布只读目录；research调用原ResearchService、Runtime、预算、权限和ArtifactStore。正文不在首轮目录。受控库不回退外网。没有替换私有注册表、没有伪造Session资源已存在。

PSF是受控外部库的历史公开短片段，Session初始无对应Artifact；保修/API是合成外部受控文档。真实Session附件和未提供资料另设对照。目录只表述实际位置和许可，不暗示不存在的历史或外网可读性。

两版入口改动逐字相同（baseline-entry-v2.patch与candidate-entry-v2.patch相同）。基线/候选之间仍只保留原Research closing的<=1与==0差异（research-candidate.patch）。正式主工程src保持原样；本报告中的implementation和patch为实际隔离实现，不称已经并入生产。

## 先固定契约与RED

PLAN.md先固定场景/判据，protocol.py冻结三正常题、三个独立工具提示诊断题及七个控制任务；全部新Task ID和hash。总契约hash：`504ecefe65eb1be0f8abeee22a57d537b70b94f40f169c4c683ba7a4efa85318`。

test_resource_entry.py先执行3 failed（缺少真实公开接口/模块，red.log），接线后3 passed（green.log）。检查真实Session目录、正文不泄漏、未挂载/跨Session引用、受控搜索、未登记URL、重复定位、无效挂载。

第一次驱动preflight-01因相对路径调用as_uri报错停止，保留目录和日志；修正为绝对输出路径后新建preflight-02运行。第一次全量命令从主目录收集隔离tests，发生5个同名模块导入错误，保留full-candidate.txt；改在隔离工作树cwd运行后通过。两者均是本轮执行脚本问题，没有归为Adrian生产缺陷，也没有追加真实调用。

## 有效预检结果：26条参考轨迹

真实应用公开mount → allow_web → submit/wait，既有工具注册与Runtime执行；固定参考策略只接收模式，从模型实际Schema/工具结果取定位，不能直接读取case、正文或Gold。策略可以复现一条参考路径，但不能替代真实模型的行为结论。评分关闭应用后以只读SQLite核对实际任务、Trace、Artifact和引文，不采用策略自评分。

| 场景（每版） | 基线Task / Reach | 候选Task / Reach | 说明 |
|---|---|---|---|
| 正常PSF、保修、API，各1条 | 3 FAIL / REACHED | 3 PASS / REACHED | 两版初始输入完全相同；正常目标无工具顺序 |
| 独立工具提示诊断三题 | 3 FAIL / REACHED | 3 PASS / REACHED | 单列，不计正常任务成绩 |
| 内部采购合同未提供 | PASS / NOT_REACHED | PASS / NOT_REACHED | 空目录，无Artifact，不调用research，诚实请求材料 |
| 真实Session附件 | PASS / NOT_REACHED | PASS / NOT_REACHED | 读取真实Artifact，不冒充外部搜索 |
| search后主动停止的观察器对照 | FAIL / REACHED | FAIL / REACHED | 触达不等于成功 |
| 直接read定位的对照 | PASS / NOT_REACHED | PASS / NOT_REACHED | 成功不等于触达待修边界 |
| 未授权研究入口 | FAIL / NOT_REACHED | FAIL / NOT_REACHED | waiting_user，无原文读取，硬权限保留 |
| 0工具、2模型预算，各1条 | 2 FAIL / NOT_REACHED | 2 FAIL / NOT_REACHED | 不增预算、不伪称完成 |

所有Repair Outcome为INCONCLUSIVE；表中FAIL包含预先声明的负例，不是要全部刷绿的测试套件。preflight-summary.json含逐条断言；缺BudgetRecord反例确认为UNKNOWN。正常路径在已成功search、尚未read的真实研究派发时，两版有效remaining均为工具1、模型4；基线read_webpage Schema不可见，候选可见。候选工具usage3/3、模型usage5/7，可完成读取、研究Evidence收尾和主循环回复。触达由BudgetRecord及工具结果决定，不把Schema可见性作为前提。

## 首轮上下文和数据证明

每条preflight-02/<variant>/<task>/model-visible.json保存真实dispatch的messages、完整schemas、响应安全投影、归属与真实BudgetRecord；result.json保存目录、只读验证结果、预算和三类结果；trace.json是应用实际工具Trace。运行数据库仅保留本地，不上传GitHub。

audit.py核对13组初始输入：消息和Schema两版相同；Session附件控制仅将两版各自随机生成的对应Artifact ID归一化，原始输入仍保留。三正常题完全无需这种归一化。目录和首轮消息没有三个正文、Gold或验收字段。PSF目录提供历史来源与受控定位而不伪装实时抓取。

input-diff.json及三个*-messages-v1-to-v2.patch展示旧六次收据中实际首轮消息与v2差异：用户目标澄清了资料库语义，移除了不相关合成主会话标记；目录进入工具Schema。旧收据只保存工具名称，未保存完整Schema，故不能恢复或声称有完整旧Schema字节diff；该证据缺口明确保留。新v2两版则保存完整实际Schema。

模型窗口65536、输出4096是本地参考模型公开的合成能力元数据，不代表本轮重新核验了真实Provider当前能力。本轮未读取凭据、未请求模型元数据。未来先导执行前须确认实际Provider能力且符合窗口/费用申请，否则停。

## 回归、安全及资源

- 候选隔离全量：**1268 passed、3 deselected、1 warning，251.38秒**（full-candidate-02.txt）；其中原1259、研究候选6、公开挂载3。
- 原冻结20题原契约：**15 PASS、2 FAIL、3 UNKNOWN**，expected_outcomes_match=true；输出新建frozen-suite，未覆盖旧报告。
- 所有26条轨迹owner绑定、无业务写入、预算不超限、Artifact来源范围、上下文保守包络检查通过；无实时搜索、无真实抓取。
- 跨Session引用、未挂载Session目录、未登记读取、没有许可、0工具和模型不足均保留硬边界。
- integrity.json保存主src哈希不变和历史保护清单逐项比对；旧六次、旧20题及旧报告文件均未改写。Source-entry改动只有隔离源码及本目录归档。
- 依赖、构建、主项目待发布快照全量结果及GitHub核对见PUBLICATION.md（推送前补全）；Type/Lint没有项目配置，记SKIP，不假称通过。

## Windows复现

从项目根目录运行，所有输出另建目录，不能覆盖本批：

```powershell
powershell -NoProfile -File .\reports\protocol-v2-20261027-001\reproduce.ps1 -OutputName protocol-v2-rerun-001
$env:PYTHONPATH=(Resolve-Path .\reports\protocol-v2-rerun-001\candidate\workspace\src).Path
.\.venv\Scripts\python.exe -X utf8 -m pytest .\reports\protocol-v2-rerun-001\test_resource_entry.py -q
.\.venv\Scripts\python.exe -X utf8 .\scripts\run_adrian_frozen_eval.py --source .\reports\protocol-v2-rerun-001\candidate\workspace --protocol .\reports\eval-v1\acceptance-protocol.json --out .\reports\protocol-v2-rerun-001\frozen-suite
Push-Location .\reports\protocol-v2-rerun-001\candidate\workspace
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
& C:\Users\陈独秀\Documents\agent-harness\.venv\Scripts\python.exe -X utf8 -m pytest -q
Pop-Location
```

复现脚本装配离线路径并拷贝原研究回归及新接口测试，不执行完整pytest；随后运行上方命令可复验全量。GitHub排除工作树副本，下载后先用reproduce.ps1生成。prepare.py拒绝变化的基线源码和已存在输出；未来源码变化应新立契约，不覆盖本报告。PowerShell 5首次因无BOM中文脚本解析失败，原日志reproduce-proof.txt保留，使用UTF-8 BOM修正后独立复现成功（reproduce-proof-02.txt）；发布清单曾遗漏.ps1，独立审查发现后补入并核验。

## 剩余问题与停止条件

真实模型可能仍不选择研究、直接读而绕开目标边界、搜索词不匹配受控标题或费用/上下文先耗尽。这些都是未来要观察的结果；离线参考路径不能保证真实模型也走到这里。本轮不扩大搜索框架、不改变自然语言评分标准、不把引文匹配当全面语义正确。

已知缺失负例的目录为空。它能证实这个分支不会被强行路由研究，但未覆盖“已挂载无关文档、目标资料不存在”的情况。本地参考策略在正例选择唯一目录文档，不能据此声称多文档语义匹配有效；未来真实先导前应另加新Task ID的非空不匹配回归，不修改本批契约或把它混入现有分数。

独立审查见independent-v2-review.md。先导预算单独待授权，本轮到此停止真实生成；用户随后授权的GitHub核对和推送只发布已验证代码及脱敏报告，不触发付费任务。
