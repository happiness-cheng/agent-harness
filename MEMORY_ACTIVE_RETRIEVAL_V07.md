# Adrian Memory v0.7：主动检索预算与事实关系实验

## 结论边界

本轮在独立工作区 `feat/adrian-memory-v07-local` 上，以 `30f6126` 为基线。没有修改生产 `src/`、数据库 schema、默认策略、Goal-Value、授权或撤回机制。实验实现放在 `experiments/memory_v07/`，必须从源码工作区运行，不随生产 wheel 发布。

旧实现的失败已通过历史记录和相同 Mock 的真实 Runtime 回放复现。新实现完成受控搜索、读取、回答闭环；这证明预算会计修复可以工作，不等于真实模型已经通过验收。真实结果见 `reports/memory_v07/evaluation_v07.md`，没有执行的指标一律 NOT_RUN。

## P0：修复什么

原实现每轮累计 `内部消息序列化字节 + 工具Schema字节 + 8192`，不按已完成请求的 usage 结算。内部 Message 同时保存逻辑工具调用和 Provider 原生状态；协议适配器实际发送原生表示，不重复发送逻辑调用。内部对象大小不是实际请求大小。

v0.7 的实验包装器只保留一个上下文预算入口：

1. 调用现有 DeepSeek 适配器的纯 `_build_body`，测量实际协议 JSON 字节，不创建客户端或网络请求。
2. 单次保守上界 = 请求 JSON 字节 + 4096 协议余量 + 4096 输出预留。
3. 发送前校验单次上界 ≤32768，以及已结算 Token + 下一次最大预留 ≤65536。
4. 请求结束后，只接受 Provider 返回的非负整数 input/output usage；结算实际 Token，释放没有使用的预留。
5. usage 缺失、异常或超过预留时失败关闭，不能继续花费。全局请求/费用门禁和 Runtime 的3轮/4工具门禁继续独立生效。

这是**预算单位与结算方式的修正**，不能表述为旧的“累计序列化字节最大预留”语义原封不动。数值上限保持一致，下一次请求仍保守预留；不忽略真实累计消耗，不猜测缓存折扣，不删必要证据，不丢原生推理状态。

没有采用压缩来掩盖问题。旧单次请求中未发现重复 system、旧动态状态或重复目录；稳定前缀在不同 API 请求中重复出现是正常的。工具历史和当前证据继续保留。若真实原生状态使单次请求确实超限，本轮会阻断并停止整批，不能提高阈值掩盖失败。

## 职责与文件

| 文件 | 职责 |
|---|---|
| `experiments/memory_v07/budget.py` | 实际协议计量、预留/结算、明确门禁码 |
| `experiments/memory_v07/runtime_loop.py` | 包装真实 Provider，复用现有 ProjectedRuntime 与只读 Memory 工具 |
| `experiments/memory_v07/trial.py` | 冻结材料、有界授权、B0/B2试运行、阻断即停批 |
| `experiments/memory_v07/relations.py` | 独立的来源感知事实关系实验 |
| `scripts/diagnose_memory_budget_v07.py` | v0.6 已保存请求的只读离线回放与计量 |
| `scripts/control_memory_active_v07.py` | 相同 Mock 在旧/新真实 Runtime 的闭环对照 |
| `scripts/eval_memory_active_v07.py` | prepare / 明确授权后 run |

现有 `active_registry`、证据披露 ID、owner、合法时点、确认、场景、删除依赖和 Runtime 工具调用逻辑直接复用，没有另建长期事实库。

## P1：最小事实关系验证

关系包含 subject、attribute、value、state、scenario、valid_time、原始 source/quote/span。用户陈述、历史助手陈述、受信工具实时核实分别标注；问句、猜测和条件句不升级为事实。

固定对抗样本区分两个账户、同一对象冲突、账户号/路由号/用途、讨论/申请/批准/持有/取消、不同情景、未来信息、不同 owner。账号保持字符串，前导零不丢失。

这是受控英文模板解析器，不是通用语义理解系统。无法识别的句子返回空结果，绝不猜测。历史冲突只标“需要审查”，不宣布世界事实矛盾。它没有接入生产、没有自动写 Memory，也没有注入 B0/B2 试运行，因此不会把关系规则的收益混进预算修复效果。

## P2：冻结比较

1道已见公开银行题用于诊断回归，2道新合成题用于账户属性及卡片状态验证；全部非私人资料。不是盲测泛化基准。

B0 是既有生产首轮 Memory + 一次模型回答；B2 是既有只读主动检索 + 修复后的预算包装器，最多3轮模型/4次工具。相同模型 `deepseek-flash`、high、4096最大输出、4096参考材料上限。B2更多调用的成本单列，不能宣称同成本优势。

Gold、禁止行为标签和 source_episodes 留在本地评分包，不送入模型查询、检索或生成。模型只能看到组别协议原本允许的消息/工具/Memory投影。主提示词全文、每次实际消息、工具Schema、实际送达证据及回答保存在运行收据中。

真实调用必须绑定请求包 SHA256、新的用户确认、最多12请求/0.50美元；不使用旧轮次授权，不自动重试。任一 B2 预算阻断立即终止后续全部案例。Provider原生 reasoning 只保存长度/哈希，不保存或展示隐藏推理正文。

## 复现

PowerShell，工作目录为本仓库，Python 为主项目已有虚拟环境：

```powershell
$env:PYTHONPATH='src'
$py='C:/Users/陈独秀/Documents/agent-harness/.venv/Scripts/python.exe'
& $py -X utf8 -m pytest -q
& $py -X utf8 scripts/diagnose_memory_budget_v07.py --output reports/memory_v07/v06-budget-replay.json
& $py -X utf8 scripts/control_memory_active_v07.py reports/memory_v07/p0-control-replay.json
& $py -X utf8 scripts/eval_memory_active_v07.py prepare --output reports/memory_v07/prepared-replay.json.gz
```

输出使用独占创建，不覆盖旧产物。`prepare` 不读凭据、不联网；真实 `run` 必须提供本轮授权文件、固定请求包、明确凭据和代理。所有报告中的 Mock 计数与真实付费请求严格分开。

## 后续边界

本轮完成后不继续扩大 Memory 架构。是否启用 B2 必须依据真实闭环完成率、证据使用、无依据断言和额外成本决定；只有 Mock 通过不能启用。小样本结果也不能证明大规模长期任务已可靠。
