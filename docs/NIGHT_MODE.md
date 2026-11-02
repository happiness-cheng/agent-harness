# Adrian 夜间模式：最小实施设计

本需求接续统一验证V1。队列负责调度，不另建Agent Loop或业务状态库；任务通过现有Harness执行、独立Verifier验收。夜间任务状态与Runtime单次执行状态分别保存，不能将模型completed直接当夜间任务完成。

## 当前能力与限制

- 本机已通过`codex --version`与`codex exec --help`核对：codex-cli 0.164.0-alpha.11.1，支持exec、read-only/workspace-write、output-schema、ephemeral、ignore-user-config。不查询/修改认证文件或密钥。非交互真实生成另在受控示例中验证，不把help成功当账户可用。
- 官方依据：[Non-interactive mode §Permissions and safety / Create structured outputs](https://developers.openai.com/codex/noninteractive)、[CLI reference §codex exec](https://developers.openai.com/codex/cli/reference)。
- Codex真实余额/剩余订阅额度不可观测。V1限制的是CLI启动次数和运行时长，不冒充精确模型轮数或货币余额。不会自动切换OpenAI付费API。
- 豆包只输出人工接力包，状态WAITING_FOR_USER，不模拟App或宣称可夜间自动调用。
- V1不运行任意部署、安装、删除或对外发消息工具；即使任务自称低风险也没有这些工具能力。代码修复先限于受控、AST可验证的计算器绑定示例，通用任意编程需单独的OS隔离/范围审核，暂不冒充已实现。

## 状态、调度和持久化

PENDING → RUNNING → VERIFYING → COMPLETED；明确失败可有限重试，最终FAILED。审批或外部人工操作进入WAITING_FOR_USER；费用/调用额度不足进入BLOCKED_BY_BUDGET。遇阻塞继续选择依赖已完成的其他独立任务。依赖失败/不存在要说明原因，不能无限等待。

队列保存目标、优先级、依赖、允许工具/数据、TaskSpec验收契约、状态、尝试、批准需求和证据路径。JSON版本化持久化、原子替换，复用现有OS文件租约防止两个调度者同时执行。不迁移SQLite Schema，不碰生产数据库。每次尝试用新的隔离评估目录/Session；代码候选用独立worktree，不自动合并或推送。

重启不能清空预算或夜间起始时间。遗留RUNNING任务优先核对已落盘结果；无法证明完成的任务挂起，保留费用预留，不盲目重放潜在副作用。已完成任务不重跑。子进程超时按UNKNOWN处理，不声称外部操作已撤销。

## 预算与路由

默认最长10小时、保留20%额度；真实DeepSeek费用/Codex调用预算默认0，需在创建夜间计划时显式配置后用live模式启用。程序任务无需模型；analysis优先DeepSeek；code路由Codex受控修复；manual路由豆包接力包。允许有限升级但不在多个模型之间来回循环。

每个DeepSeek任务先占用有限费用包络；每次真正生成前在持久账本预留“输入保守上界+本次申请最大输出”的费用，再根据实际usage按配置的峰值费率结算。费用信息缺失/请求结果未知时保留预留，不当作免费。费用门禁依据已配置费率，真实账单不可观测；费率可能变化，报告标明来源和日期。没有足够资金覆盖最大潜在响应时阻塞，不偷偷将所有任务统一限制为4096 Token。

Codex每次启动前扣调用次数，失败/超时也不退回次数；不假装知道订阅余额。真实执行必须使用最小权限沙箱，不使用yolo、ignore-rules或danger-full-access。限期、最大重试/升级次数与审批边界不因用户睡觉而改变。

## 固定验收

- 模拟时钟推进10小时，报告明确SIMULATED，不冒充真实墙钟运行10小时。
- A待审批仍能执行B；依赖未满足的任务不执行。
- DeepSeek费用不足不进入适配器；Codex次数用尽不调用CLI。
- 在每次生成前的成本账本预留生效；未知调用不释放预算。
- RUNNING/VERIFYING重启不盲目重试，已有验收结果可恢复。
- 反复失败达到次数后停止；安全/标准变更不重试绕过。
- 早晨报告区分已验证、失败、等待用户、预算阻塞、未知结果，列出成本可观测边界和下一步。

本轮已提供可重复离线演示和CLI，不自动开始一个真实10小时付费后台任务、不设开机启动。

## Windows操作与任务示例

先看 `docs/examples/night-jobs.json` 与 `night-job.schema.json`。默认示例包括：真实项目计算器回归（不用模型）、公开代码分析草案（语义待人工审查）、隔离计算器受控修复演示（Codex）、一个必须挂起的部署任务。通用任意代码实现仍不自动开放。

```powershell
$env:PYTHONPATH='src'
# 零真实模型调用的10小时模拟，含真实Harness/SQLite/Verifier
.\.venv\Scripts\python.exe -X utf8 -m min_agent.evaluation.night_cli demo --out reports/my-night-demo
# 初始化新的夜间计划；真实模型预算默认0，此例明确配置
.\.venv\Scripts\python.exe -X utf8 -m min_agent.evaluation.night_cli init --queue reports/my-night/queue.json --jobs docs/examples/night-jobs.json --hours 10 --deepseek-budget 1 --codex-calls 2 --reserve 0.2
# 仅执行无需模型的任务，其余安全阻塞
.\.venv\Scripts\python.exe -X utf8 -m min_agent.evaluation.night_cli run --queue reports/my-night/queue.json
# 明确开始真实夜间工作；需要保持窗口和电脑运行，不自动设置开机/定时启动
$credentialsFile=Join-Path $env:USERPROFILE 'agent-harness-credentials.json'
.\.venv\Scripts\python.exe -X utf8 -m min_agent.evaluation.night_cli run --queue reports/my-night/queue.json --wait --live --credentials $credentialsFile --proxy http://127.0.0.1:7890
# 早晨查看/重新生成报告
.\.venv\Scripts\python.exe -X utf8 -m min_agent.evaluation.night_cli report --queue reports/my-night/queue.json
```

报告默认在队列同目录的`morning-report.md`，每项都指向尝试目录和结果证据。`--wait`空闲时仅等待，不调用模型；不加时处理现有可执行任务后退出。停止可Ctrl+C；重启用同一queue文件，不能用新计划冒充恢复或重置预算。遗留RUNNING不自动重放；先核对保留的结果，人工决定后再创建/调整下一步任务。

具体预算解释：2个Codex槽位、20%预留时，只允许自动占用1个槽位，另1个保留。`codex_calls`是预扣槽位，`observed_codex_invocations`是适配器已观测到的启动；不等于内部LLM请求或真实剩余额度。Codex真实示例耗时约128秒，因此代码任务示例给240秒整体时间，另有180秒CLI上限，不沿用普通问答的短超时。

遇到DeepSeek账户额度/认证错误会持久关闭该路线；Codex非交互执行失败也会保守关闭同路线。继续其他独立程序任务，不无限探测余额或切换账户。恢复路由须用户先核对账户及旧结果；V1没有自动充值或自动权限批准入口。

`operation=regression`仅接受tests/下明确路径，在新worktree中复制当前实际src/tests再调用原pytest，结果直接由退出码核验。`operation=calculator_repair`仅允许`src/min_agent/tools/calculator.py`的受限操作绑定候选，修复后还需通过该任务原有的独立验收。其他代码目标挂起等待范围审核。

费用限额依据配置峰值费率和保守Token上界，并非Provider账单实时熔断；费率变化、缺失usage和未知调用会保留预留并报告。电脑休眠、断电时不会继续运行；重启按持久状态恢复，不能声称整晚从未中断。本轮验证为加速时钟模拟，并未真实值守10小时。

## 本轮实际证据

[完整交付报告](../reports/eval-v1/REPORT.md)记录真实DeepSeek及Codex调用、全量回归、版本与限制。[模拟早晨报告](../reports/eval-v1/night-demo/morning-report.md)覆盖审批、两条模型路线预算阻塞、有限失败重试、独立任务和豆包接力；其中路线与费用为注入模拟。另有真实调度进程被终止再恢复的集成测试，旧任务挂起且不重放，独立任务继续完成。未启动真实整夜任务，也未自动重启现有网页服务。
