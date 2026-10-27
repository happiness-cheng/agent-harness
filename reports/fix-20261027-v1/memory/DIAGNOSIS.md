# 长原文读取测试：根因调查

## 原始反馈回路

`python -X utf8 -m pytest -q tests/integration/test_memory_tools.py::test_explicit_source_read_and_structured_recall_are_not_silently_head_truncated`

修复前结果：FAIL，实际 partial，预期 completed。见 red.txt。

## 可证伪假设

1. 请求超过测试模型窗口，安全门正确暂停。预测：第二次请求在调用 ScriptedModel 之前触发 ContextLimit；实测符合。
2. 显式来源工具被通用摘要错误截断。预测：模型投影中的来源正文减少，或 tool_summary 被调用；实测不符合，read_memory_source 与 recall_memory 均直接保留完整响应。
3. ScriptedModel 响应耗尽或工具失败。预测：至少两次 generate 或出现工具执行错误；实测仅一次 generate，两个工具均成功。
4. 仅因保留两轮历史而不能压缩。预测：移除旧历史后当前轮足以放入窗口；实测不符合，当前完整轮自身仍超窗。

## 定量证据

ContextManager 默认 Mock/Demo 窗口为 32768；输出预留 4096、安全余量 1024，所以 request_bound 允许上限为 27648。真实 Provider 从 context_limits 元数据读取容量，本调查未调用任何真实模型。

- 第一次请求 request_bound=22850，可派发。
- 两个来源工具返回后 request_bound=30663，超限。
- 即使直接移除全部旧历史，只保留 system、当前完整请求与工具配对、动态状态，request_bound=28008，仍超过 27648。见 current-only-diagnosis.json。
- 原文工具正文 2829 字节，结构化回忆 3841 字节，工具 schema 9942 字节。

因此，仅调整历史保留数量不能让原测试成功；让其成功将需要改变模型容量、当前任务内容、上下文表示或输出预留。不能把生产代码当前的安全暂停误报为原文截断，也不能为测试变绿静默扩大生产窗口。

建议将“足够上下文容量时原文完整送达”与“容量不足时保留来源并安全暂停”作为两个独立工程场景，并保留本次原始失败及输入/配置差异；冻结的 Evaluation TaskSpec/Gold 不应被修改。
