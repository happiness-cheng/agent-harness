# C05 完整材料：补齐任务判断的上下文输入

日期：2026-10-24。本轮只修任务材料接口，不修改压缩算法、Skill选择策略、主系统提示或工具。

## 问题与冻结标准

旧摘要覆盖到A，但A之后新增100轮时，接口只向下游提供最后两个用户交互范围。中间98轮既不在旧摘要里，也不在本次判断依据里。原会话历史仍保留；这里的问题是消费接口漏传材料。

先固定[4项套件](../acceptance/c05-task-input-suite-v1.json)、[公共入口回归](../../tests/integration/test_task_input_coverage.py)和[探针](../../scripts/audit_task_input.py)，再运行修改前基线，最后修改生产代码。U01用于开发，U02—U04为公开固定验收，均非盲测。

## 实际生产代码差异

```diff
 def _prepare_skill_retention(self, core, state):
     candidates = [snapshot for _, snapshot in latest_skill_snapshots(core)]
     if not candidates or self.skill_selector is None:
         return state
-    user_positions = [index for index, message in enumerate(core) if message.role == "user"]
-    start = user_positions[-2] if len(user_positions) >= 2 else 0
-    task_context = {"previous_memory": state["summary"], "recent_messages": [
+    # 旧摘要只覆盖 covered 以前的记录，其后的任务依据必须完整提供。
+    start = state["covered"]
+    task_context = {"previous_memory": state["summary"], "uncompressed_messages": [
         message.model_dump(mode="json", exclude={"provider_state"})
         for message in skill_history_projection(core[start:])]}
```

`covered`是已由旧摘要覆盖的消息条数，`core[start:]`取该位置之后的全部消息。它不是轮数：一轮可以包含多条消息。`previous_memory`仍原样提供；新字段名明确表示尚未被旧摘要覆盖的记录。

例如A点覆盖了前4条，后面有199条消息，起点就是4，传入其后全部199条，而非从倒数第二条用户消息开始。B点覆盖到204条时，下一次从204开始，不重复传入已由B摘要覆盖的原消息。

Skill正文仍通过既有独立候选提供，历史中的读取结果沿用短记录投影；原历史不改。`provider_state`仍排除。辅助调用原有提示、无工具限制、窗口预检、预算及保守回退均未修改。

## 同一组输入的前后结果

每段100个用户回合，前99个各有一条助手回复，共199条消息。第50轮放入明确约束，确保不能只靠最后几条蒙混通过。

| 场景 | 应传消息 | 修改前 | 修改后 | 中间第50轮约束 |
|---|---:|---:|---:|---|
| U01：A摘要后新增100轮 | 199 | 3 | 199 | 缺失→存在 |
| U02：B摘要后再新增100轮 | 199 | 3 | 199 | 缺失→存在 |
| U03：从未压缩 | 203 | 3 | 203 | 缺失→存在 |
| U04：完整判断依据超窗 | 199 | 3 | 199 | 缺失→存在 |

U03额外4条是最初的交互与Skill读取。回归逐条检查普通消息内容及顺序、总体消息数量、旧摘要一致、原历史不改；不是仅检查某个关键词。

U04中，修改前错误地把3条短材料视为足够，调用了离线辅助模型；替身返回停用，Skill正文在主输入中消失。修改后先把199条材料交给真实选择器，其既有预检发现超窗，辅助模型调用0次、辅助预算消耗0次、fallback为true，原Skill正文保留1份。这个替身输出刻意用于暴露机制风险，不代表真实模型一定会做出相同停用决定。

本轮没有自然语言回答前后对比：主模型替身统一返回“固定离线答复”，辅助替身返回固定分区；测试对象是输入接口，不能把替身回答当真实模型效果。

## 完整输入与输出

- [修改前报告，0/4](../acceptance/c05-task-input-before-20261024.json)
- [主目录修改后报告，4/4](../acceptance/c05-task-input-after-20261024.json)
- [独立设计分支报告，4/4](../acceptance/c05-task-input-public-20261024.json)

每份报告的每个record保留：`input_messages`原始完整输入、`consumer_input`实际任务材料和候选、`auxiliary_requests`实际辅助请求、`main_input`实际主模型输入、预期与观测值、covered位置及数量。三个报告使用相同冻结套件哈希，不覆盖旧失败。

为了单独测完整输入契约，U01—U03辅助窗口设200000，U04设11000，主上下文窗口为16000；这不是生产推荐配置。实际应用使用配置窗口，较长材料可能更频繁走保守回退。

普通摘要器使用离线占位替身以隔离接口，不测试摘要语义。主目录与公开设计分支底层压缩版本不同，分别运行；本补丁仅同步同一方法中的三行差异。实际多次历史压缩连续性已由独立的[C04](https://github.com/happiness-cheng/agent-harness/blob/e760f328fbfc902b9d9e7ae034ef4b23f65a5d47/docs/steps/C04-readable.md)回归验证，不把那部分算法复制到旧设计分支。

## 实际验证

主目录命令，使用项目`.venv/Scripts/python.exe -X utf8`：

```powershell
python -m pytest tests/integration/test_task_input_coverage.py -q --tb=short
python scripts/audit_task_input.py --suite docs/acceptance/c05-task-input-suite-v1.json --report docs/acceptance/c05-task-input-after-20261024.json
python -m pytest tests/unit/test_compaction.py tests/unit/test_context_lifecycle.py tests/integration/test_multi_compaction_regression.py tests/unit/test_skill_compaction.py tests/unit/test_skill_context.py tests/integration/test_task_input_coverage.py -q --tb=short
python -m pytest tests/unit/test_compression_summary.py tests/integration/test_compression_layers.py tests/integration/test_semantic_compression.py -q --tb=short
```

结果：修改前新4项全失败；修改后新4项通过；相关回归分两组69 passed in 33.97s、35 passed in 2.57s，合计104项。独立设计分支明确设置`PYTHONPATH`为自身src并打印实际context.py路径，运行全量`python -m pytest -q --tb=short`：**994 passed, 3 deselected, 1 warning in 34.63s**。警告为既有Starlette/httpx弃用提示，3项live排除。

主目录没有运行无关全量套件，不声称其全量通过；此前已知的记忆读取失败不属于本轮修复范围。

交付另执行三份Python文件的py_compile、git diff --check、定向敏感特征扫描和链接核对。Type/Lint没有项目配置，SKIP；不涉及依赖变更，依赖漏洞审计和重新构建安装包SKIP。没有外部模型请求、凭据读取或工作数据库操作。

材料链接检查首次发现C04相对路径在旧设计分支不存在，已改为固定提交链接，并用本地Git对象验证目标存在；复验通过。

| 阶段 | 结果 | 证据 |
|---|---|---|
| Build | PASS | 三份Python文件py_compile；安装包构建SKIP |
| Type | SKIP | 项目未配置类型检查器 |
| Lint | SKIP | 项目未配置lint |
| Test | PASS | 主目录相关104；独立分支全量994 |
| Security | PASS | 本轮文件定向密钥特征扫描，无匹配；不替代完整安全审计 |
| Diff | PASS | git diff --check，限定10文件，材料链接复验 |

本轮限定范围可交付；未宣称跳过项通过。

## 取舍与审查

完整依据需要更多输入预算；放不下时保守保留，可能暂时无法移除不适用Skill。这里没有通过偷偷截取材料来提高表面上的调用成功率。未来若要增加专用任务摘要，需要另定标准和评测，本轮没有添加。

主代理自审，无子代理：确认covered与core使用同一消息索引；旧摘要和未覆盖区间互补；未改其他源码方法；窗口/预算检查仍在模型调用前；异常保守回退；字段名没有其他硬编码生产消费者。离线通过只证明接线和机制，不证明模型相关性判断、语义忠实度或所有长会话场景都正确。
