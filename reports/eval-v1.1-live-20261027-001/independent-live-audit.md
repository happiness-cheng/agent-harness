# V1.1 有限真实模型实验：独立结果审计

日期：2026-10-27。审计结论：**NO_VERIFIED_GAIN**。六份任务均为FAIL；没有足够证据证明候选提升真实研究能力，也没有证据把一次partial认定为候选代码导致的REGRESSION。

本次审计仅读取新实验目录中的收据、费用账本、模型可见投影及新SQLite库，并以只读连接核对状态。没有发起Provider/Codex CLI调用、网络请求或凭据读取，没有修改源码、评分、数据库或旧报告。原生provider_state和隐藏推理未导出。

## 实际结果

| 运行 | 生成请求 | Session状态 | 实际成功工具数 | 工具预算 | 判定 |
| --- | --- | --- | --- | --- | --- |
| baseline-0 | 2 | completed | 2 | 2/3 | FAIL |
| baseline-1 | 2 | completed | 2 | 2/3 | FAIL |
| baseline-2 | 2 | completed | 1 | 1/3 | FAIL |
| candidate-0 | 4 | partial | 3 | 3/3 | FAIL |
| candidate-1 | 2 | completed | 2 | 2/3 | FAIL |
| candidate-2 | 2 | completed | 2 | 2/3 | FAIL |

生成请求为2+2+2+4+2+2=14，低于批准的42；各任务不超过7次模型和3次工具。所有Provider错误列表为空，14次均有完整usage记录，原预算消耗与收据、模型可见调用条数一致。API正常返回仅表示请求成功，不能替代任务验收。

五个Session为completed，但缺少实际读取和引用而被机械观察器判FAIL。candidate-0为partial/budget_checkpoint：成功执行3个工具，随后另一个工具请求明确not_started，未超额执行；没有普通最终答复。

## 改动分支没有触达

六个Session中，所有请求工具名称仅有recall_memory/read_session_state；没有research、search或read_webpage请求，也没有对应工具Trace或研究模型phase。六个库的Artifact均为空，固定来源搜索/读取审计计数为0。

候选唯一生产修改在ResearchService的工具收尾条件；本批没有进入该服务。三个成对任务的首个模型可见messages和工具名列表还逐字相同。候选PSF一次运行多做了只读查询并耗尽工具额度，是本次真实观察，但无法据此将差异因果归于完全未执行的修改；每条件一次运行也不足以作稳定退化或泛化判断。

五份普通最终答复均明确缺少原文或来源，要求补充材料；未观察到“已读取/已核验”的成功虚报。candidate-0只有预算检查点，没有普通最终成功答复。

## 明确的实验设计缺口

实际首请求都提供了research工具，但都没有冻结URL、来源目录/Artifact标识、case query或来源正文；任何已收到的模型可见请求中也没有冻结原文。此项逐案检查见independent-live-input-audit.json。

冻结固定策略会直接读取case['query']来发起搜索，因而具备真实模型没有的定位先验。真实模型只收到“归档片段”“合成保修”“合成API”等目标，并在空会话中查不到材料。本批因此再次暴露资料不可发现性问题，不能把固定策略GREEN直接外推为真实模型可以进入最后一次read的边界。

付费前应核对正向任务具有等价可发现入口，并预检能否触达待测试的ResearchService路径。本批没有完成该准备；此项属于实验实施方的设计缺陷，主执行者已承担该责任。当前证据支持存在这一缺口，但不能证明它是所有FAIL的唯一原因，也不能保证提供目录后必然成功。任何输入/资料目录改动必须另立提案和新实验版本，不能回填或更改本批FAIL/Gold。

## 安全、隔离与费用

- 六个新数据库各只有一个Session，六个Session ID及owner ID互不相同，并与对应观察器记录一致。
- 六库todos均为0、reminders容器为空、Artifact为0；未观察到业务写入。实际工具均为当前会话只读查询。
- 模型与工具使用均在原持久预算内，candidate-0的第4个工具请求明确未执行。
- 基线、候选与冻结观察器的执行后hash均与预先保存清单一致；入口在开始批次前进行相同的source/observer校验。未修改冻结评分。
- 六份CostLedger均无未决预留，总spent_upper为**0.027355 USD**。这是按本批配置峰值费率和微美元向上取整累计的费用上界记录，未采用缓存折扣；不是Provider实际账单，也没有核验账户余额。
- 审计只检查这些新隔离库；没有为核对本轮结果读取正式个人Memory、工作数据库或私人文件。

## 证据与停止

逐项结构化核对：[independent-live-audit.json](independent-live-audit.json)。输入可发现性与最终普通答复投影：[independent-live-input-audit.json](independent-live-input-audit.json)。批次原始任务收据和账本均保留。

应保留全部六项FAIL和已有离线机制改进记录，将本轮真实能力结论归为NO_VERIFIED_GAIN，停止当前批次。未达到42次上限不构成继续调用的理由；后续来源入口方案及追加实验须单独明确授权。本审计没有追加请求。
