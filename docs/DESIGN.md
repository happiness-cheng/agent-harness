# 设计与决策

2026-10-21更新。需求基线为原交接 HANDOFF.md / STEPS.md；用户后续明确授权连续完成并取消逐步讲解。原交接文件未改写。下列为实际采用的设计，不是未来平台承诺。

| 决策 | 采用方案及原因 | 未选方案与代价 |
| --- | --- | --- |
| 核心循环 | Python Runtime 自行驱动模型与串行工具，状态和错误可测试 | 不用 Agent 框架托管；需要自行处理协议配对和恢复 |
| 数据契约 | Pydantic 严格消息、动作和检查点，真实 handler 输出独立 | 不解析任意自由文本作为工具调用，牺牲非结构化模型兼容性 |
| Web | FastAPI + Uvicorn，本机 Cookie/CSRF/Origin/Host 门，1.5 秒轮询 | 不引入 SPA 框架或 WebSocket，降低依赖，事件不是逐 token 流式 |
| 数据 | SQLite 事务、原两表和快照 extensions | 不为每种扩展新增表，代价是快照增长和单机写入边界 |
| 并发 | 四个会话 worker，每会话一个执行者；共享存储 RLock | 不并行执行同一响应的工具，换取清楚的写入顺序 |
| 权限 | 可信 owner/session、Plan/Build、定向提案 ID、规范化动作实例与同业务事务的消费回执 | 不以提示词或“Build 已选择”代替真实写入授权 |
| 模型 | 明确凭据文件、固定官方端点和 ID、不自动换 key/提供商 | 不读取 Codex auth；OpenAI 计费失败不归因为 token 输出上限 |
| 预算 | 新逻辑任务新账本，续跑新片段沿用原账本；调用前持久扣除，研究/压缩共享 | 不把session等同任务，不在调用后统计；仅调用次数硬门，F06累计token/时间/费用仍缺 |
| 无进展 | 归一化调用及结果，三次重复后一次纠错，再重复暂停 | 不把时间戳变化当新事实，也不把合法无变化操作一概视为失败 |
| 恢复 | 检查点哈希与预算绑定，操作 ID 日志，未知结果先核对 | 不承诺任意外部副作用可回滚或恰好一次 |
| 搜索 | HTTPX 固定 Bing 公开页，复用 DDGS 解析字段/链接，有限公开查询和显式代理 | 库的 Bing 引擎已禁用且会自动回退；改用固定请求，不转发到未选引擎。结果质量仍波动，粗筛不是事实验证 |
| 网页 | HTTPX 固定公开 IP + 原 Host/SNI，重定向复检；gzip/deflate 在解压前限制输出，trafilatura 提取正文 | 不接受私网/任意端口，不关闭 TLS，不无限解压，也不使用未验证的代理固定 IP 隧道 |
| 研究 | 隔离问题/约束，显式校验持久预算与活动父任务；共享trace及预算，只读search/read，预留证据输出 | 不复制全部主聊天，不递归委派；增加调用与延迟 |
| 证据 | 引文必须对应当前实际已读原文；唯一逐字短句可校正位置并显式标记 | 不将来源摘要或模型记忆包装成已读证据；校验不保证结论本身完全正确 |
| 上下文 | 原文、冻结投影、版本分开；UTF-8 序列化字节作保守上界 | 不冒充精确 provider token 计数，可能比真实窗口更早压缩 |
| 压缩 | 先记忆再旧历史，完整配对，验证后换版本，连续失败熔断 | 不删除原始日志，不让摘要重建权限；仍需语义验收 |
| Memory | 完整交互与终态一起归档，压缩记录独立；当前session相关性检索和受控原文分页 | 无跨 session 画像、向量库或全历史常驻上下文 |
| Skill | 预登记 Markdown 名称/摘要，按需正文，只读受控路径 | 无脚本 Skill 和插件执行器 |
| 提醒 | 明确绝对时间/时区，后台注入时钟，到点不调用模型 | 不把 todo 日期变成默认提醒，也不运行周期性模型任务 |
| 通知 | 一条持久事件，两渠道状态，attempting 崩溃转 unknown | 不把 API 接受当用户看到；全局系统通知开关由用户控制 |

主要文件：src/min_agent/application.py、runtime.py、sessions.py、budget_persistence.py、context.py、compaction.py、research.py、scheduler.py。调用关系见 [README](../README.md)。实际结果和未通过的系统通知门见 [验收记录](ACCEPTANCE.md)。

固定 DNS/SNI 的具体实现对应 [HTTPcore connection.py 的 _connect](https://github.com/encode/httpcore/blob/master/httpcore/_sync/connection.py)；Windows 通知发送与交互能力分别见 [Windows-Toasts Getting Started](https://windows-toasts.readthedocs.io/en/latest/getting_started.html) 和 [Interactable 通知章节](https://windows-toasts.readthedocs.io/en/v1.3.1/interactable.html)。当前真实显示与点击尚未通过，库文档不能代替本机观察。本机检索与恢复日志不随仓库公开。

## 实现审查后的边界

F01/F02/F04/F05已按 [修复记录](REVIEW_FIXES.md) 补齐并回归。trace保存最近20次run、每次2000事件，有父子关联和归属检查。旧v1确认只读兼容，写入需新提案；不迁移数据库schema，不清除旧记录。

F03早期动态application_state仍可能破坏其后缓存前缀，未实现追加投影；F06累计token、整个任务deadline和费用限制未实现。字节窗口估算、HTTP timeout和单次输出限制不代替这些门，也没有200轮真实模型或缓存命中率实测。
