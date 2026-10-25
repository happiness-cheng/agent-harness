# S04：先固定评测，再改进长Skill上下文

日期：2026-10-23。[完整评测、判据、前后结果、代码diff与不足](S04-readable.md)。

## 项目规则

用户新增要求：每次设计前先准备评测集、测试集与判据，跑改前基线，再设计实现，最后同条件比较。已写项目AGENTS.md，不修改全局记忆。开发样本与固定验收样本分开；不把小公开样本称盲测，不把程序通过称模型表现提升。

## 本轮顺序与结果

先冻结skill-lifecycle-v1（7项），通过真实ManagedModel跑出现状4/7；再针对L02写失败测试，确认长Skill被替换为普通工具摘要；最后修改一处入口条件，并以原suite复测5/7。原始基线与标准未覆盖，suite哈希和逐项expected一致。

完成：窗口充足时read_skill长规程不走普通工具摘要；仍接受总体预算和重复正文去重。未完成：历史整轮压缩后恢复仍需规程（L03）、恢复最近一次显式版本（L05）。不能以5/7宣布生命周期交付。

实现取舍：用已登记调用的tool_call_id/name判断，避免把任何“看起来像Skill”的用户文字当规程。仅绕过普通工具摘要，不把所有历史永久钉住；没有添加模型提示或扩大权限。旧会话如果曾经冻结摘要并已覆盖，仍可能触发上下文版本不一致保护，迁移/恢复未在本步解决。

## 两套评测

- 机制验收：docs/acceptance/s04-skill-lifecycle-suite-v1.json。短规程、长规程、压缩后恢复、重复读取、最新版本、未加载材料、预算阻断。7项均为硬判据，不用平均数抵消丢规则或越预算。
- 模型行为：docs/acceptance/s04-skill-behavior-suite-v1.json。固定润色事实边界、unknown结果与副作用、新旧版本金额规则三个合成问题；规定必要内容和失败条件，不强求固定措辞。比较不加载/已加载/压缩后候选恢复，并记录token、请求及截断。当前未执行、未追加额度；样本与系统提示可能重叠，需对照且不能保证能独立归因Skill。

## 实际命令与结果

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/audit_skill_lifecycle.py --suite docs/acceptance/s04-skill-lifecycle-suite-v1.json --report '<新报告路径>'
.\.venv\Scripts\python.exe -X utf8 -m pytest -q tests/unit/test_skill_context.py --tb=short
.\.venv\Scripts\python.exe -X utf8 -m pytest -q --tb=short
.\.venv\Scripts\python.exe -X utf8 -m py_compile scripts/audit_skill_lifecycle.py src/min_agent/context.py tests/unit/test_skill_context.py
```

新增长Skill测试先1 failed，再整组14 passed in 0.32s。

本机全量：1 failed,979 passed,3 deselected,1 warning in 48.64s，仍是前两轮已记录的记忆原文回查context_limit问题。未扩大范围修记忆模块，不作本机整体通过声明。

公开worktree指定自身src为PYTHONPATH：945 passed,3 deselected,1 warning in 37.90s；相同生命周期验收复测仍5/7。警告为已有TestClient/httpx弃用提示。语法检查通过。脚本运行成功退出0仅表示基线测量完成，必须读取逐项passed，不能把退出0视为7项全通过。

## 自审与边界

主代理自审PASS（本次评测设施及L02局部修复），没有子代理。新增项目协作规则逐条符合用户要求；没有改权限、凭据、数据库schema或系统提示。原始历史不变；公开分支只移植本步局部条件及请求名追踪，保留主工作区既有改动。

类型/Lint SKIP（项目未配置）；本轮未新增依赖，未重跑安装构建。真实模型评测SKIP（待单独授权）；自动触发压缩的端到端验收仍缺。敏感模式和diff检查不是完整安全审计。

下一步先利用已冻结的L03/L05设计恢复机制，再增加自动触发压缩的集成检查。不能悄悄放宽“全文可见”来把摘要判为通过，也不能为通过测试向全部上下文无上限追加所有Skill。
