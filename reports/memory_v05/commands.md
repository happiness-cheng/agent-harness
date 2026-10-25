# 实际执行命令与输出位置

工作目录：`C:/Users/陈独秀/.codex/tmp/adrian-memory-v05`。PowerShell，Python为原项目现有虚拟环境。以下命令已实际运行；输出文件已存在时运行器拒绝覆盖。报告与分析复现应指定新输出目录。

```powershell
$env:PYTHONPATH='src'
$py='C:/Users/陈独秀/Documents/agent-harness/.venv/Scripts/python.exe'
& $py -X utf8 -m pytest -q --tb=short
```

基线输出`baseline-tests.txt`：1211 passed，3 deselected。新增测试后的`full-regression.txt`：1231 passed；最终`verification-final.txt`再次1231 passed，3 deselected，1 warning，61.07秒。3个live测试默认未运行；单独的本轮27请求以指定CLI运行，不把它们伪装成pytest live测试通过。

定向测试命令：

```powershell
& $py -X utf8 -m pytest -q tests/unit/test_memory_answer_eval_v05.py tests/unit/test_memory_answer_run_v05.py tests/unit/test_memory_answer_scoring_v05.py
```

结果：20 passed。初次RED日志为模块尚未创建导致的导入失败，保留于`prepare-red.txt`及`runner-red.txt`，不冒充生产旧bug的行为复现。

唯一一次真实批次：

```powershell
& $py -X utf8 scripts/eval_memory_value_v05.py run --pack reports/memory_v05/prepared-pilot.json.gz --output reports/memory_v05/live-results.json --allow-live --authorization reports/memory_v05/authorization.json --credentials-file C:/Users/陈独秀/agent-harness-credentials.json --proxy http://127.0.0.1:7890
```

输出：`model_calls=27`。日志见`live-results.jsonl`，授权占用见`authorization.claimed.json`。27是本轮授权总数，不能删除claim重跑；后续命令均不调用模型。

```powershell
& $py -X utf8 scripts/eval_memory_value_v05.py report --pack reports/memory_v05/prepared-pilot.json.gz --output reports/memory_v05/unreviewed-live-report.json --results reports/memory_v05/live-results.json
& $py -X utf8 scripts/analyze_memory_value_v05.py --output-dir reports/memory_v05/final-audit
& $py -X utf8 scripts/eval_memory_value_v05.py report --pack reports/memory_v05/prepared-pilot.json.gz --output reports/memory_v05/evaluation-detail.json --results reports/memory_v05/live-results.json --reviews reports/memory_v05/final-audit/source-reviews.json
& $py -X utf8 scripts/analyze_memory_value_v05.py --output-dir reports/memory_v05/reproduction-check
```

最终审查26份有效回答，非法JSON一份NOT_RUN。final-audit与reproduction-check的source-reviews、source-stage-diagnostics、paired-analysis、prompt-and-protocol四个文件全部逐字节相同。

```powershell
& $py -X utf8 -m pip check
& $py -X utf8 -m pip wheel --no-deps --no-build-isolation --wheel-dir reports/memory_v05/wheels .
& $py -X utf8 scripts/verify_memory_value_v05.py --book-root C:/Users/陈独秀/.codex/tmp/ai-agent-book-memory-reference --output reports/memory_v05/verification.json
git -c core.whitespace=cr-at-eol diff --check
```

构建成功，pip check为No broken requirements found。核验286旧文件、60题数据、69项制包源码哈希及已批准包哈希；10个新增/本轮Python文件语法编译成功。Wheel SHA256为`ce934e238d6ad61569b45a4a4ed513b2998ec36aa67e02f489cc1a4f7c1dbbdf`。

一个临时检查曾错误地按ISO字符串字典序比较时间（Z对+00:00），触发假阳性断言；定位后改用datetime解析重新核验，所有来源均合法。这不是Memory数据未来泄漏，也没有改动历史时点。`verify_memory_value_v05.py`保留正确的时间比较。

静态类型检查、独立Lint没有项目配置，NOT_RUN。外部Judge、额外模型调用、私人数据测试、远端推送、生产数据库迁移均NOT_RUN。
