# S02：Skill重复正文去重

日期：2026-10-23。[完整材料：共用提示、Skill、前后工具结果、代码diff与不足](S02-readable.md)；[实际应用输入探针](../acceptance/s02-skill-deduplication-20261023.json)。

## 总结

目录提前读所有正文是SkillCatalog.summaries的系统行为，不是模型过失；模型端仍只先看到目录。用户本轮要求修重复全文，因此不扩大到目录元数据或三级加载。

修复采用当前可见上下文的确定性投影：同名+同正文SHA-256，且第一次成功全文仍在本轮可见消息中时，后续read_skill结果改成already_loaded短通知，带首次call_id。保留第一次全文和后续tool_call_id配对。原始历史不改，真实工具仍执行读取，不做全局或永久“已读”缓存。

比较方案：工具端永久标记简单，但正文更新、上下文压缩和会话隔离容易误判；本次选择可见上下文计算，代价是每次准备输入时扫描/哈希，并不减少磁盘读取。窗口预算也按相同投影计算，避免本可去重的重复内容提前触发上下文上限。只处理成功的read_skill字符串正文，其他工具、失败、JSON无效或摘要结构均不碰。

## 复现与验证

先写真实ManagedModel输入边界测试，旧实现3 failed,6 passed：重复正文、压缩后再次重复、去重后窗口预算三项失败。增加最小实现后9 passed；补充无效/非全文结果边界后13 passed。

覆盖：正文只保留一份；raw消息不变；调用/结果配对；重复准备稳定；正文变化或不同名称不合并；失败和其他工具不处理；旧全文压缩移出后完整重读；用户声称已加载不算；去重后预算；旧输入前缀保持；无效JSON与结构不假定已加载。

实际应用探针仍走HarnessApplication → Runtime → ManagedModel：复用S01合成问题和Skill，原先重复读取后2份正文，本次1份且第二次结果already_loaded；原始历史仍2份。新会话无继承。两个工作区探针均通过8项检查。旧S01报告保持原样，脚本更新为验证修复后行为，可按旧提交重现旧行为。

命令：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest -q tests/unit/test_skill_context.py
.\.venv\Scripts\python.exe -X utf8 scripts/audit_skill_disclosure.py --report '<新报告路径>'
.\.venv\Scripts\python.exe -X utf8 -m pytest -q
.\.venv\Scripts\python.exe -X utf8 -m py_compile src/min_agent/skills.py src/min_agent/context.py scripts/audit_skill_disclosure.py tests/unit/test_skill_context.py
```

结果：

- 定向新增测试：13 passed in 0.41s。
- 本机全量：1 failed,959 passed,3 deselected,1 warning in 32.55s。
- 公开worktree以自身src为PYTHONPATH全量：925 passed,3 deselected,1 warning in 29.83s，之后实际应用探针和语法检查通过。
- 主工作区失败项为test_memory_tools::test_explicit_source_read_and_structured_recall_are_not_silently_head_truncated，返回partial/context_limit。单独启用去重仍失败；在独立进程将project_skill_results替换为identity关闭本次逻辑后也失败（1 failed in 2.35s，诊断复验显示context_limit）。不能把主工作区全量标为通过；未改作者已有记忆/上下文模块来消除该基线问题。两工作区测试集合差异来自作者已有未提交实现与测试。
- 警告为已有TestClient/httpx弃用提示；真实模型调用、本机凭据、业务数据库操作均为0。

## 关键实现

skills.py增加project_skill_results，只对当前消息中有对应read_skill请求的成功正文进行去重；ContextManager._fits对投影计预算；ManagedModel在上下文准备后调用同一投影再送模型。原始上下文哈希和冻结摘要不重写，压缩后重新基于实际可见消息计算，不以已加载标记错误压住重读。

公开分支的context.py保留其自身实现，仅移植三个局部改动；没有覆盖主工作区已有structured_history/selector/state_reader等作者改动。

## 剩余限制

- 目录仍提前读所有正文；不混入本次修复。
- 不阻止模型重复发read_skill请求，也不缓存磁盘读取。
- 只对仍可见的成功全文去重；不把摘要当全文。现有长工具结果摘要策略未改，长Skill完整保留/自动重注入另行设计。
- 原始历史可能保留重复结果，目的是审计，与模型工作上下文分开。
- 很短的Skill替换为带hash/引用的通知可能不节省字节，不以本批推断token、耗时或费用收益。
- 未运行真实模型复验，无新增额度；语义效果仍未知。

## 自审与交付边界

按systematic-debugging、TDD、execution-workflow、code-review、verification-loop执行；主代理自审，不伪造独立子代理审查。全局code-quality.md不存在，按提供的AGENTS要求及现有代码风格降级执行。

本次Skill去重功能的定向验收与公开分支全量通过；主工作区全量存在上述基线失败，不作整体通过声明。目录优化和其他记忆问题未顺便修改。类型/Lint SKIP（项目未配置），独立安装构建及长Skill语义验收SKIP；语法、测试、敏感模式与diff按实际证据记录。
