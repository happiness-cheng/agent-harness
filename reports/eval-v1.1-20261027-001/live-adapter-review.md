## 代码审查结果

### 发现的问题

[MEDIUM] usage缺失不会停止本次Provider任务

File: reports/eval-v1.1-20261027-001/live_pair.py:67

Issue: RecordedProvider.generate直接返回CostBoundedModel.generate；既有CostLedger.settle在usage缺少input_tokens/output_tokens时保守计入整笔预留，但不抛错。因而recorded.errors为空，live_pair.py第88—96行可以输出PASS并返回0，父批次继续。已用真实DeepSeek适配器加MockTransport复现：所有5次POST都不返回usage，worker仍执行5次、usage=[]、receipt_verdict=PASS、worker_status=0；账本出现5次unknown_kept_full_reserve。外部请求0。证据见review-unknown-usage-result.json与review-unknown-usage-probe.txt。这与LIVE_REQUEST.md第33行“异常usage将停止”的承诺不符。

Fix: 只在实验脚本的RecordedProvider内核对本次新增的usage是否包含有效、完整的input/output字段，缺失时记录usage_unknown并抛出，让worker返回UNKNOWN且父批次停止；继续保留原账本的保守预留，不需更改既有finance库。补充缺usage/部分usage的MockTransport反例。

[LOW] 文档不能声称自动发现真实费率变化

File: reports/eval-v1.1-20261027-001/LIVE_REQUEST.md:33

Issue: 当前脚本使用显式配置的input_rate=.3/output_rate=1.2，Providerusage仅返回Token数，没有账单单价；因此“实际费率变化…将停止”不能由现有接口自动实施。没有凭据/账单查询，也不应新增未经授权的账户读取。

Fix: 明确这是已核对价格下的本批预算假设；若费率变更需重新核定，脚本只能因费用观测/预留门失败而停，不能宣称自动监测真实单价。

### 已核对的边界

- 默认无--live或显式--plan在读取fixture、凭据及Provider导入前即返回PLAN。实际执行了--plan并提供一个不存在的凭据路径，退出0、仅打印计划，见review-live-plan.txt。
- CLI真实路径要求明确项目外凭据，resolve后检查项目子路径；未运行任何真实--live，也未读取凭据。
- 父入口先核对两版source hash及冻结观察器hash；每版本3任务顺序执行，各raw客户端max_requests=7，合计最多42生成尝试，没有自动重试。
- 每worker独立CostLedger .25，配置合计1.50；不是Provider账户账单硬限制。父worker错误/无收据/超时会停止批次并保留UNKNOWN。
- 真实容量来自metadata，再向下收紧至65536；max_output_tokens同时不超过收紧窗口，不扩大Provider容量。8192仅该实验显式配置，两版一致。
- 模型/计数元数据投影不改变冻结观察器checks与机械verdict；收据另外区分机械评分、Provider错误、MOCK/live来源。Mock标记的generation_requests=0、new_model_decision_tested=false，未把接线测试当真实决策证据。

### 总结

| Severity | Count |
|----------|-------|
| CRITICAL | 0 |
| HIGH | 0 |
| MEDIUM | 1 |

Verdict: PASS（按角色规则，无CRITICAL/HIGH；上列MEDIUM应在声称异常usage会立即停止前修复）。

这是初次待授权接线审查，不能替代真实实验批准。没有真实Provider/Codex CLI生成、外网请求、凭据或正式数据库访问。仅创建本审查的新报告与合成测试输出，没有修改报告脚本、源码或冻结评分文件。
