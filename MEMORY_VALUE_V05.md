# Adrian Memory v0.5：端到端记忆价值验证

## 实际结论

在这批6道公开题、3道合成控制的**首轮只读回答**中，生产默认的材料选择/装配链路是可观察到的主要瓶颈之一。把合法原始证据交给相同模型后，公开题的主回答必要事实覆盖由B组4个提升到C组18个。不过，模型仍会遗漏消歧要求、补充无来源规则、写错引用ID，且出现一次JSON格式失败。不能把结果解释成“只需换检索器，模型一定正确”。

这是受控试运行，不是60题全量任务成绩、未见样本泛化、完整Agent工具循环或个人第二大脑的最终验收。没有使用私人两个月记录，没有调用外部Judge。

## 实现边界与差异

沿用v04独立工作区快照，保留286个既有文件的字节哈希。基线为`5b9e8401810d0fc8d8ee39db333dd334ead72ff7`，本轮工作区`C:/Users/陈独秀/.codex/tmp/adrian-memory-v05`，分支`feat/adrian-memory-v05-local`。

真实请求执行时的代码提交为`b85e0cf`。该提交包含冻结协议、9题请求包、运行器、授权门禁与测试。之后仅新增本地分析与交付报告，并修正报告已审状态和评审元数据；没有改变已发送的消息、Gold、生产检索器或重发请求。最终完整提交见`git log`和交付哈希清单，不能仅用旧b90f104复现本轮。

| 新文件 | 责任 |
|---|---|
| `src/min_agent/long_memory/answer_eval.py` | 构建一致A/B/C消息、生产视图等价校验、Oracle来源/时点/预算校验 |
| `src/min_agent/long_memory/answer_run.py` | 明确授权、请求包哈希、发送前最坏费用预留、单次授权占用、逐请求日志、无重试 |
| `src/min_agent/long_memory/answer_scoring.py` | 词面代理、七类失败归因约束、绑定真实回答哈希的来源审查导入 |
| `scripts/prepare_memory_value_v05.py` | 原书哈希验证、6道公开题及3道合成题的冻结输入 |
| `scripts/eval_memory_value_v05.py` | 真实批次运行和完整逐题报告 |
| `scripts/analyze_memory_value_v05.py` | 已取得回答的本地来源审查、独立语义覆盖列及分阶段诊断汇总 |

没有修改生产默认检索、Core Importance、Graph、上下文打包策略或数据库schema。长期确认、owner、时间、去重、删除和既有Runtime边界仍由原实现执行。

## 对照是怎样构建的

```text
原始公开历史 / 合成历史
        → 原有 approved Fixture（模拟批准完整Episode）
        → 固定合法任务时点与owner
           A：不提供长期记忆
           B：bind_runtime_memory → registry.memory_view
              默认GOAL_VALUE、4096字节，包含Core与检索两条路径
           C：人工指定原始证据位置 → 原句/来源/角色/时间校验
        → 相同system + 当前问题 + 动态参考数据
        → deepseek-flash / high / 每组单请求、无工具
        → 原始回答与usage → 本地来源审查与报告
```

B不是v03/v04离线实验排序/打包器，而是生产默认的自动首轮投影；另与直接`assemble_context`结果作相等断言。Core可以绕过FTS直接提供证据，因此不能把FTS没召到等同于模型没看到。

A/B先构建并冻结消息哈希，之后才构建C；Gold、评分标准、必要事实列表和审查结果没有进入模型请求。C只含合法历史原句，保留来源和时间；不会增加预算，也不会静默截断。Oracle覆盖冻结核心事实，但没有覆盖原书所有附加要求，详见失败报告。

三组统一32768保守总预算、4096输出Token上限、4096 UTF-8字节参考上限。输入以消息序列化字节数加4096余量做保守预检，**不是精确Tokenizer计算**；真实Token统计使用服务返回usage。材料不足时不补充别组信息。固定合成题包含当前指令优先、历史状态变化、未来记录排除和无记录不编造。

## 原始数据与提示词

- 冻结包：`reports/memory_v05/prepared-pilot.json.gz`
- SHA256：`817067eb067672d5a3b929762029cb1c588f3289595b7ec9d63c76252422b954`
- [完整共用提示词与差异](reports/memory_v05/final-audit/prompt-and-protocol.md)
- [每题一次问题、A/B/C原始回答、实际材料与评分](reports/memory_v05/evaluation-detail.md)
- [机器可读原始回答](reports/memory_v05/live-results.json) / [逐请求事件日志](reports/memory_v05/live-results.jsonl)
- [最终来源审查](reports/memory_v05/final-audit/source-reviews.json)

初版协议草案曾写multiple_medications；实际制包与用户授权前已固定为multiple_credit_cards。不是根据模型输出换题。原书60题及原始Gold没有改写。

## 评测与安全协议

1. 原始词面成绩原样保留；人工语义覆盖是并列诊断列，不反向修改Gold。主`answer`字段作为回答对象，`uncertainties`另行检查。
2. 原书四项1～4量表由执行Codex代理对真实输出及来源作非盲审查，**不是独立外部Judge或人工用户复核**。有争议的标准单列，不把暂评分当权威泛化成绩。
3. 核心断言准确率只统计审查文件逐条列出的断言，不冒充全文一般常识的完整事实核查。明确区分“有历史原句”和“真实世界已经核实”。
4. 格式失败保留原文，语义成绩NOT_RUN，不自行修JSON、重试或填0分。无记忆的A组是有意移除材料，不诊断成生产STORAGE失败。
5. REASONING/CONFLICT需要可见证据支持；只对具体已审问题作局部判断，不表示所有要求都有完整证据。网络/格式失败不强塞进七个记忆失败类别。
6. 每次请求前记录费用预留；批准包、模型、数量、数据范围不一致则拒绝。授权claim和journal用独占创建，避免重启误计费；未知usage/异常停止批次，无自动重试。

本轮授权27次，实际27次，工具执行0、外部Judge0、私人导入0、自动重试0。只读项目外凭据，不把凭据内容放进日志。未推送远端。

## 复现入口

在当前独立工作区，以PowerShell执行：

```powershell
$env:PYTHONPATH='src'
$py='C:/Users/陈独秀/Documents/agent-harness/.venv/Scripts/python.exe'
& $py -X utf8 -m pytest -q --tb=short
& $py -X utf8 scripts/analyze_memory_value_v05.py --output-dir reports/memory_v05/reproduce-local
& $py -X utf8 scripts/eval_memory_value_v05.py report --pack reports/memory_v05/prepared-pilot.json.gz --results reports/memory_v05/live-results.json --reviews reports/memory_v05/reproduce-local/source-reviews.json --output reports/memory_v05/reproduce-local/detail.json
```

以上只重算本地审查和报告，不发送模型请求，输出已存在时拒绝覆盖。实际已执行过的实时命令保存在`reports/memory_v05/commands.md`；原授权已用尽且claim已存在，**不能通过删claim来重新调用**。新实时实验需新的明确授权。

重新制包使用：

```powershell
& $py -X utf8 scripts/prepare_memory_value_v05.py --book-root C:/Users/陈独秀/.codex/tmp/ai-agent-book-memory-reference --output reports/memory_v05/new-offline-pack.json.gz
```

制包会包含当前代码版本，因此新包文件哈希可能不同，不再受本轮旧授权覆盖。原书依赖源码为[ai-agent-book，第三章user-memory-evaluation](https://github.com/bojieli/ai-agent-book/tree/main/chapter3/user-memory-evaluation)，四项量表及任务成功定义见该目录`evaluator.py`，本地实际数据/Gold清单在冻结包及交付清单中。

## 后续边界

完整60题真实回答、独立Judge、独立人工复核、多次采样稳定性、私人记录质量、真实自动提炼和确认流程、完整工具闭环：**NOT_RUN**。没有为改善分数追加实验或更改生产默认。下一轮需要先评审本报告，再决定投入方向。
