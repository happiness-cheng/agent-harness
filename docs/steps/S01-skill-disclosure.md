# S01：Skill加载机制与渐进式披露验证

日期：2026-10-22。[完整提示、Skill、目录、真实输入diff、问题/回答与不足](S01-readable.md)。

## 结论

当前有“两级模型上下文披露”：目录先可见，read_skill后才追加所选正文。离线真实应用探针已验证；真实模型在3个相关合成场景各选择并读取对应Skill，简单计算不读，总计7/8次授权请求，未用完的1次不继续消耗。

不等于完整Claude Code能力：磁盘上仍提前读所有Skill正文取首行；目前仅平铺*.md目录，没有SKILL.md目录/附属文件加载；同一Skill重复读取仍追加全文。未测长正文压缩、重注入、目录规模或热更新。

## 参考与差异

Claude Code官方[Control who invokes a skill](https://code.claude.com/docs/en/skills#control-who-invokes-a-skill)说明普通默认会话先呈现描述，调用才加载正文；预加载子Agent等模式是例外。[Add supporting files](https://code.claude.com/docs/en/skills#add-supporting-files)说明正文可导航到按需读取的附属资料。[Skill content lifecycle](https://code.claude.com/docs/en/skills#skill-content-lifecycle)说明内容留在会话，同内容重复调用用简短通知，压缩时另有保留规则。只借鉴公开行为，不声称知道其全部内部代码。

| 层级 | 当前实现 | 验证 |
| --- | --- | --- |
| 名称/描述 | summaries取文件名+第一行，放read_skill工具description | 首次模型输入可见3条，无正文标记 |
| Skill正文 | 真实read_skill返回字符串，Runtime按工具结果追加 | 读取A只出现A，读取B后A/B保留 |
| 附属资料 | 没有目录格式和资源读取接口 | 源码只glob根目录*.md；本轮未实现 |
| 生命周期 | 会话历史保留，重复读重复追加 | 同会话A两份；新会话无正文 |

当前代码：src/min_agent/skills.py的summaries/read，以及application.py的_registry注册read_skill。目录描述仅取标题，不能等同完整的触发条件；但本批三个短标题足以让模型选对，不能推断所有Skill都会如此。

## 方案与执行

采用复用现有实现+实验探针，不重写Skill引擎（Extend现有验证能力）。完整复制Claude Code的目录、参数替换、动态脚本、子Agent及权限扩展超出本次一点，暂不做。

离线先检查实际加载，再由作者独立授权最多8次真实请求，只允许临时Skill的只读read_skill。真实入口每场景最多2次，全局8次硬上限；不自动重试、不注册业务工具、不提供网络工具。这里的“只读”指不操作业务数据；应用探针在临时SQLite记录合成会话。

现有项目只有public-evidence一份Skill。另两份为本轮自写合成润色/故障Skill，临时加载，不安装到个人目录，不改生产提示或生产注册。

真实测试保持现有系统提示，未增加必须使用Skill的指令；只提供read_skill以隔离机制，其选择率不能外推到生产17工具全集。每场景一次，未设seed，不估算总体准确率。Skill原则与系统提示或模型常识重叠，没有不读Skill的对照，因此不能证明内容使回答质量提升。

## 实际结果与不足

- 润色：读writing-polish后回答，事实保留；但原句重复和额外建议偏多。
- 故障：读debug-feedback后回答，但500必有异常堆栈等断言过强，观察与因果推断混淆；回答长。
- 核验：读public-evidence后保留条件与冲突；但B为旧状态“更可能”无依据，核验清单过度扩展。
- 计算：直接答2，未请求Skill。

四个最终回答均stop，无length。正文只在读取后进入模型输入；不输出测试正文标记并不表示没利用，反过来也不能凭读取证明遵循。原生提供商状态仅在内存回传，公开报告删除该字段，正文、工具参数和结果未改写。

usage：{'input_tokens': 16553, 'output_tokens': 4301, 'total_tokens': 20854}。不是费用或性能结论。

## 验证

新增真实实验入口前先跑5个离线测试，均因入口尚不存在报setup错误；补入口后5 passed。后续增强未同意/已有报告时禁止读凭据的断言，并保留硬请求上限、逐轮正文披露、工具白名单、首错停止验证。

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/audit_skill_disclosure.py --report '<新离线报告路径>'
.\.venv\Scripts\python.exe -X utf8 -m py_compile scripts/audit_skill_disclosure.py scripts/eval_skill_disclosure.py tests/unit/test_skill_disclosure_eval.py
.\.venv\Scripts\python.exe -X utf8 -m pytest -q tests/unit/test_skill_disclosure_eval.py tests/unit/test_context_lifecycle.py tests/unit/test_runtime_reply.py tests/unit/test_llm_adapter.py tests/unit/test_deepseek_adapter.py tests/unit/test_llm_smoke_guard.py tests/integration/test_application_flow.py
```

本机相关测试102 passed,1 warning in 2.46s；公开worktree101 passed,1 warning in 2.26s。差1项来自作者已有test_context_lifecycle改动，未把该改动复制过去。均有已有TestClient/httpx弃用提示。公开worktree重新执行离线应用探针同样通过全部6项检查与7次输入断言。真实调用只在本机当前工作区版本执行；原生状态记录清理及调用门增强后还要重跑受影响的5项测试，结果后补。

类型/Lint SKIP（项目未配置）；安装构建、全量应用回归与长期上下文测试SKIP。脚本语法、相关测试、敏感内容与diff检查按实际结果交付。主代理自审，不调用子代理。

## 下一步建议：一次只做一个点

优先把目录中的“何时使用”描述变成独立元数据，并验证生成目录不必读取全部正文；本次不自动实现。随后再逐项考虑相同内容重复加载提示、附属资料按需读取和压缩后的生命周期。真实能力验证应增加含糊/相似/不相关描述与更大目录，不能以本批3个正例认定机制全面成熟。


### 最后复核与自审

记录清理及调用门增强后，重跑受影响5项：本机5 passed in 1.03s；公开worktree5 passed in 1.02s。两个位置语法检查均通过，不重复消耗模型额度。自动核对：7请求、4场景；目录Schema与离线真实应用注册一致；首次输入没有正文标记；读取结果与文件全文一致；四份最终回答原样出现在阅读版；原生provider_state未进入公开请求记录；当前提示与实验快照逐字一致。

主代理自审：PASS（仅实验与证据交付，不等于回答质量通过）。需求范围、请求硬上限、按场景最多两次、工具白名单、保留报告和错误停止均已检查；无生产代码/提示/数据库/权限改动。基础密钥模式扫描与git diff检查执行；不是完整安全审计。原始脚本模型答复不是语义效果证据，单次真实样本不是成功率。

本机实验所用源码SHA-256（用于标识含作者已有改动的实际版本，不将未提交代码一起发布）：

- src/min_agent/skills.py: 26d7dcaa0258f3e4f560a3689be26a3d75ed711afc83b8f437ecf9ef7b95e909
- src/min_agent/application.py: aa14ebe5699c2223092f7443e9056d9d72ed423a80cd6a61a48ba8053d25fee4
- src/min_agent/runtime.py: 980623764d41ffd7f877e7febf993cb725bf2879c3302bd3a51f7f3287afa1e9
- src/min_agent/context.py: 2411f4c5931f30561ab40b25c5b69a0d5387d92999915b5c1b1ae041a489f3fd
