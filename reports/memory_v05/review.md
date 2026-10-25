# v0.5交付自审

Verdict: PASS（限定为9题受控试运行与评测设施；不是60题全量或完整Agent验收）

未使用子代理，由主执行代理自审；没有伪造独立审查。

| 检查项 | 状态 | 证据/边界 |
|---|---|---|
| 需求满足 | PASS with limits | A/B/C同配置、相同预算、27份真实输出；全量60题和外部Judge未跑 |
| 边界条件 | PASS | 20定向测试、费用/请求/哈希限制、未知usage停止、未来Oracle拒绝、未执行不填分 |
| 安全与数据 | PASS | 286旧文件哈希、60题原始数据、69项制包源码、27唯一请求日志；不读私人导出，不打印凭据 |
| 代码范围 | PASS | 生产默认/数据库/Runtime未修改；新增可独立运行的评测模块及脚本 |
| 可复核性 | PASS with limits | 原始JSONL、失败JSON、单题来源与评分、命令、版本、哈希均保存；评分为非盲执行代理审查 |

## 修正及保留的问题

- 报告“已审”状态和public_book适用标记经本地检查修正，最终读final-audit；保留早期草稿，不覆盖原始回答或Gold。
- 格式错误不自动修复、不重试，不作为检索失败计0分。
- 候选截断、Top8后的装配损失与Core等价证据分开记录，避免以单一Gold来源判漏召。
- 原书预批准时点、入境规则和信用卡属性存在证据/标准冲突，单列EVALUATION。
- 主观来源审查缺独立复核；Oracle并非覆盖完整原书标准；成本是峰值估算非账单，延迟单次采样。

## 核心代码：先固定A/B，再构建Oracle

它像三张独立考试卷：先封存前两张，第三张的参考证据不能混进前两张。代码位于`src/min_agent/long_memory/answer_eval.py`的`prepare_answer_case`：

```python
bind_runtime_memory(registry, memory, context)
automatic = registry.memory_view([
    Message(role='user', content=case['user_question'])
]) or {}
direct = memory.assemble_context(context, query)['view']
assert automatic == direct
groups = {
    'A': make_messages(case['user_question'], at, {}),
    'B': make_messages(case['user_question'], at, automatic),
}
frozen_ab = {g: groups[g]['request_sha256'] for g in groups}
# 后续C只能从合法原文位置提取；其后再次断言A/B哈希未变。
```

这里只展示便于阅读的等价片段，完整代码还传递reference_budget并校验来源、时间及费用。该局部测试与27请求试运行不能证明所有生产环境问题已消失。
