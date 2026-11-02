# Adrian 八系统最小闭环：可信Research观察与撤权复核

日期：2026-10-30，Asia/Shanghai。工作区已有未提交变更；本记录仅描述本轮追加的B2修复，不能代表整个工作区从HEAD重建。

## 发生了什么、目标与取舍

Adrian的现有主线已经具备意图/Plan与Build、长期Memory、Skills和工具、任务/Checkpoint、B0授权与预算、Trace、评测等第一版基础，但Research观察和网络许可仍会信任本地会话缓存。已登记反例证明，另一合法Store删除Artifact或提交撤权后，旧实例仍可能给出VERIFIED或进入网络后端。

目标是先闭合八系统共同依赖的最小可信链路：研究事实来源可查；每个网络工具调用都检查最新许可；未知/陈旧状态失败关闭。暂不扩展多目标评分、模型端证据送达、语义真伪、用户界面、工具分类或真实模型效果。这样做对应项目P0当前硬边界；不是声称八个完整系统已经全部实现。

设计参考李博杰《深入理解 AI Agent》：[第1章 Agent基础与Harness](https://bojieli.github.io/ai-agent-book/book/chapter1/)强调把模型决策、上下文和工具放在可治理的运行外壳内；[第2章上下文工程](https://bojieli.github.io/ai-agent-book/book/chapter2/)、[第3章用户记忆](https://bojieli.github.io/ai-agent-book/book/chapter3/)、[第4章工具](https://bojieli.github.io/ai-agent-book/book/chapter4/)、[第7章评估](https://bojieli.github.io/ai-agent-book/book/chapter7/)、[第9章持续进化](https://bojieli.github.io/ai-agent-book/book/chapter9/)和[第10章多Agent协作](https://bojieli.github.io/ai-agent-book/book/chapter10/)对应八系统中的相邻职责。此次只使用与当前缺口相符的Harness/权限/验证原则，没有接入书中或其他框架。

## 改了什么及原因

- `sessions.py`：新增`authoritative_snapshot()`，从SQLite读取当前持久快照，并要求本地revision一致；权威读取失败或revision过期时失败关闭。`assert_current()`复用此路径。
- `application.py`：加入默认关闭的`research_observation_enabled`和host只读查询入口。Research每次search/read前重新检查持久网络许可、父任务、预算；观察查询遇到错owner/session只回`WITHHELD`。
- `research.py`：给研究循环增加可选许可检查和事实采集回调。回调在网络Search和网页Reader进入前执行；成功读取后保存原文ID、hash及本轮实际返回区间，最终引用必须位于这些区间才可机械验证。
- 新增`research_observation.py`：最多保留每会话20条run的元数据；查询从同一权威快照核对run范围和Artifact内容hash，不返回原文正文，也不签发执行授权。
- 新增`tests/b2_research_observation/`：迁入原有19项主测试及A边界探针，覆盖部分证据、重启读取、错误owner、陈旧Artifact、新父任务、权威存储失败、跨Store撤权和真实WebReader调用入口。

核心检查的顺序是：

```python
latest = self.sessions.authoritative_snapshot(context)
if not latest.extensions.get("allow_public_web", False):
    raise AuthorizationRequired("network_not_authorized")
```

这段逻辑在每个Research网络工具调用前运行。当前实例比持久数据陈旧时会拒绝继续；用户需重新打开会话，不会自动刷新旧授权缓存。

## 效果与验证

- 新增B2集成/边界回归：**20 passed, 1 skipped**。跳过的生产WebReader正常读取链路依赖当前环境未安装的`trafilatura`；没有安装依赖或发送真实网络请求。
- B0/B1、Research原有相关回归合并执行：**131 passed, 1 skipped**，同一条依赖跳过。
- 修复前后同一组Research/Web/Auth基线：均为**30 passed, 4 failed**。四项失败在修复前已存在：一项因缺少`trafilatura`，三项因搜索模拟请求没有进入transport/得到`search_failed`；本轮未修改这些判据或搜索代码。
- 验证边界：这证明合成模型/网络下的机制集成，不证明真实模型准确率、真实网络安全覆盖、成本收益或整个仓库全量回归。观察能力默认关闭；未启用真实数据或付费调用。

## 八系统当前落点与本轮剩余

| 系统 | 当前已有基础 | 本轮结果/未证明内容 |
|---|---|---|
| 理解与规划 | 系统提示、目标提案、Plan/Build、steer/stop | 未重测任意复杂自然语言规划质量 |
| 上下文与长期记忆 | 上下文管理、跨会话owner隔离Memory、读取/删除接口 | 未证明真实任务召回收益；Memory评测与本轮分开 |
| 工具与Skills | 类型化注册表、Todo/Reminder/Research、按需读取Skill | 工具分类/风险元数据依旧延期 |
| 执行与任务状态 | Runtime、预算片段、Checkpoint、resume、持久Task | 不等于通用多日任务调度或远端副作用恢复 |
| 权限、安全与预算 | B0 CAS/授权绑定；本轮网络子调用权威许可复核 | 未提供通用美元/Token/deadline总账 |
| 可信观察与验证 | Todo观察；本轮Research原文hash/区间/引用机械验证 | 不判断Research结论语义正确，也不证明父模型已收到原文 |
| 失败诊断与纠错 | 状态码、Trace、UNKNOWN保留、有限错误反馈 | 没有自动诊断后执行补救；UNKNOWN不盲目重试 |
| 评估与持续改进 | 冻结用例、B0/B1/B2边界测试、整改/阶段记录 | 没有自动改写Prompt/代码或自动发布 |

达到的是现有八系统基础上的一个最小集成修复，不给整体成熟度打分，也不把长期未完成项包装成此次交付。

## 回滚方式

这些目标文件在本轮开始前已有用户未提交改动。**不要对整文件运行`git restore`或checkout。** 如需撤销本轮修复，只反向删除以下明确新增代码：

1. `application.py`：构造参数`research_observation_enabled`、观察Store初始化/查询方法，以及Research函数中的`authoritative_snapshot`许可检查、collector和两个可选回调参数。
2. `research.py`：`run()`新加的`permission_check`/`observation`参数、search/read前置检查、`note_read`/`note_citation`和结束采集钩子。
3. `sessions.py`：新增的`authoritative_snapshot()`；将`assert_current()`恢复为本轮前实现。
4. 删除本轮新增的`src/min_agent/research_observation.py`与`tests/b2_research_observation/`。

回滚后重新运行本阶段131项命令，并检查`git diff`，确保本轮之前的其他未提交改动仍在。此阶段没有commit、push、发布或服务重启。

## 下次启动条件

如果要把Research观察作为可启用能力继续交付，先在具备项目锁定依赖的环境中跑过跳过的WebReader正常读取测试，再由独立复核者审查本轮补丁和授权前置边界；之后再讨论是否进入P1（证据送达/反馈）。不需要为本轮范围重跑付费模型或真实网络实验。
