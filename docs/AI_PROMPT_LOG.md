# 关键提示词与调整依据

仅记录实际提示词文件和已经发生的调整，不包含密钥、个人聊天或隐藏思维链。

| 提示词 | 用途 | 实际调整依据 |
| --- | --- | --- |
| [system.md](../src/min_agent/prompts/system.md) | 身份、模式、精确授权、工具与完成判断 | S18 七类真实语义案例；提醒接入后动态声明能力，要求先提案和用户定向确认，实际当前日期由应用注入 |
| [research.md](../src/min_agent/prompts/research.md) | 自主 search/read，隔离上下文，有来源和限制的证据包 | 首轮真实研究消耗完工具额度仍未输出；增加有预算的只读收尾，最后一轮撤去工具，保留证据缺口 |
| [compact_memory.md](../src/min_agent/prompts/compact_memory.md) | 整理已有会话记忆 | 强调日期/条件/否定/用户纠正与出处，不能改变授权 |
| [compact_history.md](../src/min_agent/prompts/compact_history.md) | 压缩较旧完整交互 | 首轮真实摘要把“尚未写入”推成“仍需写入”；增加“未执行不等于仍需执行、只保留显式未解决问题” |
| checkpoints.py 中 CLOSING_INSTRUCTION | 软阈值的一次无工具成果/缺口/阻塞整理 | S20-a 离线测试发现收尾缺已确认目标；补 application_state，不允许临近预算伪造完成 |

真实写入验收曾出现模型声称 todo_add 返回 changed；实际服务返回的是记录。系统说明已明确这一区别；保留原始报告中的表述缺陷，不倒改历史。

真实压缩续问使用的合成事实：“甲截止日期2026-10-22，不是2026-10-21；乙12.30元尚未支付；不要删除原文；只核对、不创建待办”。压缩后模型正确回答四项事实，再由真实模型选择 calculator 得出 12.30+0.20=12.5，无写入工具。报告在 [live-continuity-20261020.json](acceptance/live-continuity-20261020.json)。

模型输出的 source_references 是摘要线索，权威覆盖区间与源哈希由 Harness 记录。格式、日期与引文校验只能约束部分错误，不能替代事实核验。真实报告原先 pending 的审阅字段保留，主代理审阅结论单独记在验收文档；不伪造独立 reviewer 或用户已验收。

2026-10-21实现审查修复未修改生产提示词：F01/F02通过预算作用域和权威动作消费修正，F04/F05通过持久化及trace接线补齐，不依靠提示词掩盖执行漏洞。新测试和架构修订见 [修复记录](REVIEW_FIXES.md)。
