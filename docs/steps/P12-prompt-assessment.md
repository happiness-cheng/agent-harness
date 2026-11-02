# P12：系统提示阶段评估与暂时冻结

日期：2026-10-22。作者要求比较优秀Agent的设计，达到8—9分即可暂时停止优化。

## 结论

针对当前最小Agent范围，主代理静态设计评估约8/10，可以暂时冻结；不是标准化评分、产品排名或实测成功率。不能评为已验证的9分，最新版真实行为仍未验收。

本轮不修改提示，不执行新增模型请求。提示的字节SHA-256为80da134d5552d2b4e43cda7be3d16ec216c24501cc9c4170710b7ba5f31de31c。完整当前提示及上一版差异见[P11](P11-tone-review.md)；本轮没有提示diff，也没有新模型回答。

## 与公开成熟设计比较

| 维度 | 当前判断 | 保留限制 |
| --- | --- | --- |
| 身份与职责 | 明确理解、判断、执行及主动补位 | 第二大脑仍是目标，不等于长期记忆或完整自主能力 |
| 澄清与行动 | 按歧义后果决定，支持独立部分推进 | 模型是否判断准确需实测 |
| 执行闭环 | 已写明执行、观察、验证、继续/等待/报告 | 指令写了不等于验证器或恢复机制已被测试 |
| 权限与事实 | 区分提案、确认、工具结果、未知状态及完成 | 不能把提示当成授权或注入防护的唯一机制 |
| 沟通 | 自然、体贴、有依据地指出问题 | 新版没有真实模型效果证据 |
| 组织与维护 | 可用，暂不阻塞 | 存在重复、工具参数与通用规则混合、信任边界说明分散 |

Codex官方提示指南的Recommended Starter Prompt与Autonomy and Persistence把完成任务、验证、工具使用和沟通分别表达。我们已有相应主线。其示例针对特定Codex模型，不能直接推断DeepSeek遵守程度，也不应原样复制其自主性要求。

Claude Code的The agentic loop说明获取上下文、采取行动、验证结果会交织，问题不同不必走完同一套步骤；我们当前流程已明确是内部按需判断。Security的Permission-based architecture与Protect against prompt injection展示程序权限和沙箱等配套，不能仅靠一段提示证明防护有效。

LangChain的Agents/System prompt与Tools把行为提示、工具及动态middleware作为可分别配置的部分。当前系统提示中todo参数、changed返回等接口说明维护成本偏高；并非所有工具使用指导都必须从提示删除，但字段细节应与工具契约保持单一可靠来源。

## 尚不足9分的具体原因

1. 最新P08—P11没有真实模型A/B。历史P04未证明通用提示整体更好，P06仍有能力承诺不稳定。124项程序回归通过不能替代行为验证。
2. 沟通段、流程与执行边界多次重复相似规则。并非长就不好，而是后续修改可能留下多个不同版本的判断条件。
3. 外部材料不能增加权限已有说明，但“外部指令不能改写当前任务”与“不能授予权限”不是同一件事。仍需检查工具结果、网页和Skill等来源的指令/数据边界；当前不宣称注入安全已通过。
4. 工具字段细节仍混入主提示，后续契约更新可能漂移。暂不借此扩大本轮工作。

## 阶段取舍

采用当前候选并暂时冻结文字优化。冻结指暂停无故改写，不等于功能/安全验收完成，不自动合并main或切换运行中的会话。

后续在既定功能学习或真实使用中记录可复现失败；再按失败定位提示、工具契约或Runtime责任。明确的授权/事实错误或规则冲突应及时处理，不必为冻结而保留缺陷。

若另行开展真实验收，使用明确请求、关键歧义、多部分任务、纠正与范围限制、失败/未知结果及不可信资料等保留场景，预先确定判据与请求预算。当前没有追加额度或启动实验。

## 核验与边界

本轮阅读当前完整system.md、Runtime加载条件和P04/P06/P11记录，并打开下列官方文档。重新核对提示文件未变；仅新增本文。沿用P11中已记录的124项回归证据，未重跑或宣称本轮新测试通过。无代码变更，Build/Type/Lint/Test SKIP；执行文档差异检查及基础密钥模式扫描，不等于安全审计。主代理自审，无子代理。

## 官方依据

- [OpenAI Codex Prompting Guide](https://developers.openai.com/cookbook/examples/gpt-5/codex_prompting_guide#recommended-starter-prompt)：Recommended Starter Prompt、Autonomy and Persistence。属于公开指南，不是所有产品版本的完整内部请求。
- [Claude Code: How Claude Code works](https://code.claude.com/docs/en/how-claude-code-works#the-agentic-loop)：The agentic loop。
- [Claude Code: Security](https://code.claude.com/docs/en/security#permission-based-architecture)：Permission-based architecture；Protect against prompt injection。
- [LangChain: Agents](https://docs.langchain.com/oss/python/langchain/agents#system-prompt)：Core components / System prompt、Tools。
