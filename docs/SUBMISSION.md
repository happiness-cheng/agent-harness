# 笔试提交说明

提交范围：自写最小 Agent、五个模块各一道架构题、运行与设计说明、AI Prompt 和问题解决记录。提交后再逐步扩展为个人 Agent，不将未来设计计入当前功能。

入口：[README](../README.md) · [架构答案](ARCHITECTURE_REVISIONS.md) · [AI 可见对话](ai-conversations/README.md) · [Prompt](AI_PROMPT_LOG.md) · [问题解决](PROBLEM_SOLVING.md)。

## Vibe coding 要求对照

| 笔试要求 | 实现及证据 |
| --- | --- |
| 核心 Runtime 自行实现 | [runtime.py](../src/min_agent/runtime.py)，不使用 Agent 框架驱动循环 |
| 接收输入、直接回答或自主调用工具、继续决策 | Runtime + 模型适配器；`test_runtime_reply.py`、`test_runtime_tool_loop.py`、`test_application_flow.py` |
| 至少三个工具，todo 必须实现 | calculator、todo CRUD；research 子循环注册 search / read_webpage；另有提醒。入口见 [工具注册](../src/min_agent/tools/registry.py)、[待办](../src/min_agent/tools/todo_tools.py)、[研究](../src/min_agent/research.py) |
| 名称、描述、参数 Schema | ToolDefinition + Pydantic，模型按 Schema 生成调用；严格校验、已确认动作与业务提交分开 |
| 解析工具调用与最终答案 | [协议适配](../src/min_agent/llm/openai_protocol.py) 解析类型化输出；仅保留 API 实际提供的必要推理协议状态，不要求或伪造隐藏思维链 |
| Session 隔离及持久化 | 可信 owner/session、SQLite；独立历史/待办/提醒/记忆；隔离、重启和双窗口浏览器测试 |
| 多轮状态、纯对话及工具追问 | 持久历史与上下文投影；真实压缩后事实追问及 calculator 追问记录见 [验收](ACCEPTANCE.md) |
| 最大轮次与基本压缩 | 逻辑任务/执行片段调用预算、收尾检查点；冻结预览、会话归档、分层压缩及失败熔断 |
| 异常处理与执行日志 | 明确错误和执行状态、受限纠错、权限等待、无进展暂停；父子关联 trace 保留最近20次运行 |
| 测试用例 | 单元、集成、意图场景、端到端、两项本机 Edge 测试；真实调用测试单独受控且默认排除 |
| 真实 LLM API | 已有 DeepSeek 官方接口的有限真实结果；失败及成功分开保留。真实模型不是 DemoModel，也不以脚本模型替代真实验收 |
| README / memory 说明 / AI 记录 | README 的 Memory 与恢复章节、四份生产提示词、问题记录及两份脱敏可见聊天 |

待办支持日期、新增、查询、修改、完成及删除；过期不自动删除。截止日期与主动提醒独立。隐含意图“明天写周报，记得提醒我”纳入意图场景，写入仍需确认，未指定提醒时刻不能虚构默认时间。

## 修复的独立复核

2026-10-21，复核者重新执行完整回归与原始独立探针，使用临时数据，不操作工作库、不发送新的模型或搜索请求：

| 原发现 | 本次结论 |
| --- | --- |
| F01 会话累计额度锁死 | PASS：三个新问题均 completed，各自新账本，保留历史；暂停任务续跑仍沿用原额度 |
| F02 一次确认重复写入 | PASS：不同 call ID 请求同一个已确认动作，只新增一条，重复请求停为 waiting_user；并发、重启、响应丢失和事务回滚有集成回归 |
| F04 缺逐轮归档 | PASS：三个完成交互分别得到1、2、3条归档；记录与终态提交，助手文字明确未核实，不冒充完整语义事实抽取 |
| F05 研究 trace 丢失 | PASS：检查共享 recorder / 父子 span / 持久历史接口，并运行集成测试；下一轮和重启后仍可按归属读取 |
| F03 动态早期前缀 | 未实现；准确披露，不以工具预览冻结代替整个前缀稳定 |
| F06 累计 token / 费用 / 时间门 | 未实现；现有硬门仅调用次数，字节窗口及单次 timeout 不能替代 |

本次未修改生产 Python 逻辑，只整理发布文档、路径脱敏、启动包装和 Git 排除规则。原修复过程与历史命令见 [REVIEW_FIXES](REVIEW_FIXES.md)。

## 实际验证

| 阶段 | 命令 / 方法 | 结果 |
| --- | --- | --- |
| Test | `.venv/Scripts/python.exe -X utf8 -m pytest -q --tb=short` | 901 passed、3 deselected、1 warning，29.57秒，退出0 |
| 原问题复现脚本 | 原审查脚本复制到临时目录，以 `python -X utf8 -I` 运行 | 两项 contract_checks 均 true，退出0；未覆盖原失败报告 |
| Dependencies | `python -X utf8 -m pip --isolated check` | No broken requirements found |
| Syntax | `python -X utf8 -m compileall -q src tests scripts` | 退出0 |
| Independent install | `scripts/verify_release.py`，使用修复后 wheel 和本地依赖缓存 | 项目外干净 venv 安装；计算、网页提取、SQLite 工具循环、CLI HTTP 和静态资源通过，退出0 |
| Type / Lint | 项目未配置 | SKIP |
| Security / Diff | 具体待提交文件的密钥/路径/文件类型检查，源码与修复基线及 wheel 对照 | 详见 [发布前检查](acceptance/submission-review-20261021.json)；模式检查不等于全面安全审计 |

默认排除的3项 live 不是通过项。一条 Starlette / HTTPX 弃用警告保留。上述结果不能换算为模型准确率或200轮任务成功率。

## 已知限制与后续范围

- 原真实验收批次已用完，本次不暗中追加请求。修复后的新研究路径有完整可控集成测试，但尚缺一次全新、连续的实网“搜索→阅读→结论”复验；分段真实证据不能冒充该复验。
- Windows 系统关闭通知，实机弹窗显示和点击未通过；会话内通知与离线调度测试已有证据，不承诺休眠或退出时准时提醒。
- OpenAI 适配器保留，真实调用曾被计费额度拒绝；当前真实运行使用 DeepSeek，不声称双模型比较通过。
- 更稳定的缓存前缀、累计资源预算、跨会话长期记忆、通用异步工具事件及更多供应商适配，留到提交后迭代。

结论：基础笔试功能已有实现、离线及有限真实调用证据，可以按上述边界提交；全部扩展设计和完整实机验收没有全部通过。

## 公开材料边界

用户已明确批准创建 `happiness-cheng/agent-harness` 公开仓库，包含两份指定的脱敏可见聊天。聊天正文与已批准预览保持相同字节，不添加伪造历史；不上传其他聊天、原始 JSONL 或隐藏推理。

历史文档与 JSON 中的本机用户目录替换为 `<USER>`，原始版本保存在项目外；测试结果与失败状态不改写，脱敏文件的原始/公开哈希见 [公开导出记录](acceptance/PUBLIC_EXPORT.json)。数据库、凭据、工具配置、构建缓存和本机恢复日志由 `.gitignore` 排除。旧本地 wheel 链接在公开仓库中改为普通路径说明，源码可按 README 自行构建。
