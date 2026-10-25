# C04 完整材料：主上下文多次压缩回归

日期：2026-10-24。用户要求各部分独立推进。本轮只新增上下文连续压缩的测试和报告，生产代码未修改。

## 固定标准与范围

[计划](C04-multi-compaction-plan.md)。先运行已有5个相关测试文件，45 passed in 2.20s；再固定8项新增标准。本轮没有生产修改，因此不是修复前后A/B，也不捏造RED失败。开发样本R01，其余固定验收不是盲测。

输入：先在A点完成一次压缩；A→B新增100轮，第50轮有持续有效约束；B→C再新增100轮，中间再加入约束。没有用户撤销/替换这些约束。摘要不要求逐字包含普通背景，原历史必须完整保留，新增历史必须进入整理过程或留在近期完整材料中。

完整输入/状态/请求：[主工作区报告](../acceptance/c04-multi-compaction-20261024.json)、[独立源码报告](../acceptance/c04-multi-compaction-isolated-20261024.json)。独立报告基于压缩实现220f6ec18d50e08316a54c3b34b81b0d00c6744d；主工作区有其他未提交改动，两处结果分开记录。套件哈希一致：`ebd09e46018f0833e111a8680c5b8708c1613ced1637e48b512cdb43551b293c`。

| 判据 | 结果 |
| --- | --- |
| R01 A→B新增100轮，中间第50轮约束与旧约束都在主模型输入可见 | 通过 |
| R02 B→C再增加100轮，中间新约束与A/B约束都在主模型输入可见 | 通过 |
| R03 covered单调推进，新增压缩范围精确衔接且原文归档覆盖完整已压缩前缀 | 通过 |
| R04 最终摘要引用校验回原始内容，原始会话与近期消息均未删改 | 通过 |
| R05 重建管理器复用同一压缩状态，不额外调用压缩模型 | 通过 |
| R06 已覆盖历史被修改或删除时阻断，不静默复用不匹配摘要 | 通过 |
| R07 下一次压缩响应无效时保持C点上下文状态，原历史完整且不调用主模型 | 通过 |
| R08 压缩额度不足不启动部分模型调用，不提交新摘要或丢弃原历史 | 通过 |

## 合成输入中的关键约束


```json
{
  "A": "不得删除原文，标识A_RULE_UNIQUE。",
  "B第50轮": "必须核验最终结论的来源，标识B_RULE_UNIQUE。",
  "C第50轮": "禁止将未知执行结果报告为成功，标识C_RULE_UNIQUE。"
}
```

每轮有独立TRACE标识。全部原始消息保存在报告的stages.input_messages。实际压缩模型请求保存在compression_requests；最终交给主模型的材料在visible_messages。

离线压缩模型只按固定规则选择包含_RULE_UNIQUE的原句ID，不调用真实API、不改写正文，也不用于证明语义理解准确率。主模型替身仅保存实际收到的输入并返回固定字符串。

## A、B、C实际边界

| 压缩点 | covered | 状态版本 | 路径 | 实际请求字节预算上界 |
| --- | ---: | ---: | --- | ---: |
| A | 2 | 2 | structured | 2752 |
| B | 204 | 4 | llm | 2806 |
| C | 406 | 6 | llm | 3226 |

covered的单位是消息条数，不是对话轮数。实际普通历史整理范围为B:[2,204)、C:[204,406)，各202条，精确接续。A之前的约束经已有摘要继续保留；B和C的中间约束也在最终主模型输入中。B/C各100轮的TRACE在相应压缩请求或近期输入的并集中无缺项，旧消息范围哈希及归档内容核对一致。

A使用实际结构化路径；B/C走实际Compactor的历史/已有摘要整理路径，只有提供选择响应的模型是替身，没有替换摘要器实现或手工注入covered状态。

## A点实际交给主模型的材料


```json
[
  {
    "role": "user",
    "content": "{\"untrusted_session_memory\": {\"kind\": \"structured\", \"episodes\": [{\"archive_id\": \"5a38b854-29bf-4be4-babc-137b74537938\", \"start\": 0, \"end\": 2, \"events\": [{\"kind\": \"user_request\", \"unverified\": true, \"excerpts\": [{\"quote\": \"不得删除原文，标识A_RULE_UNIQUE。\", \"model_selected\": false, \"rule_matched\": true, \"relevance_review\": \"not_reviewed\", \"source\": {\"archive_id\": \"5a38b854-29bf-4be4-babc-137b74537938\", \"session_id\": \"cfcc5a60-ef7c-459a-829b-54fe729f8458\", \"message_index\": 0, \"message_hash\": \"975c856980632e3c72f51357f902c3ae50d0940beeb3f9276bb2cf98213f5d3a\", \"start\": 0, \"end\": 23}}]}, {\"kind\": \"assistant_statement\", \"unverified\": true, \"excerpts\": [{\"quote\": \"普通背景材料。\", \"model_selected\": false, \"rule_matched\": false, \"relevance_review\": \"not_applicable\", \"source\": {\"archive_id\": \"5a38b854-29bf-4be4-babc-137b74537938\", \"session_id\": \"cfcc5a60-ef7c-459a-829b-54fe729f8458\", \"message_index\": 1, \"message_hash\": \"941e12398d47eaa22edc38b57abd44fcbc85271dc0d2c88887cfa47d06ea0de8\", \"start\": 0, \"end\": 7}}]}]}], \"notice\": \"按交互保留独立记录；原句和工具报告不自动成为已核实事实或授权\"}}",
    "tool_call_id": null,
    "tool_calls": [],
    "provider_state": null
  },
  {
    "role": "user",
    "content": "复核已有要求",
    "tool_call_id": null,
    "tool_calls": [],
    "provider_state": null
  },
  {
    "role": "assistant",
    "content": "已了解",
    "tool_call_id": null,
    "tool_calls": [],
    "provider_state": null
  },
  {
    "role": "user",
    "content": "继续按所有已确认约束处理",
    "tool_call_id": null,
    "tool_calls": [],
    "provider_state": null
  }
]
```

## B点实际交给主模型的材料


```json
[
  {
    "role": "user",
    "content": "{\"untrusted_session_memory\": {\"facts\": [], \"constraints\": [\"必须核验最终结论的来源，标识B_RULE_UNIQUE。\", \"不得删除原文，标识A_RULE_UNIQUE。\"], \"corrections\": [], \"open_questions\": [], \"source_references\": [{\"section\": \"constraints\", \"item\": 0, \"artifact_id\": \"5e341fb9-43af-41f8-891f-d95aa83cd3b7\", \"sha256\": \"95acfaf46ddba8d87be6ca766ed6d5a9a2dee0c868251e6dd771ed684eaf34ea\", \"source_kind\": \"user\", \"start\": 19, \"end\": 47, \"model_selected\": true, \"rule_matched\": true, \"relevance_review\": \"not_applicable\"}, {\"section\": \"constraints\", \"item\": 1, \"artifact_id\": \"efcc1c1d-d4c1-4161-a395-8ea5931757ba\", \"sha256\": \"e07dc5d40d6ae14a280c4f96ab6aabb73883bba517690e62350715c6590cdbde\", \"source_kind\": \"unverified_summary\", \"start\": 0, \"end\": 23, \"model_selected\": true, \"rule_matched\": true, \"relevance_review\": \"not_applicable\"}], \"selection_review\": null, \"representation\": \"extractive\", \"unverified\": true}}",
    "tool_call_id": null,
    "tool_calls": [],
    "provider_state": null
  },
  {
    "role": "user",
    "content": "TRACE_B_100，一般工作记录。",
    "tool_call_id": null,
    "tool_calls": [],
    "provider_state": null
  },
  {
    "role": "assistant",
    "content": "阶段B处理记录100。普通背景材料。普通背景材料。普通背景材料。普通背景材料。普通背景材料。普通背景材料。普通背景材料。普通背景材料。普通背景材料。普通背景材料。普通背景材料。普通背景材料。",
    "tool_call_id": null,
    "tool_calls": [],
    "provider_state": null
  },
  {
    "role": "user",
    "content": "继续按所有已确认约束处理",
    "tool_call_id": null,
    "tool_calls": [],
    "provider_state": null
  }
]
```

## C点实际交给主模型的材料


```json
[
  {
    "role": "user",
    "content": "{\"untrusted_session_memory\": {\"facts\": [], \"constraints\": [\"禁止将未知执行结果报告为成功，标识C_RULE_UNIQUE。\", \"必须核验最终结论的来源，标识B_RULE_UNIQUE。\", \"不得删除原文，标识A_RULE_UNIQUE。\"], \"corrections\": [], \"open_questions\": [], \"source_references\": [{\"section\": \"constraints\", \"item\": 0, \"artifact_id\": \"d6e17fb2-61fc-4d33-9be1-d58e42506318\", \"sha256\": \"1cfe6189c14d112a21afe026d1d4c8a50b0ebe762eb17e1a59d91a6394e80566\", \"source_kind\": \"user\", \"start\": 19, \"end\": 50, \"model_selected\": true, \"rule_matched\": true, \"relevance_review\": \"not_applicable\"}, {\"section\": \"constraints\", \"item\": 1, \"artifact_id\": \"5e341fb9-43af-41f8-891f-d95aa83cd3b7\", \"sha256\": \"95acfaf46ddba8d87be6ca766ed6d5a9a2dee0c868251e6dd771ed684eaf34ea\", \"source_kind\": \"unverified_summary\", \"start\": 19, \"end\": 47, \"model_selected\": true, \"rule_matched\": true, \"relevance_review\": \"not_applicable\"}, {\"section\": \"constraints\", \"item\": 2, \"artifact_id\": \"efcc1c1d-d4c1-4161-a395-8ea5931757ba\", \"sha256\": \"e07dc5d40d6ae14a280c4f96ab6aabb73883bba517690e62350715c6590cdbde\", \"source_kind\": \"unverified_summary\", \"start\": 0, \"end\": 23, \"model_selected\": true, \"rule_matched\": true, \"relevance_review\": \"not_applicable\"}], \"selection_review\": null, \"representation\": \"extractive\", \"unverified\": true}}",
    "tool_call_id": null,
    "tool_calls": [],
    "provider_state": null
  },
  {
    "role": "user",
    "content": "TRACE_C_100，一般工作记录。",
    "tool_call_id": null,
    "tool_calls": [],
    "provider_state": null
  },
  {
    "role": "assistant",
    "content": "阶段C处理记录100。普通背景材料。普通背景材料。普通背景材料。普通背景材料。普通背景材料。普通背景材料。普通背景材料。普通背景材料。普通背景材料。普通背景材料。普通背景材料。普通背景材料。",
    "tool_call_id": null,
    "tool_calls": [],
    "provider_state": null
  },
  {
    "role": "user",
    "content": "继续按所有已确认约束处理",
    "tool_call_id": null,
    "tool_calls": [],
    "provider_state": null
  }
]
```

## 失败路径的实际结果


```json
{
  "mismatch": [
    "上下文版本不匹配，停止而不重新摘要旧投影",
    "上下文版本不匹配，停止而不重新摘要旧投影"
  ],
  "invalid_response": "压缩失败，原始历史和可见版本保留",
  "budget": "压缩失败，原始历史和可见版本保留"
}
```

修改或删除已覆盖历史会因版本不匹配停止；没有继续调用主模型。下一次压缩故意返回无效JSON，C点的context状态仍保留，新增原历史仍在。只剩一次调用额度时，没有启动部分压缩调用，调用消耗保持0，也没有提交新摘要。

R03检查：covered单调前进；每次传入普通历史压缩的消息哈希等于原输入相同区间；归档覆盖已压缩前缀，且归档消息与原始会话一致。R04检查：摘要引用的artifact哈希、字符范围和对应原句一致；近期消息尾部保持原样。重建管理器后输入保持，未新增压缩模型调用。

## 可复现命令与实际输出


```powershell
# 主工作区已有相关测试基线
.\.venv\Scripts\python.exe -X utf8 -m pytest tests/unit/test_context_lifecycle.py tests/unit/test_compaction.py tests/unit/test_compression_summary.py tests/integration/test_compression_layers.py tests/integration/test_semantic_compression.py -q --tb=short
# 45 passed in 2.20s

# 独立源码，先确认导入位置
$env:PYTHONPATH = (Resolve-Path .\src).Path
C:\Users\陈独秀\Documents\agent-harness\.venv\Scripts\python.exe -X utf8 -c "import min_agent.context; print(min_agent.context.__file__)"
C:\Users\陈独秀\Documents\agent-harness\.venv\Scripts\python.exe -X utf8 -m pytest tests/unit/test_context_lifecycle.py tests/unit/test_compaction.py tests/unit/test_compression_summary.py tests/integration/test_compression_layers.py tests/integration/test_semantic_compression.py tests/integration/test_multi_compaction_regression.py -q --tb=short
# 53 passed in 23.53s
C:\Users\陈独秀\Documents\agent-harness\.venv\Scripts\python.exe -X utf8 scripts/audit_multi_compaction.py --suite docs/acceptance/c04-multi-compaction-suite-v1.json --report <不存在的新报告路径>
# 8/8；covered=[2,204,406]；B/C缺失轮次=[]
C:\Users\陈独秀\Documents\agent-harness\.venv\Scripts\python.exe -X utf8 -m py_compile scripts/audit_multi_compaction.py tests/integration/test_multi_compaction_regression.py
git diff --check
```

新增单文件独立运行8 passed in 21.85s；全部相关文件53项通过。报告脚本若任一判据失败返回1，不只输出平均值；现有报告使用x模式拒绝覆盖。没有运行外部模型、读取凭据或修改工作数据库。

## 核心测试与完整源码

[新增测试](../../tests/integration/test_multi_compaction_regression.py)使用同一份固定套件，按每项判据独立断言；[完整探针](../../scripts/audit_multi_compaction.py)记录全部证据。核心断言是：

```python
assert evidence["outcomes"][case["id"]] == case["expected"]
```

没有通过修改生产实现让这些测试变绿，本次未发现所测主压缩路径的连续性故障。未观察到故障不等于保证任意长会话无损压缩。

## 自审与限制

主代理自审：仅新增测试、套件和文档；原始记录未删改；未改生产模块；两个模型均为离线替身；实际源码路径已确认；报告与链接、语法及差异、定向敏感特征已检查。没有子代理审查。

这证明固定样本下的上下文连续性、原文可追溯和失败保留，不评价真实模型选择是否正确、摘要自然语言忠实度、任意消息分布或语义改写的多轮漂移。本轮关键约束走完整原句保留；语义改写沿用既有相关测试，不把这8项称为真实语义评测。

Type/Lint未配置，安装打包跳过；没有生产改动，不重复无关全量应用测试。主工作区此前记忆读取失败不在本轮53项范围，不声称整个应用全绿。此前“下游任务材料只取最近两轮”的已知接口缺口也未被本轮修复，不能用本报告关掉该待办。
