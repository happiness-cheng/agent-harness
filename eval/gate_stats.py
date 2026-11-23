"""全量误拦统计：读一次真实模型跑的 summary.json，数门拦了多少命令。

回答“门碍不碍事”：被拦的 bash 命令数 / bash 总调用数；每题的 verdict
和门触发次数对照。“合法还是该拦”由人判——脚本只列出被拦的命令和
上下文，不替人定性；“任务失败是不是门造成的”也要人看检查失败的原因。
用法：PYTHONPATH=src .venv-eval/bin/python -m eval.gate_stats eval/runs-real/<目录>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print("用法：python -m eval.gate_stats eval/runs-real/<目录>")
        return 2
    run_dir = Path(args[0])
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    total_bash = 0
    blocked = 0
    blocked_cmds: list[tuple[str, str]] = []
    for result in summary["results"]:
        task_id = result["task_id"]
        gate = result.get("gate") or {}
        blocked += gate.get("gate_trigger_count", 0)
        for command in gate.get("blocked_commands", []):
            blocked_cmds.append((task_id, command))
        for step in result.get("steps", []):
            if step.get("tool") == "bash":
                total_bash += 1
    print(f"模型：{summary.get('model_resolved', '?')}　温度：{summary.get('temperature', '?')}")
    if total_bash:
        print(f"bash 总调用：{total_bash}　被门拦住：{blocked}　"
              f"拦住占比：{blocked / total_bash * 100:.1f}%")
    else:
        print("bash 总调用：0")
    print("每题 verdict / 门触发次数：")
    for result in summary["results"]:
        gate = result.get("gate") or {}
        print(f"  {result['task_id']}: {result['verdict']} / "
              f"门触发 {gate.get('gate_trigger_count', 0)} 次")
    print(f"\n被拦的命令（{len(blocked_cmds)} 条，请人工判合法还是该拦）：")
    for task_id, command in blocked_cmds:
        print(f"  [{task_id}] {command[:160]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
