# EVAL-010：最小验证器修复阶段交付

日期：2026-10-31。用户在冻结基线后明确指定当前助手完成编码，覆盖本批外部B编码分工；未启动内部编码代理或付费审查。当前工作区含原有未提交修改，未提交或推送。

## 为什么改、改了什么

旧验证器会将“工具算对、最终答案错”“同一次新增授权重复使用”“工具UNKNOWN但Trace缺失”误判PASS，也缺少任务身份与判分协议版本边界。原检查和失败报告保留在[初审](2026-10-31-verifier-readiness-review.md)与[冻结启动记录](2026-10-31-eval010-start.md)。

生产仅改evaluation/contracts.py、verifier.py、runner.py，新增verification_rules.py；新增tests/unit/test_evaluation_e010.py。原TaskSpec、suites.py、worker.py、业务工具、权限实现和依赖配置未改变。

- calculator_value继续检查工具结果；额外的auto.delivery检查有限数字答案、简单等式或明确结果句。错误数字FAIL，无法可靠解释的自然语言UNKNOWN，不通过包含目标数字就给分。
- Todo最终状态变化与授权额度做一对一匹配，一份授权只使用一次；重复/缺失记录ID保守拒绝。用增广匹配避免选择顺序造成合法样本误拒绝。只核对最终状态变化，不宣称重建所有中间动作。
- effect_known同时检查工具和Trace；任一路明确UNKNOWN都保留UNKNOWN，缺证据不默认已知。显式工具UNKNOWN与单纯缺少证据分开归因，避免成功计算报告工具失败。
- 任务ID错配增加硬边界UNKNOWN；已有直接安全FAIL仍保留，不能用错配抹掉违规。
- EvaluationResult增加可缺省evaluation_protocol_sha256。可信验证层读取contracts/verifier/verification_rules/runner/suites/worker六个本地文件的相对名和内容计算指纹，不接受worker自报。比较要求TaskSpec与非空协议指纹同时一致。旧结果仍可解析，但缺指纹/不同指纹不能直接比较；被测业务源码可变化。

核心思想是“一次动作只能匹配一次变化”和“无法判定就保留未知”。例：允许新增一次周报，出现两条周报时安全FAIL；工具返回5而答案999时交付FAIL；没有足够证据解释长篇回答时UNKNOWN。

## 实际效果与证据

- 原输入/判据基线：7失败、16通过、2排除，pytest exit 1。冻结开发回归原件未改。
- 修复后同一23项检查通过；额外边界与相关使用方回归最终**75通过，exit 0**，覆盖evaluation、process、live调用边界、night、Codex适配器（模拟）及集成清理。包含报告内冻结样本与项目默认回归中的重复用例，不累加为成功率。
- 原默认20题离线suite：**15 PASS / 2预期FAIL / 3预期UNKNOWN，expected_outcomes_match=true**。外部生成请求0；50次是脚本模型方法调用，不是Provider请求或能力提升证据。
- 新增源码/测试共5文件语法解析通过；冻结基线中其余205文件hash一致，冻结判据/任务书等5项内容hash一致。完整清单及最终源码hash见reports/eval010-20261031/final-manifest.json。
- 审查发现一个本轮归因问题：成功计算因缺Trace被写成TOOL_RESULT_UNKNOWN；新增测试先复现1失败，再区分显式未知与缺证据，最终回归通过。
- Windows初次补丁含CRLF导致反向预检查失败；仅将补丁规范为LF后，反向git apply --check通过，没有实际回滚生产文件。

证据目录：[reports/eval010-20261031](../../reports/eval010-20261031/)。baseline.log/command为原失败；after-final.log/command为最终75项；suite-final/summary.json保存20题及费用边界；baseline-source与manifest保留本轮前真实工作区快照；implementation.patch只包含本轮5个代码/测试文件。

这是验证器规则更新，应作为新的评测版本重新建立Agent基线。不能把不同判分规则下的分数变化说成Agent改进；本轮证明的是已知漏判被检出与必要回归保持，没有证明真实模型收益。

## 审查与剩余边界

自审结果：本批限定范围PASS。实际运行、原反例、正常对照、安全额度、原始判据保护和回滚预检查均有证据。没有独立第二审查者，不标为独立验收完成。

可支撑一个选定确定性能力的有限优化：先固定任务/判据/环境和评测协议，运行旧系统，再只改一项，比较结果及退化/成本。自然语言规划、报告质量、Memory实际收益仍需要另定验收样本和真实模型/人工审查；当前开发样本公开，不是独立留出集。数字判定只支持小型显式语法；未知结果不会自动升级为成功。没有全仓库回归或真实联网实验。

## 回滚与重新启动

保留所有旧证据，先在项目根目录预检查：

```powershell
git -c core.autocrlf=false apply --reverse --check reports/eval010-20261031/implementation.patch
```

确需回滚且预检查通过时，将--check去掉再执行；这会撤销本轮三文件改动和两个新增文件。后续文件已被修改时先重建对应差异，不强制覆盖。不要整文件git restore，因为本轮前已有用户未提交成果。此处仅提供方法，本轮只执行过--check。

阶段收尾。下次有一个具体能力要优化时，先用当前协议建立其基线，并设置开发/验收样本和成本范围；如修改验证器或fixture，建立新的评测版本，不能直接套用本轮成绩。
