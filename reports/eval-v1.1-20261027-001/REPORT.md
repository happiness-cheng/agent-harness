# Adrian V1.1：第一次真实失败驱动的改进实验

日期：2026-10-27，Asia/Shanghai。总判定：**NO_VERIFIED_GAIN**。

已完成所有可离线执行的调查、1轮新候选、隔离复验和完整回归。发现并修复了一个真实存在的工程边界：剩余1次工具额度时，ResearchService提前隐藏读取工具。固定策略的三个案例均从没有实际读取/引文变为真实读取/有效引文；但这不证明真实模型决策或互联网研究能力已经提高。最新的历史研究失败仍未被新模型实验验证解决。

**新增真实Provider生成0，Codex CLI调用0。主工作树未修改；候选只保存在隔离工作树，没有部署、提交、推送或合并。**

## 1. 历史失败：最早失效位置与回放边界

首先核对了V1修复报告、原始评估报告、当前设计和实际Runner/Verifier/Research/Runtime/Trace实现。当前源文件hash与V1交付一致；1259通过、20题15/2/3仅作为历史基线，没有当成本轮新成绩。

### 最新 research.conflict

原始证据的只读副本见 [历史observation](historical/reports/eval-v1/live-acceptance/research.conflict/observation.json)、[原TaskSpec](historical/reports/eval-v1/live-acceptance/research.conflict/task.json) 和 [旧输入提案](historical/reports/eval-v1/research-input-change-proposal.md)。

| 实际观察 | 支持的归因 |
| --- | --- |
| 模型调用1/7，工具0/12，Trace只有model与run | 尚未进入研究工具，不能归因为搜索/正文读取故障 |
| 无Provider错误、无context_limit、无预算耗尽 | 不支持将该次失败归因为Provider、上下文或预算 |
| 模型明确指出缺少两份规则正文、URL或引用，要求提供资料 | 资料入口不可发现/任务输入不充分，与旧独立提案一致 |
| Verifier的已读引文和冲突检查FAIL | 判据正确保留失败；没有因诚实追问而改分 |

本轮用原TaskSpec及逐字保存的最终文本，经过当前完整 HarnessApplication → Runtime → 原Verifier回放。基线与候选均为 **FAIL**，回答逐字相同、工具0、错误归因没有漂移：

- [基线响应回放](history-replay-baseline/result.json)。
- [候选响应回放](history-replay-candidate/result.json)。

这是**历史最终响应重放**，不是重新运行DeepSeek，也不能推断当前模型会做出相同或不同决策。回放显式使用合成模型容量，不能声称重建了原Provider的逐字请求体或原生协议状态。最新失败发生在主Agent调用研究之前，因此本轮修改研究内部的额度门不会直接改变这段固定响应。

旧资料入口提案仍是PENDING_USER_REVIEW。没有把正文塞进原题、降低引用要求，或将旧FAIL改成PASS。

### 更早的完整研究尝试

核对了2026-10-21的三个公开研究收据：

1. 自主search后选择Python官网，read失败，结果tool_execution_failed。
2. 后续尝试第一响应执行两次search，下一次暴露工具为空，最终明确没有正文与引文。
3. 续接已获取的搜索结果后，读取与引文成功，但它不是一次全新的搜索闭环。

原始副本位于 `historical/docs/acceptance/live-research-*.json`。旧报告已记录正文压缩与收尾修复，当前也存在 `test_two_searches_do_not_force_finalization_before_available_read`。历史文件没有保留每次完整HTTP正文、所有原始Provider请求与当时全部预算配置，因此没有人为补造网络内容后宣称精确复现当年整条链路。

## 2. 本轮新发现与候选假说

对已经修过的收尾逻辑继续核对边界，发现当前生产代码仍有：

```python
closing = budget.remaining.model_calls <= 1 or budget.remaining.tool_calls <= 1
```

真实调用链是：

`HarnessApplication._worker → 原Runtime工具循环 → _registry.research（检查网络授权）→ 原共享segment.child预算 → ResearchService.IsolatedModel.generate → 研究Runtime → search/read_webpage → ArtifactStore → 父Session Trace与业务快照`。

研究工具最终输出文字不需要工具额度；当仅剩1次工具额度、且仍有模型调用余量时，旧条件将本可以执行的最后一次read隐藏，并加入必须收尾的指令。它没有越过硬预算，但错误地使已有预算不能用于完成取证。

本轮没有注入故障代码。基线来自未改动的当前源码。新增回归使用合法的7次模型/3次工具预算，其中一次工具用于主Agent调用research，一次search，一次read；两版预算完全相同。

### 在修改前冻结的三个案例

| 场景 | 来源与边界 |
| --- | --- |
| PSF归档短句 | 仅使用旧成功收据保存的11词短引文；不是重建或重新抓取整页 |
| 保修例外 | 合成18个月保修与进水例外，与PSF内容不同 |
| API超时 | 合成12秒超时、一次重试及副作用未撤销，与前两题不同 |

这些是历史失败启发的新工程回归，**不是原历史运行的精确复现**；两变体在候选修改前可见且已冻结，不冒充隐藏holdout。固定本地策略只决定在工具可用时search/read，禁止它给自己评分。

评分在应用关闭后只读重新打开新SQLite，检查实际read Trace、Artifact、逐字引文、owner、业务写入和共享预算。仅状态completed不够：三个基线的模型都正常结束，但没有读取和引文，所以均被判FAIL。

冻结观察器 [test_research_last_read.py](candidate-01/workspace/tests/integration/test_research_last_read.py) 的SHA-256为 `08599bab5de6b2178e5aa8d6a49c305a6b92a7a75c02a0b29ac95467f0f42a14`，候选前后未变。完整契约和假说见 [PLAN.md](PLAN.md)、[frozen-experiment.json](frozen-experiment.json)。

## 3. 自主生成的候选01

当前编程Agent根据上述证据生成了新修改，没有重放旧计算器候选，也没有调用Provider或Codex CLI。生产变化只有研究文件中的一个条件及解释注释：

```diff
- closing = budget.remaining.model_calls <= 1 or budget.remaining.tool_calls <= 1
+ # 最终文字不消耗工具额度；保留最后一次可读机会，硬门仍由 Runtime.consume 检查。
+ closing = budget.remaining.model_calls <= 1 or budget.remaining.tool_calls == 0
```

实际源码：[research.py](candidate-01/workspace/src/min_agent/research.py)；完整增量：[fix.patch](candidate-01/fix.patch)；候选与假说：[candidate.json](candidate-01/candidate.json)。

为什么改这里：关闭工具发生在这个条件，不是SearchService/WebReader、模型Prompt或Verifier。改成0后，仍由Runtime.consume逐次阻止越限；模型收尾预留、父预算归属、网络许可、Session隔离、来源验证和上下文门不变。

预期现象：search后仅剩1次工具时，read仍可见；实际读取后再以0工具收尾，引用来自该Session的真实Artifact。证伪标准：仍未读取、无有效引文、超出原预算或产生业务副作用，均拒绝候选。

| 版本 | 生产源码SHA-256 |
| --- | --- |
| 当前主工作树/基线 | c953ec972ec5d6825d009509e5bbf203b8e3b8dcba3456f26e5be2fcf24a60f7 |
| 候选01 | 41a430b128c946bb1fac419bfc5040ceee820c548cb37161ea748e9de9d49669 |

没有生成候选02/03：候选01已经解决可机械验证的局部机制；继续修改无法代替缺失的新模型决策证据。保留建议是**留在隔离工作树，等待有限真实对照**，不自动合并主工程。

## 4. RED → GREEN 与实际成本

新用例修改前：**3 failed / 3 passed**，见 [red-tests.txt](red-tests.txt)。三个正向任务都没有read/Artifact/引文；无网络许可、0工具、不足模型额度三个反例正确停止。

修改后：新6项及既有研究回归共 **13 passed**，见 [green-tests.txt](candidate-01/green-tests.txt)。独立审查另实际运行9项研究检查与4项预算/权限检查，通过，见 [independent-review.md](independent-review.md)。

三个任务的配对观察一致，见 [mechanism-comparison.json](mechanism-comparison.json)：

| 项目 | 基线 | 候选 |
| --- | --- | --- |
| 工具上限 / 模型上限 | 3 / 7 | 3 / 7 |
| 实际读取次数 | 0 | 1 |
| 已读Artifact和有效引用 | 无 | 有 |
| 实际工具用量 | 2 | 3 |
| 实际本地模型派发 | 4 | 5 |
| 任务状态 | completed | completed |
| 独立机制判定 | FAIL | PASS |

不是仅改错误文字或提前结束：状态本来就正常结束，变化是实际读取和来源证据。代价是同一预算内多执行一次read和一次最终Evidence生成。本轮均为本地策略，外部费用0；这些次数不得当作新Provider调用。

允许研究使用最后一次工具也意味着父任务没有该次额度可供其他后续动作。额外操作仍会被原硬预算暂停，本轮没有宣称任意多步骤任务的资源分配已经最优。

## 5. 冻结评估与完整回归

| 验证 | 结果 | 证据 |
| --- | --- | --- |
| 原始响应回放：两版 | FAIL → FAIL，文本完全相同，工具0 | history-replay-baseline/、history-replay-candidate/ |
| 新机制基线 | 3 failed / 3 passed，真实检出缺口 | red-tests.txt / red-cases/ |
| 新机制与既有研究 | 13 passed / 1 warning，9.16秒 | candidate-01/green-tests.txt |
| 独立代码审查 | PASS，原预算/权限检查无新增问题 | independent-review.md |
| 原20题原契约 | **15 PASS / 2 FAIL / 3 UNKNOWN**；全部符合预期 | candidate-01/frozen-suite/summary.json |
| 20题前后比较 | 同契约20项，判定变化0 | frozen-suite-comparison.json |
| 候选完整pytest | **1265 passed / 3 deselected / 1 warning，283.94秒** | candidate-01/full-tests.txt |
| 编译/依赖一致性 | 通过，No broken requirements found | candidate-01/compile.txt / dependencies.txt |
| 离线wheel构建 | 通过 | candidate-01/build.txt |
| 差异空白检查 | 退出0 | candidate-01/diff-check.txt |
| 类型 / linter | SKIP：项目未配置，未另装工具 | 现有pyproject配置 |

20题基线使用V1修复报告中与当前源码逐字匹配的c953版本结果；本轮新执行的是候选20题，没有将历史基线写成新运行。故意虚假成功、错误引文与UNKNOWN都保留，不把它们当作待修生产缺陷。32K安全暂停没有改动。

完整pytest包含原1259项和新6项；数量变化不是能力得分。Starlette/httpx弃用警告保留，未改依赖消警告。多命令并发，耗时不作性能提升指标。

## 6. 待授权接线的离线验证

为后续真实对照准备了 [live_pair.py](live_pair.py)，默认仅打印计划，不读取凭据。它复用原Provider、CostLedger、HarnessApplication与冻结观察器，不新增Agent Runtime。仅明确--live且项目外凭据才可进入六个隔离worker；审批前没有执行该路径。

通过真实DeepSeek适配器加MockTransport检查了两个版本、三个任务：6份收据均明确 `MOCK_TRANSPORT_FROZEN_IO`、`generation_requests=0`、`new_model_decision_tested=false`。基线4次适配器尝试FAIL、候选5次PASS只是协议接线验证，不是DeepSeek能力证据。见 [integrity-and-adapter-checks.json](integrity-and-adapter-checks.json)。

初次接线Mock发现实验窗口收紧后未相应收紧有效输出能力，造成metadata校验失败；已只修正实验脚本，保留 smoke-01/02日志及diagnostics，未改候选生产源码。独立接线审查又发现缺失usage时会继续，见 [首次接线审查](live-adapter-review.md)。现已改为第一次usage缺失即UNKNOWN并停止整批，保留完整费用预留；独立复审通过，见 [最终接线审查](live-adapter-review-final.md)。

这两个报告脚本错误不是新的Adrian候选轮次，也不是实际Provider故障。修复后缺usage的Mock只尝试1次，正常Mock尝试5次；都没有外部调用。脚本不能自动发现上游静默改价，费用说明已如实澄清。

## 7. 安全、资源与工作树保护

- 前后核对主工程418份文件，修改0；原冻结输入、历史原件未变化。
- 候选生产源码仅research.py变化，冻结测试文件hash不变；权限、硬预算、Source/Session验证器未改。
- 所有库、owner、Session和数据均新建在本实验目录；无正式Memory、工作库或私人文件操作。
- 网络I/O仅被替换为受控公开片段/合成边界；读取本轮未授权来源会被拒绝。完整pytest还覆盖原有跨Session/owner和权限回归。
- Provider/CLI真实生成0。费用信息只查询公开官方定价页，没有认证metadata请求或账户访问。
- 未提交、推送、部署、删除失败证据或自动合并；隔离worktree及所有新失败记录保留。

完整性记录：[integrity-and-adapter-checks.json](integrity-and-adapter-checks.json)。本轮安全结论限于实际检查范围，不是恶意代码沙箱、全平台或全面漏洞认证。

## 8. 总结、剩余问题与最小预算申请

本轮已经证明的工程事实：最后一次工具额度不再被无必要地预留，三个固定策略任务可以在相同额度下完成实际读取并留下原文证据。

仍未证明的目标：新模型是否会选择这一机会；缺材料的最新真实失败是否能在合法资料入口下完成；真实互联网检索和语义结论质量是否提高。因此整体选择 **NO_VERIFIED_GAIN**，没有选择VERIFIED_IMPROVEMENT，也不把离线工作已完成说成BLOCKED。

下一步须获得新的生成授权，完整申请见 [LIVE_REQUEST.md](LIVE_REQUEST.md)：DeepSeek/deepseek-flash/high，基线3题与候选3题，每题最多7次，共最多 **42次生成**；每题Ledger **0.25美元**，整批 **1.50美元**。仅固定公开/合成资料，外部Search/Fetch与Codex CLI均0。预算使用本批显式8192输出和收紧的输入上限，两版相同，不改生产自适应输出；metadata缺失、usage异常、Provider失败或超时即停止，无自动重试。

费率依据为2026-10-27核对的 [DeepSeek官方 Models & Pricing §Model Details](https://api-docs.deepseek.com/quick_start/pricing/)；按峰值未缓存输入0.30美元/百万Token、输出1.20美元/百万Token的保守包络为1.2644352美元。申请的1.50美元是本批本地Ledger门，不冒充Provider账单或账户余额限制；执行前需重新核对费率。

即使授权后的6次任务运行都通过，也只是有限场景观察，不能声称总体成功率或通用自主进化已完成。

## 9. Windows复现

以下使用项目原.venv，不安装新工具；所有输出和basetemp必须换为不存在的新名称，不得重跑覆盖本报告目录中的已有证据。

在项目根目录重放历史最终响应和原冻结20题：

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -X utf8 reports/eval-v1.1-20261027-001/replay_history.py --history reports/eval-v1/live-acceptance/research.conflict --out reports/v11-history-NEW
.\.venv\Scripts\python.exe -X utf8 scripts/run_adrian_frozen_eval.py --source reports/eval-v1.1-20261027-001/candidate-01/source --protocol reports/eval-v1/acceptance-protocol.json --out reports/v11-suite-NEW
.\.venv\Scripts\python.exe -X utf8 reports/eval-v1.1-20261027-001/live_pair.py --plan
```

在隔离候选目录运行回归（确认所选basetemp不存在）：

```powershell
Set-Location 'C:\Users\陈独秀\Documents\agent-harness\reports\eval-v1.1-20261027-001\candidate-01\workspace'
$env:PYTHONPATH='src'
$python = 'C:\Users\陈独秀\Documents\agent-harness\.venv\Scripts\python.exe'
& $python -X utf8 -m pytest -q tests/integration/test_research_last_read.py tests/scenarios/test_research_evidence.py tests/integration/test_research_traces.py --basetemp 'C:\Users\陈独秀\Documents\agent-harness\reports\v11-targeted-NEW'
& $python -X utf8 -m pytest -q --basetemp 'C:\Users\陈独秀\Documents\agent-harness\reports\v11-full-NEW'
```

基线RED的逐次SQLite、工具结果、Trace和输入保存在red-cases；同一冻结观察器的GREEN保存在green-cases。新基线副本在baseline-source，候选源码快照在candidate-01/source。真实调用命令只在LIVE_REQUEST.md给出，审批前不执行。
