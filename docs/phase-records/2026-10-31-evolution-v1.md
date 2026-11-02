# 持续进化V1：启动契约

日期：2026-10-31。用户要求在验证器约束下自主持续优化，并授权现有DeepSeek：整批最多50次生成请求、1美元，仅合成任务、相关代码、脱敏失败日志。当前助手负责实现。默认采用15分钟单批墙钟上限、最多3个候选；不因重启重置资源。用户没有授权自动提交/推送/发布或修改真实用户Memory/数据库。

参考《深入理解AI Agent》第9章“构建可长期运行的持续进化闭环”（https://bojieli.github.io/ai-agent-book/book/chapter9/）：在线执行留证；离线诊断、提案和验证；同时观察退化、迁移、成本、安全与工程影响。更新流程成立和后续Agent获益必须分别证明。

## 基线、目标与边界

现有run_improvement是一次计算器绑定修复演示，主动在副本注入故障；night能持久排队，但没有从真实失败选择候选、迭代候选版本、开发/保留对照和无收益停止的进化控制器。新模块当前不存在。EVAL-010最终5文件hash已核对与75项通过证据一致，该证据仅复用原验证器，不代表新功能已通过。

本批实现：基于当前实际源码建立隔离版本，从开发任务的可靠失败生成最小候选，固定Verifier/任务/fixture，对开发和保留任务重跑，改善且无退化才接受为实验champion；持久保存版本、预算、证据及停止原因。没有失败则停止，不注入故障制造收益。生产采用保持用户决定。

V1唯一自动写范围是已有AST受限的calculator.py四则绑定。先复用render_candidate、run_task、CostLedger、JsonDocument与文件租约；不开放任意代码执行/Prompt/权限修改，因验证器尚无相应任务质量覆盖。未来范围需要单独定义验证适配器。本批的循环和真实实验不称为整个Adrian已具备通用自进化。

## 冻结必要验收

1. 基线无失败不调用候选生成器；不将预设FAIL/UNKNOWN诊断题当作优化目标。
2. 候选改善开发失败且全部保留集保持时，才在实验副本晋升；退化/UNKNOWN/标准或保护文件变化均拒绝。提案不接收保留题答案。
3. 生成/评估前持久预留次数和费用；超限不调用后端，未知进程结果保留资源并停止，重启不重放中断请求。
4. 状态原子落盘、单运行者；重复候选、无收益、有限轮次、墙钟或资源耗尽停止。恢复时核对源码、协议和证据，陈旧/被改文件不认证。
5. 来源、候选diff、开发/保留结果、成本和激活版本可查；不以模型说成功作为晋升依据。保留上一版本及回滚方法。
6. 合成后端先验证机制，再在上述50次/1美元边界内对现有DeepSeek跑一次实际基线/有需要才提案。真实实验失败、无收益或UNKNOWN如实报告；不调标准制造提升。

真实DeepSeek费用采用官方2026-10-31页的Flash峰值上界：输入0.3美元/百万Token，输出1.2美元/百万Token；本项目CostLedger已用此配置。它是保守用量估算/预留，不是Provider账单查询。模型不提供可用usage或进程结果未知时不当作免费。

## 计划与停止

先冻结上述判据并写失败测试，再实现持久控制器/受限后端/CLI，跑相关回归与一次真实有界实验，最后追加结果与回滚。达到核心可用、必要边界通过即可阶段收尾；不实现无限后台值守、通用Agent群、权重训练或自动生产发布。

## 2026-10-31阶段交付

已新增evaluation/evolution.py、evolution_backend.py、evolution_cli.py，以及unit/test_evolution.py、integration/test_evolution_integration.py；原Verifier、业务计算器、权限、Memory、工具分类和依赖未改。使用入口见[EVOLUTION_V1](../EVOLUTION_V1.md)。

为什么这样选：原run_improvement只有一次故障演示，新控制器从当前实际源码开始，只有可信失败才生成候选。持久状态保存预算、版本、结果及停止原因；候选接受后，后续执行确实加载新实验源码。没有失败就停止，不为了成绩修改当前业务代码。

核心晋升门：

```python
if safe and not regression and holdout_pass and improved:
    data['champion'] = name
    data['accepted_versions'] += 1
```

safe要求安全PASS且无UNKNOWN；regression检查旧PASS变成非PASS；holdout_pass要求全部保留任务通过；improved要求至少一个开发失败转PASS。结果还核对固定TaskSpec、协议及来源hash。更新器只收到开发失败，不接收保留任务/答案/得分。可信控制器与本地文件不是抵抗同权限恶意Python进程的沙箱。

### 实际验证

- 新模块缺失的基线已由pytest导入失败确认；新增证据复核测试先失败，随后修复结果hash检查；同绑定仅格式变化测试先失败，随后修复为保持原字节，不把格式差异算进化。
- 最终相关回归**94 passed，exit 0**，覆盖原评测/夜间/清理、新控制器、真实Harness集成及连续两次候选晋升。94是程序测试数量，不是真实模型成功次数。
- 覆盖保留集退化拒绝、UNKNOWN不重试、调用前预留、额度不足不进入后端、中断不重放、标准/保护文件变化即停、重复候选止损、回滚不重置预算。
- 最初两个测试文件同名造成pytest收集冲突，改为test_evolution_integration.py，未改判据。原EVAL-010文件hash仍一致。

### 两次真实实验

仅合成计算、相关源码和开发失败工具记录；沿用现有DeepSeek Flash账户与本机代理。原整批最多900秒。

| 实验 | 结果 | 生成请求 | 最坏请求槽位 | 峰值费用上界估算 |
|---|---|---:|---:|---:|
| live-001，当前实际源码 | 5 PASS；无可靠失败，不提案 | 10 | 15 | $0.007182 |
| live-controlled-001，明确合成故障副本 | 基线2 PASS/3 FAIL；真实模型提1候选；复验5 PASS；激活实验candidate-1 | 21 | 31 | $0.016058 |
| 合计 | 无未知费用预留，不替换业务源码 | **31/50** | **46/50** | **$0.023240/$1** |

第二次实验先确认第一次已停止且active/费用预留为空，只分配剩余35槽位、$0.992818和710秒。没有重新取得一份50次/$1额度。实际生成次数与保守预占槽位分别报告；不含无用户数据的模型能力元数据读取。费用依据[官方峰值费率](https://api-docs.deepseek.com/quick_start/pricing/)用量估算，不是实付账单。

当前实际源码原本5题通过，结论**NO_VERIFIED_GAIN**；没有为触发更新而破坏业务基线。另一个合成故障副本完成真实提案、验证和激活，结论**受控故障下LIVE_GENERATION_AND_INTEGRATION_VERIFIED**。这不证明真实用户任务收益或整个Adrian通用进化。两份停止计划再run均未调用模型或改状态/预算。

证据在[reports/evolution-v1-20261031](../../reports/evolution-v1-20261031/)：regression-final.log/command、source-final-manifest.json、live-summary.json，以及两次实验的state/report、runs的SQL/结果、proposals的真实提案/comparison。已核对代码hash、语法、此前EVAL-010文件及当前业务计算器保持；回滚补丁预检查exit 0。

### 回滚与收尾

本批implementation.patch只包含5个新增代码/测试文件，保留文档及所有证据。已实际执行只读预检查：

```powershell
git -c core.autocrlf=false apply --reverse --check reports/evolution-v1-20261031/implementation.patch
```

需撤销且确认后续改动无冲突时，再去掉--check；本轮没有执行撤销或删除。CLI rollback可返回已有实验版本，只移动实验指针、追加事件，不删除历史、不重置预算。候选未自动替换生产目录，因此没有生产发布需回滚。

阶段收尾。自审和程序验证覆盖限定范围，没有独立第二审查者。保留集很小且可审阅，不能声称总体成功率、长期无人值守或通用自主编程。扩大到Prompt/Skills/Memory/任意Harness代码之前，先选真实失败并定义该载体的独立质量与安全评测；没有可验证失败时保持停止。未来实验沿用剩余额度或另定新授权，不重复初始化计划翻倍预算。
