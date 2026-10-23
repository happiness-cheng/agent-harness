# C01：按用户三层策略重做上下文压缩

用户已明确要求重新实现。顺序固定为确定性处理→逐轮结构化记录和状态表→LLM兜底，不能把保存归档冒充已经压缩工作上下文。

后续修正见 [C02](C02-selection-provenance.md)：模型选择与规则补充分别记录；预算有压力时复核仅规则命中的片段，明确无关才移出上下文。下文验证记录对应 C01 当时版本，“规则保护”不再表示所有命中都永久保留。

## 接口与取舍

1. 全文分段、删除明确的格式噪声和重复内容；原文保留。重点摘要采用任务相关的完整原句抽取，每个摘录有范围和哈希，不再截取正文开头。可用独立、无工具的模型调用选择原句；共享原预算，分块覆盖全部材料。模型只能选已提供的原句ID，不能改写否定和条件。
2. 超窗时首先构造独立episode时间线及当前业务状态。完整交互边界和工具配对不拆分，旧消息存档，视图保留近期消息；若足够，不调用压缩模型。
3. 仍超窗，先压现有摘要，再LLM选择较旧历史的重要原句并合成结构化摘要。校验引用和关键条件；失败不换可见版本，同源两次失败熔断。超大输入分块，不先截断。无法在预算内保留关键条件则暂停，不冒充成功。

原句抽取牺牲自由改写的压缩率，换取可机械核对的语义保留；仍不能保证任务相关性判断完美。只删除明确噪声，不能可靠判断的材料保留原文及引用。当前采用字节估算，不引入tokenizer或新依赖。

## 执行

- [x] 测试全文末尾否定/条件、引用、摘要冻结和分块覆盖；实现工具输出摘要。
- [x] 测试结构化层在LLM之前生效、最新状态表和工具配对；接入实际prepare路径。
- [x] 测试LLM伪造引用/改写拒绝、预算/停止/熔断；实现有依据的兜底。
- [x] 清除研究上下文中的另一处机械截头；实际应用测试验证子调用计入父预算。
- [x] 完整回归、构建和独立安装验证；仅填写实际结果。

不修改凭据、工作库schema，不新增真实外部调用，不自动提交。主代理实施与审查，不委派；已有M01本地改动完整保留。

## 核心代码导读

入口是 `ContextManager.prepare`。先构造工具投影并冻结，再检查总窗口。窗口不足，尝试 `structured_history`；只有它仍放不下，才调用 `Compactor`。原始消息和可见投影是两个对象，候选没有通过检查时不覆盖已生效的摘要。

这段控制流先试结构化压缩。像先整理每次会议的独立纪要，再决定是否请人重写长篇总结。实际代码位于 `src/min_agent/context.py:88`，以下保留本次版本的行号：

```text
88: structured_end = end if end is not None else state["covered"]
89: if structured_end:
90:     try:
91:         candidate = deepcopy(state)
92:         candidate.update(summary=structured_history(self.sessions, self.context,
93:             messages[prefix:], structured_end, objective), covered=structured_end, covered_hashes=hashes[:structured_end],
94:             version=state["version"] + 1, stage="structured")
95:         structured = self._visible(stable, core, candidate)
96:         if self._fits(structured, tools):
97:             self.sessions.set_extension(self.context, "context", candidate)
98:             return structured
99:     except ValueError:
100:        pass
101: if self.compactor is None: raise ContextLimit("结构化层仍放不下，需要LLM兜底或分段")
```

`compression_text.text_units` 扫描全文，而非正文前缀；`choose_excerpts` 按当前目标选完整句子，并优先保护规则可识别的否定、条件、日期等。确定性去噪只删除明确标签和完全重复内容，不能把所有低相关性材料都当作可安全删除的噪声。大工具的运行时结果先解码 `result`，避免转义换行妨碍正文分段；`ok` 和错误执行状态独立保留。封装和解码正文分别持久化，摘录的位置指向正文，不冒充封装字节偏移。

`GroundedSelector.select` 是独立上下文的模型调用：无工具、不读私人会话、只选本次输入中的ID。全部分块用完整请求预检窗口，再预检父预算是否足以覆盖所有块并保留收尾调用；中途失败不发布“部分读过”的摘要。它不是另一个可以自主执行任务的Agent。

`structured_history` 为每轮留下独立归档和事件；工具事件保留请求参数、执行状态与原文引用，用户和助手文字采用带引用的摘录。`ContextManager._visible` 在历史摘要和近期消息之后追加从真实业务对象读取的当前状态。摘要冻结、当前状态更新，两者职责不同。

`Compactor._summarize` 将模型选择的ID映射回原句，生成分类和引用。模型不能返回“删除原文”来替换“不要删除原文”，也不能自己写一个不存在的来源字符串。已有摘要再次压缩时，先校验并沿用原始材料位置，仍标记为 `unverified_summary`，不能升级为已核实事实。逐字引用只证明这句话出现过，不证明它真实或在脱离语境后仍适用。

用C++类比：原文是不可变的 `const` 数据；压缩结果类似带索引的只读视图；状态表是从业务对象生成的当前快照。通过校验才提交新的上下文版本，类似构造临时对象、检查不变量后再交换。持久化借助既有会话存储事务，未新增数据库schema。

## 取舍与边界

- 采用抽取而非自由改写：更容易逐项校验否定、条件和来源，但摘要可能更长，无法保证句间关系全部保留。
- 常规超窗先走确定性结构化层，避免每次都请求模型；大工具重点选择和最后兜底仍有模型费用，全部计入原账本。
- 状态表只覆盖程序能核实的任务、待办、提醒和动作状态；不从聊天文字自动建立“用户事实库”。
- 当前总窗口采用字节预算；过长的不可分原句、保护信息过多、原文存储额度不足或模型额度不足都会安全暂停。没有把超大材料的任意语义压缩率当作已解决问题。
- 保留旧冻结投影，不在已有上下文版本中强行重写；本次新生成的投影使用新策略。归档和原文只追加。
- `read_artifact` 的显式原文分页与记忆原文分页一样，不再次做摘要；模型可以核对真实措辞，总窗口不足则暂停或压缩更旧交互。
- 不依赖特定供应商的服务端上下文编辑；本次没有新增API微压缩能力，也没有验证真实LLM的摘要质量。

## 验证记录（2026-10-21）

先以测试复现：尾部条件被截掉、结构化层未参与自动压缩、模型改写否定和伪造来源仍被接受。修正后另测全部分块覆盖、父预算与收尾预留、同源失败熔断、最新状态、工具配对、实际应用接线、JSON封装解码与错误状态、二次压缩仍回指原文及坏引用在模型调用前拒绝。

- 首轮 `.venv/Scripts/python.exe -X utf8 -m pytest -q`：**935 passed, 3 deselected, 1 warning in 50.31s**。后续自审新增“显式原文分页不能再次摘要”回归，最终结果另列。3项live按默认配置排除，未新增真实模型请求。警告为既有Starlette/httpx弃用提示。
- `.venv/Scripts/python.exe -X utf8 -m compileall -q src/min_agent`：退出0。
- `.venv/Scripts/python.exe -X utf8 -m pip check`：`No broken requirements found.`
- `git -c core.whitespace=cr-at-eol diff --check`：退出0；Git仅提示现有Windows换行转换策略。
- 静态类型检查、独立lint：项目未配置对应工具，本次不擅自新增依赖，记为SKIP。

- 最终完整回归：**936 passed, 3 deselected, 1 warning in 47.40s**，包含新增的原文分页回归。C01共13项新集成用例。
- `pip wheel . --no-deps --no-build-isolation --disable-pip-version-check --wheel-dir dist/c01-compression-final`：构建成功。最终wheel为 `dist/c01-compression-final/agent_harness-0.1.0-py3-none-any.whl`，SHA-256为 `1a3f5c410240c8acc60d9beb027e4b9c927381cb7b768280e460ecce9b310b2a`；56个包内文件逐字节与源码一致。
- `pip install --no-deps --no-index --target build/c01-final-installed <最终wheel>`：成功。在项目目录外通过 `python -I` 从该目录加载包，实际运行全文摘要、引用检查、冻结复用及显式分页回查；均通过，父账本模型调用1次，第二次准备和原文回查不增加调用。
- 另以临时SQLite应用和脚本模型演示：第一次追问共计主模型1次、摘要选择1次；第二次追问复用摘要，摘要选择累计仍为1次。模型看到了正文末尾“退款仅在购买后7天内且未使用时允许”，原始封装和正文均保留；没有外部请求或工作库写入。
- 变更文件有限模式扫描：23个文件、16个Python文件AST解析通过，密钥特征/动态eval或exec/凭据文件及数据库改动均未检出。未运行外部漏洞数据库扫描，不将此视为完整安全审计。
- 主代理自审覆盖三层顺序、原文/投影分离、来源与否定、预算、工具配对和失败保留。自审发现的JSON封装、二次压缩来源及显式原文回查问题均补了反例回归。

以上离线测试不能代替真实模型的相关性和压缩质量评估。未改凭据、数据库schema、已批准聊天原件或Git历史；未提交或推送。此前M01构建和本次首轮C01构建只作为历史产物保留，当前代码对应上述final包。
