# C02：区分模型选择与规则补充

## 已确认的设计（2026-10-23）

规则识别日期、否定和条件等文字特征，不证明任务相关性。每条摘录分别记录 `model_selected`、`rule_matched`，不再合并成无法追查的“必须保留”。

预算充足时，保守保留规则补充，不额外请求模型。候选原句超过摘录预算时，先复核仅规则命中的片段；无工具模型结合完整原文与首次选择，逐条返回 keep / drop / uncertain。只允许明确 drop 的候选退出上下文；keep 或 uncertain 仍受保护，放不下就暂停。模型选中且命中规则的原句不进入这次删除复核。没有模型的结构化层保持保守规则，不伪装成做过语义判断。

复核共用父任务预算，预留收尾调用，检查完整请求窗口；缺失、伪造、重复 ID、停止、预算不足或不完整响应都拒绝。原文保持不变；完整复核依据和决定存为会话内 artifact，摘要保留引用。逐字引用与复核通过不证明事实真实性或模型判断一定正确。

## 评价场景与基线

- 同一条普通相关原句与无关“禁止转载”页脚：区分选择来源，预算足够时无新增调用。
- 超预算页脚：明确判定无关后移出上下文，原文及复核记录可回查。
- 首次漏选的退款限制：复核判定相关或不确定时完整保留；放不下则失败，不截断。
- 校验缺失/伪造/重复 ID、关闭调用额度、输入窗口与停止信号。
- 工具摘要和 LLM 兜底采用相同策略，已生效投影保持冻结。

修改前：`pytest -q tests/integration/test_compression_layers.py tests/unit/test_compaction.py`，17 passed。新增回归先运行确认失败，再实现。所有模型测试使用脚本响应，不发送真实 API 请求；它们验证运行时机制，不验证自然语言复核质量。

## 发布范围

本次压缩依赖此前尚未提交的 M01/C01 实现，发布时连同必要依赖、测试和说明提交。其他会话的提示词与 Skill 改动不纳入本次提交。不得修改提交标签或重写历史。

## 验证结果

1. 修改前既有压缩回归 17 passed；首次新增的 8 个行为用例全部失败，分别暴露来源标记缺失、规则补充不能复核及相关的预算/ID检查缺失。实现后通过；自审再补审计依据用例，先观察到失败，再补齐首次选择标记。
2. 最终定向命令 `python -X utf8 -m pytest -q tests/integration/test_selection_provenance.py tests/integration/test_compression_layers.py tests/unit/test_compaction.py`：31 passed，其中本次新文件 14 项。
3. 从 Git 暂存区导出待提交快照，显式使用其 `src`，运行 `python -X utf8 -m pytest -q`：**950 passed, 3 deselected, 1 warning in 28.66s**。3 项 live 按默认配置排除；警告为已有 Starlette/httpx 弃用提示。
4. 完整工作区另测为 993 passed、1 failed、3 deselected。失败是另一会话未提交的长系统提示词使原文补读请求达到 35,885 字节，超过 32,768 窗口；仅在临时进程中换为待提交提示词，同一用例通过。本次不改动该提示词设计；GitHub 提交快照与完整工作区的结果分别记录。
5. `python -X utf8 -m compileall -q src/min_agent`、`python -X utf8 -m pip check`、`git -c core.whitespace=cr-at-eol diff --cached --check` 均成功；pip 输出 `No broken requirements found.`。17 个暂存 Python 文件 AST 解析通过。项目未配置类型检查及独立 lint，记为 SKIP；未新增这些依赖。
6. 对待提交快照执行 `pip wheel --no-deps --no-build-isolation` 构建成功，56 个包内文件与暂存源一致。wheel SHA-256：`d189a4eadb3574e98f6be44d90bbf23bc68763bb941b6f9ccb199d4dff6777b8`。
7. `pip install --no-deps --no-index --target ...` 安装 wheel 后，在项目外用 `python -I` 运行实际 `HarnessApplication`、临时 SQLite 和脚本模型：完成两次追问，首次选择及复核计入父账本（2 次压缩调用 + 1 次主调用），第二次复用冻结投影；完整原文及复核 artifact 均存在。
8. 暂存 25 个文件：检查密钥特征、敏感路径与提交范围，未命中凭据特征，不包含 `.env`、本机数据库、其他会话验收材料或 Skill 改动。没有运行在线依赖漏洞扫描，不把本地扫描当作完整安全审计。

自审结论：上述机制与边界通过；真实模型相关性质量没有验收，保留为明确限制。缺失的全局 `code-quality.md`、`review-checklist.md` 按用户提供的 AGENTS 规则及已加载 skill 清单自审，不伪造子代理审查。

## 核心代码与取舍

`plan_excerpts` 保留两组索引，不再把合并后的集合当成统一的相关性结论：

```python
model_selected = set() if selected is None else set(selected)
rule_only = rule_matched - model_selected
```

用 C++ 类比，这是两个 `std::unordered_set<int>`；`rule_only` 是规则命中集合减去模型已选集合。每条摘录另外输出两个 bool，便于解释“为什么出现在上下文里”。

预算有压力时调用 `GroundedSelector.review`，只允许复核 `rule_only`，但输入保留完整材料及首次选择。返回的每个候选必须恰好出现一次；程序仅对明确 `drop` 的 ID 解除保护。复核没有窗口或额度时暂停，不能以默认删除代替判断。比直接按规则丢弃多一次调用，却避免将退款例外等条件机械移走；预算充足时不增加调用。

`save_selection_review` 把完整原文片段、初次选择标记和三态决定存到会话 artifact，摘要只携带 ID、哈希及删除计数。移出上下文不等于删除存储；如不确定判断是否正确，可以按引用核对。当前复核要求整份判断依据能放入一次受限请求；超大材料会暂停，尚未实现跨块语义关系复核。已冻结投影继续复用，不回写旧前缀。

## 后续修正：规则保护不能挤掉相关正文

代码讲解时，作者继续追问“为什么规则保护比模型相关性优先”，发现 C02 原有排序仍有缺口：39 字节的模型选中退款流程，加上 60 字节仅规则命中的版权声明，在 60 字节预算下、复核返回 keep 或 uncertain 时，旧实现只留下版权声明。此前用例只覆盖保护条件本身超长，没有覆盖两句分别放得下、合起来放不下的情况。

修正选择顺序：先处理首次模型选中的原句，再处理规则补充；同组仍优先保护条件，再按任务关键词和原位置排序。如果仍受保护的补充与已选正文不能共存，暂停，不通过删除正文或限制来强行适配预算。另加检查：首次模型确实选了原句，但最后一条也没放进去时，不能只发布规则补充。最后仍按原文位置排列摘录；分配预算的优先级与展示顺序分开。

这没有新增句间依赖识别，也不保证所有模型所选句子都能保留；普通候选仍可能因预算跳过。无法辨认哪些普通句子是不可缺少的核心事实，仍是抽取策略的限制。

新增 `tests/integration/test_excerpt_priority.py` 六项离线用例：keep/uncertain 与正文冲突、正文单句超长、明确 drop、预算充足恢复原文顺序，以及实际 ContextManager 不发布/冻结仅补充摘要。修改前 4 failed、2 passed，确认复现后再改代码。

修正后，四个压缩相关测试文件合计 37 passed。暂存三文件后导出独立快照，显式使用导出目录的 src，执行 `python -X utf8 -m pytest -q`：**956 passed, 3 deselected, 1 warning in 36.82s**。仍为离线机制验证；未调用真实模型，警告仍为已有 Starlette/httpx 弃用提示。

在项目外以 `python -I` 从导出快照运行实际 HarnessApplication、临时 SQLite 与脚本模型：正文和 uncertain 补充冲突时返回 partial/context_limit，未生成或冻结仅补充投影，完整原文保留，共两次离线选择/复核调用。未调整本地另一会话的提示词、Skill 或字节窗口，完整工作区与提交快照仍分开验收。

| 阶段 | 结果 | 实际证据 |
| --- | --- | --- |
| Build | PASS | `compileall -q` 检查导出快照；本次无新增包模块 |
| Type | SKIP | 项目没有配置类型检查器 |
| Lint | SKIP | 项目没有配置独立 lint |
| Test | PASS | 定向 37 passed；独立提交快照 956 passed |
| Security | PASS | 三个暂存文件的范围与密钥特征扫描；`pip check` 无依赖问题，未运行在线漏洞审计 |
| Diff | PASS | `git -c core.whitespace=cr-at-eol diff --cached --check`；仅包含本次三文件 |

自审结论 READY：反例已复现并消失，明确 drop 与预算充足的路径保持通过；语义质量和句间依赖能力仍受前述限制。核心修改在排序键最前增加 `index not in model_selected`：模型选中的编号产生 False，先于其他编号的 True 分配预算；后面仍有保护集合和失败检查，不能以优先正文为由删除可能必要的限制。
