# Adrian V1.1：已授权真实对照结果

日期：2026-10-27。结论：**NO_VERIFIED_GAIN**。

已完成用户批准的两版各3题，共6次任务运行。实际 **14次DeepSeek生成**，无Provider错误、无自动重试；原判据下基线0/3通过、候选0/3通过。全部任务均未调用research，候选修改的ResearchService分支没有被触达，因此没有证据证明真实能力提升，也不能据此确认候选代码退化。

这里存在实验准备缺陷：固定资料只存在于受控I/O中，任务没有向真实模型提供资料可发现入口。固定策略知道case query，真实模型没有同等信息。我没有在付费前设置触达预检，导致本批没有检验到修复假说；这个责任属于实验设计，不能归因为Adrian不会研究。

## 1. 授权与执行边界

用户对上一轮明确预算申请回复“是”。执行前核对两份源码、冻结观察器、独立接线审查hash、项目外既有凭据路径、本机代理和公开定价。使用原live_pair.py，仅执行这一个批次。

授权为基线3题、候选3题，每题至多7次生成；整批至多42次、每题费用Ledger0.25美元、总计1.50美元。实际未使用的28次上限不是自动追加任务、改输入或补跑的授权。

- [授权与预检收据](../eval-v1.1-live-20261027-001-authorization.json)。
- [执行计划](approved-plan.json)。
- [整批收据](batch.json)。
- 使用的方案及数据范围：[原LIVE_REQUEST.md](../eval-v1.1-20261027-001/LIVE_REQUEST.md)。

原离线报告、FAIL、冻结输入和判据均未覆盖。没有充值、换账户、Codex CLI调用、外部Search/Fetch、部署、提交、推送或自动合并。

## 2. 原判据的实际结果

| 任务 | 基线 | 候选 | 生成次数：基线/候选 | 实际工具用量：基线/候选 |
| --- | --- | --- | --- | --- |
| PSF归档短句 | completed / FAIL | partial / FAIL | 2 / 4 | 2 / 3 |
| 合成保修例外 | completed / FAIL | completed / FAIL | 2 / 2 | 2 / 2 |
| 合成API超时 | completed / FAIL | completed / FAIL | 2 / 2 | 1 / 2 |

每项task的原检查均没有观察到read Trace、Artifact或有效引文。执行completed只表示模型正常回答，不能当作任务已核实；本次进程exit=0也只表示批次运行结束，不表示6题通过。

实际动作只有recall_memory和read_session_state。6个Session均为新建，归档为空、没有Artifact目录；5份普通最终回答明确承认缺少原文，要求提供文档、产品标识或引用，没有观察到取证成功虚报。

六份实际明文回答与会话投影见 [answers.md](analysis/answers.md) 和 `analysis/*-transcript.json`。只导出role/content/工具请求和结果，排除provider_state及隐藏推理。原Runtime SQLite作为证据保留，不把内部原生协议状态包装成明文分析。

### 候选PSF的partial

该任务连续尝试归档检索与当前状态查询，达到3次工具额度后又请求一次recall_memory。原Runtime阻止第4次请求并进入检查点：

- task.error_code = budget_checkpoint。
- Trace还记录checkpoint_invalid_response。
- 普通最终答复为空，检查点没有形成有效的模型整理。

实际只执行3次工具，没有越限。最后一次收尾响应的明文/finish_reason没有被原接线单独保存，因此不能细分为无效JSON、Schema缺失或其他响应形态。没有追加请求试图“补回”这段信息。

独立审计确认三组首个模型可见messages与工具名称相同，而修改的研究内部条件在所有运行中都未执行。因此这一份partial不能被直接归因为候选源码引入了退化；也不能用预算暂停当作需要放宽预算的理由。

## 3. 最早失效位置与实验设置混杂

实际链路停在：

`真实模型主循环 → 查询新Session归档/状态 → 没有资料入口 → 要求补充材料（或到预算检查点）`

没有进入：

`research → search → 剩余1次工具 → read_webpage → Evidence`

本批不是搜索失败、正文读取失败、研究子循环预算错误或引用验证器误判。搜索/读取根本没有发生；原Verifier保留FAIL是正确的，没有为了诚实追问而改分。

实验设计问题由实际输入支持：

1. 目标写“归档片段”“合成保修”“合成API”，但初始Session没有归档、附件或Artifact ID。
2. 受控search/reader里有冻结正文，主模型却没有可发现的目录、URL或“该资料集可由research访问”的环境说明。
3. 固定策略直接使用case['query']并选择research；真实模型并没有相同的入口知识。

前一轮已识别过类似资料入口风险，本批仍将该缺口带入真实对照。因此，离线固定策略的3项FAIL→PASS只能保留为工程机制证据，不能升级成真实模型能力证据。

## 4. 预算、usage与数据隔离

实际usage与新SQLite预算及收据逐项一致：

| 项目 | 实测 |
| --- | --- |
| 真实生成 | 14次，授权上限42次 |
| 输入Token | 67,756 |
| 输出Token | 5,851 |
| 其中缓存输入 | 59,133 |
| 配置峰值费率的费用Ledger合计 | **0.027355美元** |
| 未决费用预留 | 0 |
| Provider错误 / 自动重试 | 0 / 0 |
| 唯一owner / Session | 6 / 6 |
| 业务写入 / 越限 | 0 / 0 |
| research / search / read | 0 / 0 / 0 |
| Codex CLI / 外部Search或Fetch | 0 / 0 |

0.027355美元是按配置峰值费率、不依赖缓存折扣并逐次取整的本地结算上界，**不是实际账单**。没有查询账户余额或发票。执行前核对的 [DeepSeek官方 Models & Pricing §Model Details](https://api-docs.deepseek.com/quick_start/pricing/) 峰值未缓存输入为0.30美元/百万Token、输出为1.20美元/百万Token。

真实输入仅包含冻结问题、合成主会话标记、应用系统提示/工具Schema、当次空会话查询结果。由于没有读取来源，冻结来源正文也没有经read工具进入模型。没有将正式Memory、工作数据库、私人聊天或用户文件发送给Provider。

观察器中的no_real_network字段指受控来源Fetcher没有联网，不表示Provider没有联网；本批14次确实是真实Provider生成。只读metadata发现不计为生成，不能把14次理解为包含所有类型HTTP请求的总数。

## 5. 独立验证与源码保护

独立审计实际只读核对了6份数据库、预算、Trace、模型收据、费用账本和首请求输入。结论NO_VERIFIED_GAIN：

- [独立审计报告](independent-live-audit.md)。
- [数据库与资源审计](independent-live-audit.json)。
- [资料可见性/首请求审计](independent-live-input-audit.json)。
- [主执行者只读汇总](analysis/summary.json)。

最终还核对主工程、两份源码、冻结观察器和驱动hash。没有修改这批已执行的程序或评分标准；没有改数据后补跑。

| 对象 | SHA-256 |
| --- | --- |
| 基线 | c953ec972ec5d6825d009509e5bbf203b8e3b8dcba3456f26e5be2fcf24a60f7 |
| 候选 | 41a430b128c946bb1fac419bfc5040ceee820c548cb37161ea748e9de9d49669 |
| 冻结观察器 | 08599bab5de6b2178e5aa8d6a49c305a6b92a7a75c02a0b29ac95467f0f42a14 |

本阶段只执行已审查程序和生成报告，未新增源码修复，所以没有为了报告再消耗模型调用或重复完整pytest。此前候选1265通过和冻结20题15/2/3仍是独立离线证据，不能替代这里的真实FAIL。

## 6. 本轮决定与后续提案

总判定维持 **NO_VERIFIED_GAIN**：

- 没有观察到原目标完成或研究能力提升。
- 未触达修改点，不能据此确认代码REGRESSION。
- 授权批次正常完成且usage齐全，不标成外部资源BLOCKED来掩盖实验设计问题。
- 原工程机制修复可以保留在隔离候选，但没有自动合并依据。

下一步应先修订资料可发现性的实验输入，并设置触达预检，**而不是直接追加相同请求**。独立提案已保存为 [SOURCE_ENTRY_PROPOSAL.md](SOURCE_ENTRY_PROPOSAL.md)，状态PENDING_USER_REVIEW，尚未实施或付费运行。

提案只提供资料集和标题等定位元数据，不暴露正文或Gold；需新任务ID与契约hash，只在新协议内部对照，不能回填这批6个FAIL。先导若未触达目标分支就停止，避免再以固定策略通过代替真实可发现性。

这是原任务“验收标准变更先单独提案”的边界；本次“继续”用于完成现有运行的分析与交付，没有据此更换冻结输入或另开生成批次。

## 7. Windows只读复核

本报告目录内analyze.py只读原收据/SQLite，输出到新的analysis目录；原目录已经存在时会拒绝覆盖。若需重做分析，应先将脚本中的输出目录改为一个新的分析目录名，不能删除或覆盖原证据。

项目根目录可查看本批真实结果：

```powershell
Get-Content reports/eval-v1.1-live-20261027-001/analysis/summary.json -Raw
Get-Content reports/eval-v1.1-live-20261027-001/analysis/answers.md -Raw
Get-Content reports/eval-v1.1-live-20261027-001/independent-live-audit.md -Raw
```

原执行命令记录在已批准协议及相邻command日志；它包含--live，不能因需要复核结果而再次执行。只读复核不需要读取凭据或发起Provider请求。
