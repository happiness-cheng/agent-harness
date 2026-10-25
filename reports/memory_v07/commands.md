# 实际执行与复现命令

工作目录：`C:/Users/陈独秀/.codex/tmp/adrian-memory-v07`。

```powershell
$env:PYTHONPATH='src'
$py='C:/Users/陈独秀/Documents/agent-harness/.venv/Scripts/python.exe'
& $py -X utf8 -m pytest -q
# full-tests.txt: 1281 passed, 3 deselected, 1 warning in 61.77s
& $py -X utf8 -m pytest -q tests/unit/test_active_budget_v07.py tests/unit/test_active_loop_v07.py tests/unit/test_fact_relations_v07.py
# p0-p1-final.txt: 22 passed
& $py -X utf8 -m pytest -q tests/unit/test_active_trial_v07.py
# trial-green.txt: 2 passed
& $py -X utf8 -m pip check
# dependency-check.txt: No broken requirements found.
& $py -X utf8 -m compileall -q experiments/memory_v07 scripts/eval_memory_active_v07.py scripts/diagnose_memory_budget_v07.py
# exit 0; 项目未配置独立type/lint命令，分别SKIP，不冒充通过。
& $py -X utf8 -m pip wheel --no-deps --no-build-isolation --wheel-dir dist .
# build.txt: Successfully built agent-harness
& $py -X utf8 scripts/diagnose_memory_budget_v07.py --output reports/memory_v07/v06-budget-breakdown.json
# 15 cases, cumulative_block_proved 15, provider_calls 0
& $py -X utf8 scripts/control_memory_active_v07.py reports/memory_v07/p0-control.json
# old partial, new completed, new_provider_calls 3, external_requests 0
& $py -X utf8 scripts/eval_memory_active_v07.py prepare --output reports/memory_v07/prepared.json.gz
# 3 cases, max_requests 12, fee_cap .50; 哈希见delivery-receipt.json
git -c core.whitespace=cr-at-eol diff --cached --check -- '*.py' '*.md'
# exit 0；早期失败日志原样保存，不以重排日志消除失败证据。
```

诊断、prepare、control使用独占创建。复现时将输出改为未存在的 `*-replay.json` / `*-replay.json.gz`，不要删除旧结果。

基线测试输出保留 `baseline-tests.txt`；失败到通过的开发记录保留 `*-red.txt` 和后续通过日志。

真实run **尚未执行**，不要直接运行以下模板；必须先收到本轮明确授权并生成匹配的授权记录：

```powershell
& $py -X utf8 scripts/eval_memory_active_v07.py run `
  --pack reports/memory_v07/prepared.json.gz `
  --output reports/memory_v07/live.json `
  --authorization reports/memory_v07/authorization.json --allow-live `
  --credentials-file 'C:/Users/陈独秀/agent-harness-credentials.json' `
  --proxy http://127.0.0.1:7890
```

费用仅按已核查的官方峰值费率保守估算；不得把Mock的usage当作真实费用或把预留当作实际扣款。
