# 交付与验收记录



> 公开提交的独立复核见 [提交说明](SUBMISSION.md)。本文保留当时的测试与未发布状态；公开版仅脱敏本机用户目录，历史失败不改成成功。



> 2026-10-21 实现审查后更新：以下882项和旧 wheel 是审查前历史。本轮 F01/F02/F04/F05 修复、901项回归与新安装包见 [审查修复记录](REVIEW_FIXES.md)；F03/F06仍未完成，全部扩大设计和实机门不能标为通过。



更新：2026-10-21，Asia/Shanghai。用户已授权后续连续实施，不再逐步停审阅或讲解。原交接五份文档保持原样；旧停点和失败报告保留。



应用实现及离线验收已完成，可在 [本机工作台](http://127.0.0.1:8765) 使用。完整实机验收仍有一项明确未通过：Windows 弹窗显示及点击。用户回答“没有看到通知”，本机系统通知确实关闭；未擅自改系统开关。不能把桌面 API 接受写成已展示，也不声明“全部实机验收通过”。



## 最终离线验证



工作目录 C:\Users\<USER>\Documents\agent-harness，解释器 .venv\Scripts\python.exe，Python 3.13.16。



| 阶段 | 实际命令 / 证据 | 结果 |

| --- | --- | --- |

| Test | .venv\Scripts\python.exe -X utf8 -m pytest -q --tb=short | 882 passed，3 deselected，1 warning in 24.18s；退出 0，含真实 Edge 浏览器两项 |

| Build | .venv\Scripts\python.exe -X utf8 -I -m pip --isolated wheel --disable-pip-version-check --no-build-isolation --no-deps --no-index --wheel-dir dist/final-20261021-reviewed . | 已构建最终 wheel，退出 0 |

| Dependencies | .venv\Scripts\python.exe -X utf8 -m pip --isolated check | No broken requirements found |

| Syntax | .venv\Scripts\python.exe -X utf8 -m compileall -q src tests scripts | 退出 0 |

| Type | 未配置类型检查器 | SKIP，不称 PASS |

| Lint | 未配置 lint | SKIP，不称 PASS |

| Security | 限定源码扫描、敏感文件名核对、包内容核对及实际归属/CSRF/SSRF测试 | 在检查范围内通过，不是全面漏洞审计 |

| Diff | 原交接/原正式库当前哈希、源码清单和最终 wheel 逐字节核对 | 不执行删除/Git/迁移；本轮完整初始基线不可读取，不冒称全量差异哈希相同 |



默认排除的三项 live 不会被普通回归外发。Starlette TestClient 对 HTTPX 有一条 deprecation 警告，未导致失败，未为消除警告更换真实 HTTP 适配器。



最终包：`agent_harness-0.1.0-py3-none-any.whl`（本地构建产物，路径 `../dist/final-20261021-reviewed/agent_harness-0.1.0-py3-none-any.whl`），SHA-256：



~~~text

0acd9ee9a7f45a2edd9d0fd7db1c764def2c13bfcaeea472bcf280d4ab5f4c42

~~~



最终 [资源核对](acceptance/build-reviewed-20261021.json) 确认 43 个 Python 模块、51 份包资源逐字节一致，无数据库、JSON、日志或测试打包。[独立安装报告](acceptance/clean-install-reviewed-20261021.json) install/smoke 均退出 0。[限定安全及版本比较](acceptance/security-reviewed-20261021.json) 记录相对 S20-a wheel 新增 28、修改 9、移除 0 份包文件。早期 final-20261020、final-20261021 产物保留，最终使用 reviewed 目录。



独立环境验证命令：



~~~powershell

.\.venv\Scripts\python.exe -X utf8 scripts/verify_release.py --wheel dist/final-20261021-reviewed/agent_harness-0.1.0-py3-none-any.whl --wheelhouse dist/final-20261020/dependencies --report docs/acceptance/clean-install-reviewed-20261021.json

~~~



脚本新建项目外干净 venv，从本地 wheelhouse 安装，使用 -I 从 site-packages 导入；实际检查 0.1+0.2、HTML 提取、SQLite 应用工具循环、独立 CLI 后台、HTTP 会话及三个静态资源。无真实模型调用、不读取凭据、不从源码目录导入。缓存含 Windows/Python 3.13 依赖，不冒充跨平台环境验证。



## 剩余步骤与证据对应



| 范围 | 实际实现 | 主要离线证据 |

| --- | --- | --- |

| S20-b | 历史/终态/检查点一起保存；归属、原预算、历史和目标绑定；新片段恢复 | integration/test_checkpoint_persistence.py、e2e/test_acceptance.py |

| S21-S25 | 持久 Queue/Steer/Stop、重复无进展与一次纠错、操作日志、未知结果先核对 | unit/test_execution_control.py、integration/test_operation_recovery.py、integration/test_integrated_boundaries.py、e2e |

| S26-S29 | 固定公开搜索、固定 IP/SNI/重定向检查、受限解压与原文分页、只读研究和引文验证 | unit/test_public_web.py、scenarios/test_research_evidence.py |

| S30-S37 | 原文/冻结投影/版本、窗口预检、会话归档和相关回查、分层压缩、两次失败熔断、文本 Skill | unit/test_context_lifecycle.py、unit/test_compaction.py；provider 清理 capability 明确为不支持，验证本地 fallback |

| S38-S42 | 绝对时刻提醒、无模型调度、双渠道、关联 todo 取消、漏触发与崩溃窗口 | unit/test_reminder_scheduler.py；Windows 人工显示/点击仍未通过 |

| S43-S46 | 环回 Web 身份与 CSRF、异步控制接口、双会话 UI、原文/通知/未读 | integration/test_application_flow.py、ui/test_chat_flow.py |

| S47-S48 | 综合恢复、真实小样本、干净安装、包核对、README/设计/Prompt/问题记录 | 本文、e2e、acceptance 原始报告和 verify_release.py |



新增恢复和控制还覆盖：晚到 Steer 不执行旧工具，晚到 Stop 保留文字但取消任务，压缩/上下文准备期间 Stop 不再启动后续模型，停止压缩不扣新额度或累计成摘要失败；坏 handler 返回配对后可恢复；运行中不能确认目标；空消息 dispatch 真正启动旧队列并拒绝绕过检查点。



每个阶段测试文件名按实际代码组织，与交接建议文件名可不同。没有启用开发子代理、Git 操作或新 SQL schema。



## 真实模型验收



提供商 DeepSeek 官方 /responses，deepseek-flash/high，指定项目外凭据，显式本机代理；每次输出上限 4096，无自动重试。只发送合成任务和公开材料。



本次连续完成批次恰好 **32/32 次**，包括失败；额度已用完，没有自动另开批次。此前 S17/S18 的 22 次另计，历史累计 54 次 DeepSeek；OpenAI 计费失败不改写为成功。



| 报告 | 请求数 | 实际结果与主代理审阅 |

| --- | --- | --- |

| [首轮](acceptance/live-20261020.json) | 14 | calculator 0.3；软阈值 partial；真实 todo 实写 1 条。研究 budget_exhausted，摘要把未执行推成待执行，todo 回答虚写 changed 字段，均保留问题 |

| [首次后续](acceptance/live-followup-20261020.json) | 8 | SQLite 官方 FAQ 原文引文通过，但本轮 search unavailable、URL 由模型直接提出；不得说选自实际搜索结果。修订摘要保留日期/未支付/不删除/只读且无虚增工作；真实提醒创建/查询通过 |

| [压缩后续问](acceptance/live-continuity-20261020.json) | 4 | 真实摘要后四项合成事实正确，calculator 得出 12.5，原文保留，无付款或待办写入；共享预算 4 模型/1 工具，其中压缩 1 |

| [新搜索闭环尝试](acceptance/live-research-search-20261021.json) | 2 | 模型确实 search 后从返回结果选中 Python 官网，读取因 gzip 被拒绝而失败 |

| [gzip 修正后尝试](acceptance/live-research-gzip-20261021.json) | 2 | 一次响应两次搜索使旧工具预留阈值过早收尾，无已读引文；模型明确说尚未核实，不能标通过。阈值随后修正并离线验证 |

| [已有真实搜索结果续接](acceptance/live-research-continuation-20261021.json) | 2 | 模型复用实际已取得的公开结果选官方站，实际读取 gzip 正文并返回有位置的逐字引文，明确来源与时效限制；这是续接核验，不是全新搜索闭环重跑 |



报告中自动字段 semantic_review=pending 保留原值；上表为主代理实际读过输出后的审阅，不伪造独立 reviewer 或用户已验收。todo 的真实状态通过与回答字段表述缺陷分开；提示词已纠正，但没有另花请求重复 todo 语义验收。



关键结论：真实调用、业务写入、摘要续问和研究取证都有实际证据；新搜索→读取→最终输出的一次全新连续实网复验仍有上述缺口，不能用分段结果抹去失败。没有总体准确率或费用“已全部可靠”的结论。



各报告含逐次 provider usage 和耗时：本批输入 72739、输出 13485、合计 86224 token，模型 HTTP 调用耗时合计 81.045 秒，不包括搜索/抓取等等待。token 是 provider 返回值，不等于窗口预算或准确账单；没有账户计费明细，未换算人民币、未猜测缓存折扣。[汇总](acceptance/live-summary-20261021.json) 保留成功与失败口径。



## 公开搜索与网页



[固定搜索实测](acceptance/search-guarded-20261021.json) 返回 Python 官网等实际链接；[gzip 正文](acceptance/python-gzip-read-20261021.json) 完整取得 Python 官网正文并返回有位置片段；[SQLite 网页](acceptance/public-web-20261020.json) 取得 24428 字符正文和 2000 字符预览。



搜索可用性和相关性波动很大：原 DDGS Bing 引擎 disabled，隐式 auto 会转其他引擎；Google/Yahoo/Mojeek/Startpage 未取得结果，Brave 429、DuckDuckGo 校验页均未绕过。Bing 的部分 SQLite 查询返回明显无关内容，属于语义失败；新增粗筛会拒绝完全无查询词的内容，但不会保证所有通过结果都相关。所有诊断保留于 acceptance，不把 HTTP 200 当查询正确。



网页使用已解析的公开 IP、原 Host/SNI、逐跳重查和 TLS 校验。支持受输出上限限制的 gzip/deflate；不支持的编码、拼接压缩流、内网、非标准端口和 URL 凭据拒绝。没有关闭 TLS、解除 SSRF 门或访问私人文件。



## 浏览器与通知



真实 Edge 无头浏览器两项覆盖：



1. 双会话切换/刷新、Plan/Build、定向确认、真实待办服务、会话通知及已读、390 宽布局、移动导航开关、零页面错误。

2. 运行中普通消息排队、Stop 后不启动队列、定向继续后恢复及有序处理，原活动上下文不夹入排队消息。



稳定截图：[桌面](acceptance/desktop-20261020.png)、[移动](acceptance/mobile-20261020.png)。浏览器内使用脚本模型，不冒充真实模型语义。



[实机观察](acceptance/desktop-observation-20261020.json)：原发送 API accepted；用户明确没有看到。ToastEnabled=0，原生 setting=DISABLED_FOR_USER。新适配器发送前检查，系统禁用时记录 disabled，会话未读仍保留。未修改全局通知设置，未证明点击能打开对应会话。后台退出/休眠不承诺准时。



## 主代理自审



| 检查项 | 结论 |

| --- | --- |

| 需求满足 | 代码覆盖已确认范围；Windows 人工门未过，真实研究全新连续复验缺口明确保留 |

| 边界 | 离线故障/恢复/并发/预算/压缩/SSRF/两会话与真实浏览器已执行 |

| 安全 | 凭据不入源码/日志；本机身份/CSRF/受控引用/精确动作守卫；仅限实际检查范围 |

| 规范 | Python 自写 Runtime，复用基础库，无通用执行器、递归开发委派或额外平台 |

| 可回退 | 原交接/旧产物保留；原两表版本不变，未覆盖原正式数据库，未执行删除或 Git |



**代码与适用离线检查：READY。完整实机验收：NOT READY，Windows 显示/点击尚未通过。** 后续若复验新搜索研究闭环，需要明确新的有限调用批次，不能沿用已耗尽的 32 次记录。提交/推送 GitHub 未执行，不伪造仓库链接。



最后收尾：后台健康检查确认真实模式、原工作会话保留、静态资源与最终源码一致，未触发模型调用。两库只读 integrity_check 均为 ok。34 份 Markdown / 186 个本地链接审计无问题；原始结果分别为 acceptance/running-app-20261021.json 和 acceptance/document-audit-20261021.json。
