# M02：范围、代码与交付自审

审查者：主代理；未调用子代理。Verdict：本轮离线评测包PASS；生产Memory与真实模型质量未验收。

| 检查项 | 结果 | 依据 |
| --- | --- | --- |
| 范围 | PASS | 只新增评测脚本、合成数据、测试、材料；无生产Memory/Runtime/依赖修改 |
| 输入与gold分离 | PASS | component_input只提供历史与问题；独立评分；未来历史不入库；跨用户历史分别入库 |
| 真实反馈回路 | PASS | 先观察缺入口/接口失败；评分误判和空集问题均有先失败再修正的回归 |
| 用户/会话隔离 | 局部机制PASS | 正常召回阳性、原文跨会话与跨用户拒读；不代替未来跨会话索引的隔离测试 |
| 评分边界 | PASS | 空精度为null，召回与库库存分开；硬泄漏与噪声分开；旧证据不等同旧事实被误用 |
| 数据与外部请求 | PASS | 全部自建样本合成；无外部模型、无凭据读取；参考仓库只读审计 |
| 手动真实使用 | PASS | CLI实际生成96条基线、4控制、参考60例清单；主工作区复跑汇总一致 |
| 隔离分支回归 | PASS | 1024 passed, 3 live deselected, 1既有弃用警告 |
| 主工作区回归 | 存在已知失败 | 新评测器+Memory相关测试43 passed、原有长原文读取1 failed；未修复或隐瞒 |
| 语法/类型/lint | 语法PASS，后两项SKIP | py_compile通过；mypy/ruff未配置且未安装 |
| 证据与副作用 | PASS | 基线x模式新建，控制SQLite新建，不覆盖业务数据库；旧v1保留；不删除文件或历史 |

发现并修复：初版将所有forbidden_evidence当硬泄漏，使无关午餐、助手猜测和撤销偏好旧文被过重计罚。新增hard_forbidden_evidence及回归，v2重新运行；公开保留v1，不将评价方法变更算成系统改进。

已知限制：公开小样本；固定检索词；单段历史统一可用时点；消息级来源粒度；当前遗忘只测可用材料不测物理擦除；真实写入语义、模型查询策略、上下文提供、回答及裁判未执行。源码审计只固定清单与版本，不运行书中在线客户端，不证明参考集外部业务规则正确。

实际验证命令：

```powershell
python -X utf8 -m py_compile scripts/eval_memory.py tests/unit/test_memory_evaluation.py
python -X utf8 -m pytest -q
python -X utf8 scripts/eval_memory.py --report docs/acceptance/m02-memory-baseline-v2.json --control-dir C:/Users/陈独秀/.codex/tmp/m02-control-v2 --book-root C:/Users/陈独秀/.codex/tmp/ai-agent-book-memory-reference
```

主工作区另以其PYTHONPATH运行新评测器及test_memory_state.py、test_structured_memory.py、test_memory_tools.py，43通过1失败；单独CLI生成m02-memory-baseline-main-v2.json，96条离线结果与4控制均实跑。

提交前按文件清单暂存并运行git diff --cached --check及敏感数据/范围检查；上传结果以实际Git输出为准，不提前宣称远端成功。
