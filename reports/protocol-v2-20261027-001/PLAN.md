# 资料入口协议v2：实施与验收计划

用户已批准协议v2修订与离线验证；新增Provider/Codex CLI生成额度0。原NO_VERIFIED_GAIN保持。仅在本目录的baseline/candidate隔离工作树接入共同的资料挂载能力，主工程、旧六次真实运行与冻结20题不改。

## 采用方案与取舍

新增小型ResearchCollection数据对象与Scoped Reader；由真实HarnessApplication公开mount_research_collection(session_id, collection)、research_resources(session_id)。内部走既有_registry注册research，目录经真实工具Schema进入模型首请求，处理仍经原ResearchService/Runtime/ArtifactStore/共享预算。

不使用测试直接改registry._tools或替换应用对象；不把目录文本拼进正常用户问题；不扩大真实网络、权限或预算。未挂载资料的普通应用路径保持原行为。挂载只由可信调用方在Session未运行时执行，不自动授予allow_web。

公共目录只含集合类型、文档标题/定位、覆盖范围、权限和当前Session已有Artifact的引用元数据，无正文/Gold/目标引文/评分字段。PSF定义为受控外部库中的历史公开短片段，当前Session初始没有它的Artifact；读取后产生新Artifact。合成保修/API为受控库文档。单独Session附加对照使用真实ArtifactStore.save，不宣称外部文档预先在Session存在。已知缺失对照的目录为空且受控库不回退外网。

## 先固定评价，再实现

- 正常3题：用户问题无工具顺序；模型首轮Schema真实可见目录、无答案泄漏、research Schema与网络开关一致。
- 正常参考路径：固定本地策略仅从可见目录取标题/定位，真实调用research、search、read，评分只读重新打开SQLite。
- 不可发现负例：不提供的内部文档应澄清；Task PASS、Reach NOT_REACHED，不得把所有任务强行路由research。
- Session附加对照：已保存资料经真实read_artifact读取；Task PASS、Reach NOT_REACHED。
- 触达但失败对照：已search、工具剩1、模型余量大于1时停止而不读；Task FAIL、Reach REACHED。
- 缺预算观察或不能确认归属：Reach UNKNOWN；触达本身不等于任务成功。
- 无网络授权：挂载不放行research；原硬权限/预算仍执行。
- 同一v2目录/问题/资源/参数和判据用于两版；研究closing差异仍是<=1与==0。新Task ID/契约hash；正常与工具提示诊断协议分表。

## 输出语义

Task Outcome = PASS/FAIL/UNKNOWN。
Hypothesis Reachability = REACHED/NOT_REACHED/UNKNOWN；依据派发前真实BudgetSnapshot、成功search、尚未读取和工具Schema，不从总调用数猜测。
Repair Outcome本轮为INCONCLUSIVE：零生成参考路径可验证工程机制，但不验证真实模型改善。旧真实实验的NO_VERIFIED_GAIN不改写。

## 步骤

- [ ] 固定v2数据、正常/诊断问题、评分契约与已知缺失场景。
- [ ] 公共挂载API回归先RED；实现research_resources.py及application.py的最小共同接线。
- [ ] 用真实Session/工具注册表/Runtime保存首请求与资源读取证据，记录实际预算触达。
- [ ] 两版运行正常参考路径、诊断、Session附加、缺失及触达不成功对照。
- [ ] 独立代码/协议审查，定向与完整回归、旧20题、源码与旧证据hash保护。
- [ ] 交付协议正文、输入diff、预检报告、触达分析与待授权先导预算；到此停止。
