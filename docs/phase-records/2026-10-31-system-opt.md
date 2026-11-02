# 全系统优化第一批：权威记忆与验证恢复一致性

日期：2026-10-31。用户要求继续对整个系统优化，当前助手直接实现。沿用此前50次/$1真实调用授权，不重置已用46请求槽位/31实际请求/$0.023240上界；本批先离线。

## 基线与目标

全仓离线基线实际为1420 passed / 6 failed / 2 skipped / 3 live deselected，exit 1，137.05秒。6失败分别是两项隔离解释器无法import未安装包，一项缺trafilatura，三项缺ddgs导致搜索模拟未进入transport。D:\python.exe的依赖版本/安装状态与项目锁文件不完全相同，详见reports/system-opt-20261031/environment.json。

源码审查覆盖Application/Web、执行/预算、Memory发送、Research、验证器与夜间/进化调用方。新增跨组件固定反例为8失败/2正常对照通过：另一存储实例撤回记忆后旧缓存仍返回并通过before_send、存储不可用仍读暖缓存；夜间恢复忽略任务/协议/safety不匹配，首次生产完成路径也只信adapter的PASS。

本批集中修复两处：Memory共享文档从持久权威状态读取，存储失败不退旧缓存；夜间首次完成和恢复使用同一任务/协议/安全/文件hash核对。优先处理会使记忆撤回或“已验证完成”失真的底线问题。工具分类、通用框架重构、提示词质量优化及真实生产启用不在本批。

采用最小方案：SQLite可选读取区分“不存在”和“读取失败”；SessionStore在owner文档读写前核对当前会话版本并读持久文档；Night在可信文件上核对当前契约。保留原接口、原失败和测试判据，不用全量通过数抵消新反例。

## 验收与证据

同一新增反例必须修后通过，正常Memory写入/检索/删除/用户隔离、B0/B1/B2、夜间调度/预算/清理及进化保持。缺环境依赖在隔离validation-env补齐，环境变化与源码变化分别记录；不修改旧测试来掩盖安装失败，不在全局Python安装或升级。

冻结快照215文件在reports/system-opt-20261031/baseline-source；baseline-full.log/command及新增test_system_boundaries.py、boundaries-before.log/command保留原失败。回滚只用本批差异补丁，不能整文件git restore用户已有未提交工作。完成后追加实际结果、限制及下一步。

## 阶段交付：做了什么、为什么

生产只改3个文件，新增1个集成回归文件；其余212个基线源码/测试/配置文件hash一致。现有工具分类、模型参数、提示词、开发/验收原标准和真实用户库未改。

1. storage.py新增load_optional：区分“权威记录不存在”和“权威存储读取失败”，后者传播异常，不降级为空文档。
2. sessions.py的owner_document在持久化模式下先核对当前会话版本，再从SQLite读取同owner文档。读、写和幂等判断均基于当前持久版本；避免旧缓存继续返回已删除内容或覆写另一Store新主题。内存模式和原用户隔离保持。
3. evaluation/night.py增加共享verified_file门，首次生产完成和重启恢复都核对文件hash、task/Observation身份、固定TaskSpec、非空且当前协议、safety与传输状态。还用可信Verifier重算观察结果并核对逐条checks/诚实性/完成状态；改PASS标签不能掩盖预算失败。已记录FAIL/不可重试/Provider或预算阻塞不能在恢复时被某个通过文件覆盖；坏观察保留WAITING_FOR_USER，不崩溃或自动重试。
4. 新增tests/integration/test_system_consistency.py共16项，覆盖原反例、正常对照、共享写合并、错owner、损坏观察、标签篡改与回归失败恢复。冻结reports测试原件未改。

核心变化是把“拿旧缓存/旧标签当事实”改成权威读取和重新验证：

```python
stored = self._storage.load_optional(ExecutionContext(owner_id=context.owner_id, session_id=identity))
current = stored.snapshot if stored is not None else None
# 夜间验收另由可信层重算，而非只检查外层PASS。
rechecked = verify(task, result.observation)
```

例：用户在另一个合法Store撤回记忆，旧阅读器的已准备视图会在发送前拒绝；主任务结果通过但保留回归已明确失败，重启不会把该工作改成COMPLETED。

## 实际效果、环境对照

| 执行 | 实际结果 | 可证明内容 |
|---|---|---|
| 原D:\python.exe，全系统旧源码 | 1420通过/6失败/2跳过/3 live排除 | 原环境安装缺口，不作为代码退化 |
| 补齐依赖的隔离环境，冻结旧源码 | 1427通过/1跳过/3 live排除，exit 0 | 六个环境失败解决；旧套件仍漏掉新反例 |
| 同一隔离环境，冻结跨组件样本，旧源码 | 8失败/2通过，exit 1 | 真实存在的缓存与验收错误放行 |
| 同一隔离环境，修复版全系统 | **1443通过/1跳过/3 live排除，exit 0** | 原场景保持，新增16项回归通过 |
| 同一冻结跨组件样本，修复版 | **10通过，exit 0** | 同输入/判据的错误被检出，正常对照保持 |

通过数增加包含新增样本，不能算成总体准确率提升或真实模型收益。140.30秒与旧版147.10秒也不能归因为速度优化，环境热度/额外测试等没有控制。

验证环境是reports/system-opt-20261031/validation-env，复用系统已有核心包、隔离补齐ddgs==9.16.0、trafilatura==2.3.1及其依赖，并可编辑安装当前项目；没有更改全局Python。它不是锁文件全部依赖的干净重装：仍使用原Pydantic/FastAPI等版本，安装日志中的精确pin不一致保留为限制。全量唯一跳过是winrt.windows.ui.notifications缺失，Windows原生通知该路径未验证；3项live明确排除。真实模型调用新增0，之前的46请求槽位/31实际生成/$0.023240累计保持。

复跑旧快照最初漏带scripts/docs测试资源，出现8项收集错误；仅补齐原本未改变的241个资源文件后重跑，保留原失败日志与追加资源manifest。两个辅助命令也曾遇到Windows stdout编码问题，修正为显式UTF-8后执行；未修改产品或放宽测试判据。原源码快照和失败日志都保留。

完整证据：[reports/system-opt-20261031](../../reports/system-opt-20261031/)。baseline-full.log是原环境；baseline-fixed-env-full-r2.log是隔离环境旧快照；baseline-fixed-env-boundaries.log是旧版8个反例；after-full.log和boundaries-after.log是最终结果；环境安装、绑定和测试命令有真实退出码；final-manifest.json保存本批源码hash与保护范围。

## 审查、限制与回滚

自审结论：本批限定范围PASS；已实际运行原反例、正常/用户隔离/错误数据及全仓回归，冻结样本与无关源码保持，补丁反向预检查通过。没有独立第二审查者，不伪称独立验收。

陈旧业务会话现在可能保守拒绝并要求重新打开；本批不实现多完整应用并发租约。Memory发出之前已提交的撤回被复核，不声称追回已经送到外部服务的内容或擦除另一进程所有RAM副本。Night直接注入的Python executor仍属于明确标记的测试适配器；生产默认后端必须过新门，不能把注入测试成绩当生产证据。协议缺失的旧结果挂起，不盲目自动重跑。任意语义质量、真实模型收益、生产启用均未验证。

代码回滚只撤销本批3文件的局部差异和新增测试，保留先前未提交成果和所有报告。已执行只读预检查：

```powershell
git -c core.autocrlf=false apply --reverse --check reports/system-opt-20261031/implementation.patch
```

需要回滚时确认后续改动无冲突后再去掉--check；本轮未实际删除/回滚文件。验证环境单独保留，不改变原启动器或运行中服务，不将其当作已部署的正式环境。

阶段收尾。下一步从真实使用中选一个可重放失败继续；若扩展长期自主进化的修改范围，先补对应质量/安全评测，不将本次代码安全回归等同于自然语言效果进步。
