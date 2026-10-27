# 待授权：真实模型的最后一次读取对照

状态：PENDING_USER_APPROVAL。尚未执行任何真实 Provider / Codex CLI 生成。默认额度仍为0。

## 要验证的假说

相同任务、固定来源、相同预算下，真实模型是否会使用候选恢复的最后一次read机会，并给出实际读取后的有效引用。固定策略已通过，但不能预测真实模型会如何决策。

这不声称解决缺材料的旧 research.conflict，也不申请修改其输入、Gold或成绩。旧资料入口变更提案仍单独待审。此次对照是原型 last-read 场景的真实模型验证，基线与候选必须采用同一固定观察器。

## 最小完整批次

| 项目 | 请求范围 |
| --- | --- |
| Provider / 模型 | DeepSeek 官方 Responses / deepseek-flash / high |
| 基线 | 3个任务各运行1次：PSF归档短句、合成保修、合成API超时 |
| 候选 | 同样3个任务各运行1次 |
| 生成上限 | 每任务最多7次，总计 **42次**；不是42次CLI |
| 工具 | 每任务最多3次，沿用Plan、网络许可、Session与原硬预算 |
| 输出 | 仅本批显式8192上限，两版相同；不改Adrian生产自适应输出 |
| 输入 | 从Provider能力读取真实容量，再按实验上限65536收紧；不扩大物理窗口 |
| 费用门 | 每任务Ledger 0.25美元，6个任务合计 **1.50美元** |
| 外部搜索/抓取 | 0：重用冻结的公开短片段及合成资料，排除互联网波动 |
| Codex CLI | 0：候选已经由当前编程Agent生成 |
| 重试 | 不自动重试；Provider错误、超时、额度/认证失败立即停止整批 |

按2026-10-27核对的 [DeepSeek官方 Models & Pricing §Model Details](https://api-docs.deepseek.com/quick_start/pricing/) 峰值未缓存输入0.30美元/百万Token、输出1.20美元/百万Token计算，不依赖折扣或缓存命中。

每请求使用67584输入字节的保守包络（含协议预留）与8192最大输出估算：

`42 × (67584 × 0.30 + 8192 × 1.20) / 1,000,000 = 1.2644352 USD`

申请的1.50美元是本批配置的Ledger预算；现有账本按请求前预留及usage结算实施。字节估计不是精确Tokenizer，Ledger也不是Provider账户账单硬限额。usage缺失/无效会立即停止整批并保留该次完整费用预留；按配置费率结算超限也会停止。脚本不能自动识别上游静默改价，执行前须核对费率；已知费率假设变化时需重新核定预算，不充值、不换账户，也不影响其他应用的费用。

## 外发数据与隔离

仅发送三个任务的已冻结公开/合成问题、公开短引文或合成政策、原应用系统提示和工具Schema、当次工具返回、合成主会话标记。不发送私人聊天、正式Memory、工作数据库、用户文件或认证正文。API key只按标准认证头使用。

每次任务新owner、新Session、新SQLite；memory_mode=off。搜索/阅读I/O只提供该任务冻结来源，未知URL拒绝。原Session/Plan/预算门不变。原生协议状态仅由Runtime自身使用，导出的调用投影排除provider_state，不复制隐藏推理。

模型最大容量通过官方metadata接口获得；该只读发现不计入42次生成上限。未知metadata时停止，不猜窗口或切换模型。

## 已准备的可执行入口

无需授权、不会读取凭据或调用Provider：

```powershell
.\.venv\Scripts\python.exe -X utf8 reports/eval-v1.1-20261027-001/live_pair.py --plan
```

以下命令只能在获得明确批准后运行，凭据文件由用户明确指定，输出目录必须是新的：

```powershell
.\.venv\Scripts\python.exe -X utf8 reports/eval-v1.1-20261027-001/live_pair.py --live --credentials 'C:\项目外\明确指定的凭据.json' --proxy http://127.0.0.1:7890 --out reports/eval-v1.1-live-NEW
```

主入口先核对两份冻结源码和观察器hash，再隔离运行六个worker；父进程按本次Job管理退出。已有MockTransport接线检查只证明适配与门控，不算真实调用证据。授权后的每次实际响应、usage、机械判据和unknown都需要新保存；不得覆盖本轮离线结果。

每版本每题只有一次运行；即使全部完成，也只证明这个有限批次的观察结果，不作总体成功率或泛化提升声明。
