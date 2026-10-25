# S05：整份Skill保留判断的评测

日期：2026-10-23。[完整提示、Skill、同题两次原文回答及不足](S05-readable.md)。

## 先标准，再原型

用户批准讨论中的组合方向后，先冻结skill-retention-v1，包含6个判断场景和3类程序guard；先跑全保留/全移除基线，再制作独立选择器原型。未改生产代码、主Agent提示、Skill正文或压缩器。gold expected与模型输入分离，测试检查未发送gold字段。

标准优先顺序：JSON/id合法；必需规程不移除；已明确替换的旧版不混作当前版；原文不改写；预算不足明确处理。无关保留和字节量是次要指标，不能抵消安全门。uncertain保守保留。输入无效/模型异常的保守退路只在本轮评测工具内存在，不宣称已接入Runtime。

## 结果

全保留安全门5/6、完整选择1/6，旧版混留导致一例安全门不通过；全移除均0/6。真实DeepSeek独立授权最多12次，实际12/12完成，全部stop、有效JSON，选择判据12/12通过，意图不明两次均uncertain。无工具请求或执行，输出未修改规程内容。

只说明对6个清晰、人工结构化场景的分类路径可行。不是生产准确率、没有盲测、没有从原始长对话自行识别任务状态，也没有测试真正压缩后的遵循效果。旧版淘汰在样本上下文中已明确说明，不能据此证明能自主发现所有版本冲突。

资源：模型筛选input_tokens=4276、output_tokens=1822、total_tokens=6098。保留的正文累计1844 UTF-8字节，对照全保留3402字节；这是各请求所选正文之和，不是完整请求token或收益。短Skill场景筛选开销可能超过节省，不能直接宣布成本改善。选择频率、是否只在压缩时触发、与确定性版本淘汰/任务关联的比较仍待研究。

## 验证

新增8项评测工具测试，入口未存在时8个setup错误；实现后发现一个测试fixture缺name，修正fixture；随后8 passed。补充调用门及错误停止后，相关4文件50 passed in 1.27s；公开worktree相同50 passed in 1.35s，语法检查通过。

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/eval_skill_retention.py --suite docs/acceptance/s05-skill-retention-suite-v1.json --report '<新离线报告路径>'
.\.venv\Scripts\python.exe -X utf8 -m pytest -q tests/unit/test_skill_retention_eval.py tests/unit/test_skill_context.py tests/unit/test_skill_metadata.py tests/unit/test_skill_disclosure_eval.py --tb=short
.\.venv\Scripts\python.exe -X utf8 -m py_compile scripts/eval_skill_retention.py tests/unit/test_skill_retention_eval.py
```

真实复验必须另获额度并显式--allow-live、项目外凭据及代理。本入口固定6场景×2重复，全局12次；无自动重试，首个传输错误保存后停止。已有报告用x方式拒绝覆盖，未同意不读凭据。tools=None，无工具执行器。原生provider_state不写公开报告。

## 自审与下一步边界

主代理自审PASS（评测工具交付，不是生产机制通过）。未知/遗漏/重复id、错误类型、uncertain保留、gold不外传、省字节但丢规程不合格、未同意/已有报告不读凭据、12次上限及首错停止均有测试。固定suite哈希与离线/真实报告相同；所有回答原文保留。没有独立子代理审查。

本轮没有生产实现改动，未重复全量应用回归；前一轮的本机记忆窗口基线失败仍未处理。类型/Lint SKIP（未配置），安装构建SKIP（无依赖/打包变更）。敏感模式和差异检查执行，不是完整安全审计。

候选模型判断值得继续原型验证，但不直接放进生产压缩流程。下一步系统侧需要已加载快照、版本、当前可见状态、预算与恢复配对；继续使用S04未通过的L03/L05，不换成容易通过的判据。对模型选择应增加相似Skill、原始历史及真实任务切换，并比较不用模型的低成本规则。新的真实调用均需另行授权。
