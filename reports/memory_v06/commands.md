# v06实际命令与复现

PowerShell工作目录`C:/Users/陈独秀/.codex/tmp/adrian-memory-v06`。本轮使用已有虚拟环境，不安装新依赖。命令已实际执行，原始日志保留。

```powershell
$env:PYTHONPATH='src'
$py='C:/Users/陈独秀/Documents/agent-harness/.venv/Scripts/python.exe'
& $py -X utf8 -m pytest -q --tb=short
```

基线`baseline-tests.txt`：1231 passed，3 deselected。第一轮`full-regression.txt`：1256 passed。客户端配置修复后的`final-regression.txt`：1257 passed，3 deselected，1个既有警告，95.33秒。pytest默认live测试未运行；真实请求由下面独立CLI完成。

定向命令：

```powershell
& $py -X utf8 -m pytest -q tests/unit/test_task_evidence_v06.py tests/unit/test_active_evidence_v06.py tests/unit/test_evidence_budget_v06.py
& $py -X utf8 -m pytest -q tests/unit/test_evidence_trial_v06.py
```

前者17 passed，最终后者9 passed。P1首次导入缺模块的RED之后，实际出现verify限定被丢失的行为失败，修复后4 passed；对应初稿日志保留。客户端配置测试先失败、修后通过，模拟75个假客户端不调用API；不把这些算进90次真实请求。

```powershell
& $py -X utf8 scripts/verify_memory_value_v05.py --book-root C:/Users/陈独秀/.codex/tmp/ai-agent-book-memory-reference --output reports/memory_v06/v05-receipt-check.json
& $py -X utf8 scripts/eval_memory_value_v06.py prepare --book-root C:/Users/陈独秀/.codex/tmp/ai-agent-book-memory-reference --output reports/memory_v06/prepared.json.gz
& $py -X utf8 scripts/eval_memory_value_v06.py offline --pack reports/memory_v06/prepared.json.gz --output reports/memory_v06/offline-results.json
```

15题，60个单次视图、15个脚本Runtime控制；外部请求0。脚本完成5/15，其他10个预算暂停；原样保留，不作为自然语言质量。

真实启动记录：

```powershell
& $py -X utf8 scripts/eval_memory_value_v06.py run --pack reports/memory_v06/prepared.json.gz --output reports/memory_v06/live-results.json --allow-live --authorization reports/memory_v06/authorization.json --credentials-file C:/Users/陈独秀/agent-harness-credentials.json --proxy http://127.0.0.1:7890
```

第一次本地配置失败，0请求、0费用；记录`live-results.json/.jsonl`。修复实例105上限问题并证明15题请求完全相同后：

```powershell
& $py -X utf8 scripts/eval_memory_value_v06.py prepare --book-root C:/Users/陈独秀/.codex/tmp/ai-agent-book-memory-reference --output reports/memory_v06/prepared-client-fix.json.gz
& $py -X utf8 scripts/eval_memory_value_v06.py run --pack reports/memory_v06/prepared-client-fix.json.gz --output reports/memory_v06/live-client-fix.json --allow-live --authorization reports/memory_v06/authorization-client-fix.json --credentials-file C:/Users/陈独秀/agent-harness-credentials.json --proxy http://127.0.0.1:7890
```

输出COMPLETE只表示75个实验单元已尝试完；实际90次请求、52份Schema有效最终回答，B2 15题没有最终回答。不是75题全部任务成功。原授权业务范围不变；原哈希及修复包哈希、0请求证明见`client-fix-equivalence.json`。

**上述实时命令不是离线复现步骤。授权claim已存在，不得删除claim重跑或自行消费剩余额度。** 不自动重试API请求；需要新范围时另行授权。

无需模型调用的复现：

```powershell
& $py -X utf8 scripts/audit_memory_value_v06.py --output reports/memory_v06/reproduced-source-audit.json
& $py -X utf8 scripts/report_memory_value_v06.py --pack reports/memory_v06/prepared-client-fix.json.gz --offline reports/memory_v06/offline-results.json --results reports/memory_v06/live-client-fix.json --audit reports/memory_v06/reproduced-source-audit.json --output-dir reports/memory_v06/reproduced-report
& $py -X utf8 scripts/verify_memory_value_v06.py --output reports/memory_v06/reproduced-receipt.json
```

输出已存在时拒绝覆盖，换新输出路径即可。来源审查依赖公开固定决策文件，不意味着自动程序独立证明语义正确。报告中的本地选择耗时再次复算会变化，原始真实输出和费用不变。

```powershell
& $py -X utf8 -m pip wheel --no-deps --no-build-isolation --wheel-dir reports/memory_v06/wheels .
& $py -X utf8 -m pip check
git -c core.whitespace=cr-at-eol diff --check -- '*.py' MEMORY_VALUE_V06.md
```

构建成功、依赖无损坏；原始失败日志中的空格/换行与旧草稿保持原样，差异检查不为消除告警而改写原始实验记录。Type/Lint未配置，NOT_RUN。没有自动推送、迁移生产数据库或读取私人导出。
