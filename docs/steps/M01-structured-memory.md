# M01：逐轮结构化记录、原文引用与当前状态表

日期：2026-10-21。用户在代码导读中要求实现；范围已明确选择“先完成可核实的任务与工具状态”。

## 目标与边界

完成用户第二层策略的可核实部分：独立交互记录保存历史，原文引用支持核对，当前状态表读取真实业务对象。自然语言事实、纠正意图与未解决问题不自动提取；助手文字保留未核实标记。

不新增模型调用、依赖、数据库表或迁移。原始消息不覆盖；现有逐轮归档保持兼容。源码在当前会话内执行和自审，不委派、不自动提交。

## 选择与取舍

- 采用确定性的结构化事件和只读状态视图：模型成本为零，结果来自可追溯消息及当前业务状态，适合本次范围。
- 未采用每轮LLM提取：会增加成本，且格式合法不等于语义忠实；用户已选择先做可核实状态。
- 未采用可重放的全量事件溯源框架：现有事务业务库已是权威状态，重复建设会扩大改动。历史记录不参与执行授权。

## 数据与接口

- 原文引用：归档ID、当前会话ID、全局消息位置、消息哈希及字符范围。读取时验证归属、归档完整性、消息哈希和范围；最多返回8000字符。
- 逐轮事件：用户输入、未核实助手文字、工具请求、工具结果，绑定原文引用；不把工具返回成功等同整个业务目标完成。
- 归档保存当时的执行/目标/授权快照。当前状态表从SessionStore和同一TodoService读取，不从摘要重建，不提供写入口。
- `MemoryJournal.read_source(reference, offset=0, limit=4000)`：受控原文读取。
- `SessionStateTable.read()`：当前目标与约束、执行状态、待办、动作消费的只读JSON视图。
- 主Agent工具：现有`recall_memory`返回结构化条目；新增`read_memory_source`和`read_session_state`分别补读来源、核对当前状态。

## 执行与验收

- [x] 步骤1：先写归档事件、引用读取及篡改/越界/跨会话拒绝测试，观察RED；再实现memory_records.py与memory.py，运行GREEN。
- [x] 步骤2：先写当前状态表的日期修改、删除、隔离、错误绑定测试，观察RED；再实现memory_state.py，运行GREEN。
- [x] 步骤3：先写实际应用工具接线测试，观察RED；再修改application.py与系统提示词，运行GREEN。
- [x] 步骤4：回归既有归档/上下文/授权路径，执行完整离线测试、构建、语法、依赖及diff检查；更新本页实际证据与核心代码讲解。

核心行为测试入口：`.venv/Scripts/python.exe -X utf8 -m pytest tests/integration/test_structured_memory.py -q`。完整回归：`.venv/Scripts/python.exe -X utf8 -m pytest tests -q`。仅临时数据库与脚本模型；不以离线通过宣称真实模型语义验收。

主代理方案自审：不修改业务权限或schema；状态表复用业务权威源；对不同Session拒绝访问；输入/助手文本不获授权效力；旧归档缺新增字段可继续读取；测试覆盖负例而不只验证字段存在。

## 实际执行记录

- 事件与引用：12项RED（缺events）；实现后连同既有归档14项GREEN。
- 状态表：RED缺memory_state模块；实现后合并18项GREEN，覆盖修改日期后的当前值、已消费动作、同用户不同会话、删除、返回副本修改与错误服务绑定。
- 工具接线：修正测试模型准备方式后，实际RED为未知工具导致partial；接入后19项GREEN。最初测试错误使用不存在的ScriptedModel.responses字段，未把该准备错误当作功能RED。
- 新增负例复现显式原文读取仍遭头部截断；ContextManager对三种记忆读取保存完整投影后GREEN，已有冻结预览不改写，窗口硬门保留。
- 自审增加构造绕过校验、归档原文被改、旧归档无events三个边界；绕过校验和旧归档各先复现RED，再修正为重新校验输入及只在读取副本补引用。旧归档不猜测当时的控制状态，不回写历史。
- 最后定向回归：29 passed in1.50s。最终完整回归：923 passed, 3 deselected, 1 warning in32.27s。3项live测试未运行，warning为既有Starlette/httpx弃用提示；没有新增真实模型调用。

## 最终验证与交付边界

| 检查 | 实际结果 |
| --- | --- |
| `.venv/Scripts/python.exe -X utf8 -m pytest tests -q` | 923 passed / 3 deselected / 1 warning，32.27s |
| `python -m compileall -q src/min_agent`、`python -m pip check` | 语法通过；No broken requirements found |
| `python -m pip wheel . --no-deps --no-build-isolation --wheel-dir dist/m01-structured-memory` | 构建通过，53个包资源与当前源码逐字节一致 |
| 独立目标目录安装wheel，再从项目外用`python -I`导入运行 | 结构化归档、引用读取和当前状态查询通过；确认导入路径来自安装包 |
| 类型与Lint | SKIP，项目没有配置对应检查器；未擅自引入依赖 |
| 安全与范围 | 13个变更/新增文件扫描无凭据模式、无新增eval/exec；主代理按代码审查清单自审，未委派 |
| 差异与空白 | 按既有CRLF约定用`git -c core.whitespace=cr-at-eol diff --check`；额外检查真实行尾空格为0 |

最终wheel：`dist/m01-structured-memory/agent_harness-0.1.0-py3-none-any.whl`，SHA-256：`9ba121eec91476670d2897607a0c325afb74e7dbb45a7110e224df17f73fc71b`。

另做独立临时应用演示：真实TodoService先新增22日待办、再按新确认改为23日；两个交互归档独立，旧source仍返回22日原要求，当前状态返回23日，动作凭据为2条。使用脚本模型4次，外部请求为0；不是模型意图或真实API验收。现有工作数据库与服务进程未用于演示。代码本地修改，未提交或推送GitHub；首次完整验收由主代理执行，不伪造其他审查者结果。

## 核心代码导读

### 1. 历史：记录发生过什么

`memory.py`的`record_completed_interaction`继续与completed终态同事务保存。新增`events`来自`memory_records.interaction_events`：用户原话、助手未核实文字、工具请求、工具结果各占独立事件。工具请求保存真实参数，结果保留配对call ID、工具名及返回状态；不凭模型回答确认写入。

每条事件的source由程序生成，包含归档ID、会话ID、全局消息位置、消息哈希和字符范围。`read_source`先核对归属和归档哈希，再核对消息哈希、位置与分页范围。哈希的作用是发现版本不一致，不证明内容或模型结论正确。引用不含provider内部状态；工具请求原文呈现正文和参数。

事件ID由归档ID与顺序确定。同一次交互保留自己的记录，后续轮次及全量摘要不会把它覆盖。旧归档可以在读取副本上补出事件引用，原始存储保持不变。

### 2. 当前状态：现在是什么

`memory_state.py`中最重要的是这条读取路径：

```python
with self.sessions.control(self.context):
    snapshot = self.sessions.snapshot(self.context)
    # 目标、执行、待办和动作凭据在同一把锁保护下读取。
    current_todos = self.todos.list_views(self.context)
```

实际返回包括当前WorkState、TaskState、待办与提醒、已提交动作的标识/操作/资源/状态。状态表是业务数据的只读视图，不是新建SQL表，也不是从历史重放或摘要生成的第二份权威数据。这样避免“库中日期23日，记忆表仍写22日”的双写一致性问题。

C++可类比：持有同一把`std::recursive_mutex`读取业务对象，生成一个值对象返回；调用者改这个副本不会改业务对象。原始Python沿用既有RLock，未引入新的锁顺序。

### 3. 统一例子：日期被修改

用户先确认截止日期2026-10-22，随后确认修改到2026-10-23：历史分别保留两次请求和工具结果；`read_session_state`读取当前待办，返回23日；追问修改经过用`recall_memory`，核对具体措辞或参数用`read_memory_source`。不让摘要自行决定应采用哪个日期，也不把历史中的确认当作新的执行许可。

### 4. 模型什么时候看到它们

事件和状态表不全量常驻提示词。模型需要历史时调用recall_memory，需要原文时补读source，需要当前业务事实时调用read_session_state；工具结果照常追加到消息末尾。结构化返回及显式分页不再被通用1200字符预览二次截头，但超出总窗口仍会暂停或进入既有压缩路径，不承诺任意规模能一次注入。

### 5. 本步没有完成的设计

本步落实第二层中可核实的记录与状态查询基础；尚未把该记录体系接成自动替换旧上下文的独立压缩层。自然语言事实/纠正/未解决问题的语义提取、通用工具任务感知摘要、通用噪声剔除及LLM全量摘要的语义/逐事实来源校验，仍是明确后续项。现有Compactor的自由字符串source_references未在本步变为强校验，不能混淆两条路径。

后续补充：用户随后明确要求按三层策略重做压缩，[C01](C01-compression.md) 已接入自动结构化层、全文任务相关摘录和由程序生成引用的LLM兜底。上段描述的是M01当时的交付范围，不代表C01后的当前实现；自然语言真实性和完整语义保留仍不能由引用校验保证。
