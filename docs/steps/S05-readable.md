# S05 完整材料：模型选择整份Skill是否继续保留

日期：2026-10-23。只评测选择判断，不改生产系统提示、Skill正文或压缩器，不执行任何工具。

## 独立选择器的完整提示

这不是主Agent系统提示；是在独立候选组件中测试分类判断。

```text
你只判断已加载Skill是否仍适用于当前工作。根据当前请求和明确上下文，将每个候选id恰好放入keep、release、uncertain中的一个数组。不要改写正文，不新增id，不授予权限。临时插问不等于原任务结束；缺少判断依据时选择uncertain，系统会暂时保留。已明确替换的旧版本不再适用。预算不足不能成为删除必需规程的理由，由系统处理。只返回JSON对象，包含keep、release、uncertain三个字符串数组。
```

## 候选Skill完整内容

以下四份都是合成短规程，不是对全量真实Skill的测试。

```json
[
  {
    "id": "polish-v1",
    "name": "writing-polish",
    "version": "v1",
    "description": "整理中文表达，保持事实、金额与完成阶段。",
    "body": "润色时保留用户的事实、日期、否定及完成阶段；金额保留两位小数。"
  },
  {
    "id": "polish-v2",
    "name": "writing-polish",
    "version": "v2",
    "description": "整理中文表达，金额原样保留。",
    "body": "润色时保留用户的事实、日期、否定及完成阶段；金额按原文保留，不四舍五入。"
  },
  {
    "id": "debug-v1",
    "name": "debug-feedback",
    "version": "v1",
    "description": "通过复现、日志和可证伪假设定位故障。",
    "body": "先检查复现条件与真实错误，再做对照实验；工具结果unknown不算成功，不盲目重放写入。"
  },
  {
    "id": "evidence-v1",
    "name": "public-evidence",
    "version": "v1",
    "description": "核验资料日期、条件及来源冲突。",
    "body": "区分来源陈述与已核实事实，保留日期、条件、否定和冲突；缺依据时不猜哪一方更可能正确。"
  }
]
```

## 实际输入结构

两条消息：system为上面的选择器提示；user为current_request、task_context及loaded_candidates。没有发送expected、样本分组或评分结果；tools=None。完整逐次消息见[真实报告](../acceptance/s05-skill-retention-live-20261023.json)。

## R01：同题两次实际回答

问题：继续排查同一个接口错误。

任务上下文：故障任务未结束，润色任务已交付且用户明确结束。

候选：debug-v1, polish-v2

标准（未发送给模型）：

```json
{
  "must_retain": [
    "debug-v1"
  ],
  "must_release": [
    "polish-v2"
  ],
  "prefer_uncertain": []
}
```

第1次模型回答原文（finish_reason=stop）：

```json
{"keep":["debug-v1"],"release":["polish-v2"],"uncertain":[]}
```

第2次模型回答原文（finish_reason=stop）：

```json
{"keep":["debug-v1"],"release":["polish-v2"],"uncertain":[]}
```

不足：上下文已人工整理，候选短且区分明显；两次重复不能证明在原始长历史、任务混杂或相似Skill中稳定正确。

## R02：同题两次实际回答

问题：周报润色已经结束，接下来只排查空串导致的接口错误。

任务上下文：用户明确切换任务；不再处理周报或资料核验。

候选：polish-v2, debug-v1, evidence-v1

标准（未发送给模型）：

```json
{
  "must_retain": [
    "debug-v1"
  ],
  "must_release": [
    "polish-v2",
    "evidence-v1"
  ],
  "prefer_uncertain": []
}
```

第1次模型回答原文（finish_reason=stop）：

```json
{"keep":["debug-v1"],"release":["polish-v2","evidence-v1"],"uncertain":[]}
```

第2次模型回答原文（finish_reason=stop）：

```json
{"keep":["debug-v1"],"release":["polish-v2","evidence-v1"],"uncertain":[]}
```

不足：上下文已人工整理，候选短且区分明显；两次重复不能证明在原始长历史、任务混杂或相似Skill中稳定正确。

## R03：同题两次实际回答

问题：先插问一下1加1，然后我们继续刚才的故障排查。

任务上下文：接口故障仍待解决；旧润色任务已结束。

候选：debug-v1, polish-v2

标准（未发送给模型）：

```json
{
  "must_retain": [
    "debug-v1"
  ],
  "must_release": [
    "polish-v2"
  ],
  "prefer_uncertain": []
}
```

第1次模型回答原文（finish_reason=stop）：

```json
{"keep":["debug-v1"],"release":["polish-v2"],"uncertain":[]}
```

第2次模型回答原文（finish_reason=stop）：

```json
{"keep":["debug-v1"],"release":["polish-v2"],"uncertain":[]}
```

不足：上下文已人工整理，候选短且区分明显；两次重复不能证明在原始长历史、任务混杂或相似Skill中稳定正确。

## R04：同题两次实际回答

问题：请先核对这两份公开材料的矛盾，再把结论整理成简洁说明。

任务上下文：两份材料已提供，核验和润色均属于当前任务；故障任务已结束。

候选：polish-v2, debug-v1, evidence-v1

标准（未发送给模型）：

```json
{
  "must_retain": [
    "polish-v2",
    "evidence-v1"
  ],
  "must_release": [
    "debug-v1"
  ],
  "prefer_uncertain": []
}
```

第1次模型回答原文（finish_reason=stop）：

```json
{"keep":["polish-v2","evidence-v1"],"release":["debug-v1"],"uncertain":[]}
```

第2次模型回答原文（finish_reason=stop）：

```json
{"keep":["polish-v2","evidence-v1"],"release":["debug-v1"],"uncertain":[]}
```

不足：上下文已人工整理，候选短且区分明显；两次重复不能证明在原始长历史、任务混杂或相似Skill中稳定正确。

## R05：同题两次实际回答

问题：接着处理刚才那个。

任务上下文：前文同时有未完成的润色和故障排查，用户未说明指哪个；目前无法消歧。

候选：polish-v2, debug-v1

标准（未发送给模型）：

```json
{
  "must_retain": [
    "polish-v2",
    "debug-v1"
  ],
  "must_release": [],
  "prefer_uncertain": [
    "polish-v2",
    "debug-v1"
  ]
}
```

第1次模型回答原文（finish_reason=stop）：

```json
{"keep":[],"release":[],"uncertain":["polish-v2","debug-v1"]}
```

第2次模型回答原文（finish_reason=stop）：

```json
{"keep":[],"release":[],"uncertain":["polish-v2","debug-v1"]}
```

不足：上下文已人工整理，候选短且区分明显；两次重复不能证明在原始长历史、任务混杂或相似Skill中稳定正确。

## R06：同题两次实际回答

问题：继续按我刚刚明确加载的新版规则润色这份预算说明。

任务上下文：已明确加载polish-v2替换v1；故障任务已结束。

候选：polish-v1, polish-v2, debug-v1

标准（未发送给模型）：

```json
{
  "must_retain": [
    "polish-v2"
  ],
  "must_release": [
    "polish-v1",
    "debug-v1"
  ],
  "prefer_uncertain": []
}
```

第1次模型回答原文（finish_reason=stop）：

```json
{"keep":["polish-v2"],"release":["polish-v1","debug-v1"],"uncertain":[]}
```

第2次模型回答原文（finish_reason=stop）：

```json
{"keep":["polish-v2"],"release":["polish-v1","debug-v1"],"uncertain":[]}
```

不足：上下文已人工整理，候选短且区分明显；两次重复不能证明在原始长历史、任务混杂或相似Skill中稳定正确。

## 固定策略对照

| 策略 | 必需规程/版本等安全门通过 | 完整选择判据通过 |
| --- | --- | --- |
| 全保留 | 5/6 | 1/6 |
| 全移除 | 0/6 | 0/6 |
| 模型两次重复 | 12/12 | 12/12 |

不同分母是因为基线每题一次，模型每题两次；不称为总体准确率。全保留是故意朴素的对照，不代表已经与“先确定性淘汰旧版”等更好规则比较过。

模型选择正文累计1844 UTF-8字节，同样12个输入若全保留则3402字节；只是所选正文之和，不是完整请求或token。筛选自身input_tokens=4276、output_tokens=1822、total_tokens=6098；不能据此声称节省费用或总体上下文。

[固定场景与guard判据](../acceptance/s05-skill-retention-suite-v1.json)；[离线基线](../acceptance/s05-skill-retention-baseline-20261023.json)。guard为评测工具中的拒绝/保守保留原型，预算案例是参考计算，不是生产Runtime集成验收。
