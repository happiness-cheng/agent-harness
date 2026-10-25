# P07：必要源码能否改善概念讲解

日期：2026-10-22。[完整材料：共用提示、配置diff、完整源码、同题两组回答及不足](P07-readable.md)；[原始4次结果](../acceptance/p07-knowledge-context-20261022.json)；[发送的源码材料](../acceptance/p07-source-material-20261022.md)。

## 结论与取舍

4次请求全部收到响应，不等于4次回答均完整：无源码2次stop，有源码2次length；均无工具请求。两组都区分了检查点与授权，不能将这种正确性归因于源码。源码让第一次回答引用了具体实现，但第二次仍混淆目标与所有者，而且两次均截断。当前不改生产提示或上下文组件，不把这个结果描述为已修复。

这次还暴露出知识供给的成本：固定2048输出token（包含供应商计入的推理开销）下，较多材料可能伴随更长分析和讲解；本批无法区分究竟是输入长度、内容复杂度、默认讲解方式还是随机性导致截断。不能仅凭本批断言“长源码必然导致截断”。

## 设计与边界

- 单独获得最多4次授权，包含选定非敏感源码；实际4/4，无追加、无失败重试，不执行任何业务工具。
- 固定当前P03候选提示、应用状态、5工具、合成问题、模型deepseek-flash（high）及2048输出token上限。参考材料为null或8172字符的原始源码片段。
- 一题两条件各重复2次，顺序：无/有/有/无。没有固定seed，不估算总体成功率。
- 新问题明确要求区分检查点、人工确认、恢复执行，并询问授权关系；它本身和系统提示已经提供方向。有源码组还看得到带语义说明的既有注释，这在检验资料利用，不是检验模型能独立推导全部设计。
- 源码按AST选择CheckpointNote、ToolResultRef、Checkpoint、CheckpointRecord，及confirm、authorize、save_checkpoint、begin、resume_session。保留原注释；不完整、不作为可运行代码，不发送凭据/业务数据。
- 源码放user参考消息，不改变system。没有检验真实检索召回、自动选材、上下文压缩、恢复端到端行为或提示词注入安全。此实验绕过ManagedModel/ContextManager。
- 本轮提示文件与实验内快照逐字一致。快照来自主工作区，包含用户已有的记忆规则改动；公开分支的生产提示没有合入这段已有改动。不能误认为实验快照等于公开分支默认提示。

## 看源码时应保留的准确区分

`save_checkpoint`保存恢复记录并绑定当时目标，不产生新的批准；`confirm`要求当前提案ID匹配并更新工作状态；`begin`检查恢复的检查点及预算片段与目标绑定；`authorize`针对动作检查模式、确认状态、范围及消费记录。这几个函数互相配合，不是一句“恢复了”就授权所有业务写入。

检查点中的模型笔记未核实，结果引用可以unknown，不能说存档就代表验收。检查点也不自动提供外部业务回滚。代码里的目标ID不是所有者ID。教学类比必须在这些边界处明确收住。

## 实际验证与审查

先新增知识条件测试，旧实现因不识别--factor/--knowledge-file失败（1 failed,5 passed）；补实现后6 passed。缺材料测试进一步确认在读凭据前拒绝。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m py_compile scripts/eval_tool_exposure.py tests/unit/test_tool_exposure_eval.py
.\.venv\Scripts\python.exe -X utf8 -m pytest -q tests/unit/test_tool_exposure_eval.py tests/unit/test_llm_adapter.py tests/unit/test_deepseek_adapter.py tests/unit/test_llm_smoke_guard.py
```

实际：67 passed in 0.99s。覆盖调用同意门、保留报告、首错保存、不重试、工具条件8次及知识条件4次上限；知识条件仅reference_material变化，工具/提示/问题一致。离线测试不证明模型讲解准确。

验证：语法PASS；相关测试PASS；类型/Lint SKIP（pyproject未配置这两项）；安全范围检查与差异审查由主代理完成，无子代理。全量应用回归、安装构建、发布SKIP（仅实验入口与证据变更）。全局corrections-core.md、code-quality.md和review-checklist.md缺失，按已提供AGENTS中的同等要求自审。

模型usage：input_tokens 15724、output_tokens 7596、total_tokens 23320。usage不等于精确费用，耗时也未用于性能结论。两次有源码输出都耗尽2048上限，第二次可见回答明显更短；不能仅按可见字数推断总输出消耗。

## 下一步候选

本轮不增加额度、不更改生产行为。若后续继续，应单独试验按学习问题选择更小且完整的源码片段，控制讲解范围；或者固定材料单独比较输出预算。不能同时改提示、材料与预算再把改善归于其中一项。

## 交付自审

主代理自审结论：PASS（针对实验入口与记录交付，不代表模型回答质量通过）。

| 检查项 | 结果 | 证据与限制 |
| --- | --- | --- |
| 需求满足 | PASS | 4次内比较，两条件各2次，提供完整提示、差异和回答 |
| 边界条件 | PASS | 未同意/缺材料提前拒绝、已有报告保留、首错停止；未做并发写同一报告测试 |
| 安全与范围 | PASS | 仅6个指定文件；源码与合成问答，无业务内容；基础密钥模式扫描无匹配，不等于完整安全审计 |
| 代码规范 | PASS | 复用既有入口；默认保留8次工具实验，知识实验独立硬限4次 |
| 可回滚性 | PASS | 独立分支，不修改生产提示或Runtime；保留主工作区已有改动 |

在公开worktree以PYTHONPATH指定其src重新执行语法检查和相同测试：67 passed in 1.10s。git diff --check通过（仅Git换行转换提示）。核对4份回答均原样出现在完整材料，材料哈希一致、每条件消息哈希稳定。类型/Lint与全量应用回归仍为SKIP，原因见上节。
