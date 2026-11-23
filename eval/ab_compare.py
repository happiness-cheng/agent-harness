"""门开 / 门关 A/B 对比：同一批题、同一个模型，各跑 N 次，比较成功率差。

为什么不用“被拦率”和“任务没做完”：模型被拦后会绕路（C7 里 sed 改成
grep），任务最后不失败，但代价体现在额外调用和输出差异上——“0/10 任务
损失”测不到。正确做法是门开和门关各跑，比较成功率差，再记被拦后绕路率
和额外调用数（EVAL-DIAG-006，Claude 第六轮审批）。
用法：
  PYTHONPATH=src .venv-eval/bin/python -m eval.ab_compare --provider qianwen \
      --tasks C1 C2 C2b C3 C4 C5 C6 C7 C8 C9 C10 --repeat 5 --temperature 0.7
  已有两批报告时只比较，不重跑：
  PYTHONPATH=src .venv-eval/bin/python -m eval.ab_compare \
      --on-dir eval/runs-real/<门开那批> --off-dir eval/runs-real/<门关那批>
注意：跑满 11 题 × 5 次 × 2 组约 130 万 tokens，跑之前先确认额度和解冻线
（DECISION_LOG → EVAL-DIAG-006：10% 需用户确认，至少 30 个任务次数才算数）。
"""
from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _run_arm(provider: str, tasks: list[str], repeat: int, temperature: float,
             token_cap: int, gate_off: bool) -> Path:
    """跑一组（门开或门关），返回报告目录。"""
    cmd = [sys.executable, "-m", "eval.run_real", "--provider", provider,
           "--tasks", *tasks, "--repeat", str(repeat),
           "--temperature", str(temperature), "--token-cap", str(token_cap)]
    if gate_off:
        cmd.append("--gate-off")
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1])
    print(proc.stdout[-2000:])
    if proc.returncode != 0:
        print(proc.stderr[-2000:], file=sys.stderr)
        raise SystemExit(f"跑{'门关' if gate_off else '门开'}组失败，退出码 {proc.returncode}")
    # 报告目录是 run_real 打印的最后一行“报告：<目录>”。
    for line in reversed(proc.stdout.splitlines()):
        if "报告：" in line:
            return Path(line.split("报告：", 1)[1].strip())
    raise SystemExit("没找到 run_real 的报告目录")


def _load(run_dir: Path) -> dict:
    return json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))


def _compare(on: dict, off: dict) -> str:
    """比较两批 summary，返回 A/B 报告正文。"""
    from collections import defaultdict
    on_runs: dict[str, list[dict]] = defaultdict(list)
    off_runs: dict[str, list[dict]] = defaultdict(list)
    for r in on["results"]:
        on_runs[r["task_id"]].append(r)
    for r in off["results"]:
        off_runs[r["task_id"]].append(r)
    lines = ["# 门开 / 门关 A/B 对比", "",
             f"模型：{(on.get('model_resolved') or {}).get('model', '?')}　"
             f"温度：{(on.get('model_resolved') or {}).get('temperature', '?')}",
             f"门开组：{on['total_runs']} 次运行，通过 {on['passed']}　"
             f"门关组：{off['total_runs']} 次运行，通过 {off['passed']}",
             ""]
    on_rate = on["passed"] / on["total_runs"] if on["total_runs"] else 0
    off_rate = off["passed"] / off["total_runs"] if off["total_runs"] else 0
    lines.append(f"成功率：门开 {on_rate:.1%}　门关 {off_rate:.1%}　差 {on_rate - off_rate:+.1%}（负数表示门拖低了成功率）")
    lines.append("")
    # 每题：成功率、平均调用数。
    lines.append("| 题 | 门开通过 | 门关通过 | 门开平均调用 | 门关平均调用 | 额外调用 |")
    lines.append("|---|---|---|---|---|---|")
    total_extra = 0
    for task_id in sorted(set(on_runs) | set(off_runs)):
        o, f = on_runs.get(task_id, []), off_runs.get(task_id, [])
        o_pass = sum(1 for r in o if r["verdict"] == "PASS")
        f_pass = sum(1 for r in f if r["verdict"] == "PASS")
        o_calls = sum(r["tool_calls"] for r in o) / len(o) if o else 0
        f_calls = sum(r["tool_calls"] for r in f) / len(f) if f else 0
        total_extra += (o_calls - f_calls) * len(o)
        lines.append(f"| {task_id} | {o_pass}/{len(o)} | {f_pass}/{len(f)} | "
                     f"{o_calls:.1f} | {f_calls:.1f} | {o_calls - f_calls:+.1f} |")
    lines.append("")
    lines.append(f"额外调用总数（门开组比门关组多调的次数）：约 {total_extra:.0f} 次")
    lines.append("")
    # 绕路率（门开组）：被拦过的题里，最后通过的有多少。
    blocked_tasks = [r for r in on["results"] if (r.get("gate") or {}).get("gate_trigger_count", 0) > 0]
    bypassed = [r for r in blocked_tasks if r["verdict"] == "PASS"]
    lines.append(f"绕路：门开组里被拦过的运行 {len(blocked_tasks)} 次，其中最后通过（绕过去了）{len(bypassed)} 次，"
                 f"绕路率 {len(bypassed) / len(blocked_tasks):.1%}" if blocked_tasks else "绕路：门开组里没有被拦过的运行。")
    lines.append("")
    # 被拦的命令（按任务意图手工标：任务必需 / 可替代 / 多余）。
    lines.append("## 被拦的命令（请按任务意图手工标：任务必需 / 可替代 / 多余）")
    lines.append("")
    for r in on["results"]:
        gate = r.get("gate") or {}
        for command in gate.get("blocked_commands", []):
            lines.append(f"- [{r['task_id']}]（{r['verdict']}）`{command[:200]}`")
    lines.append("")
    lines.append("注：被拦次数按规则分（该拦/误拦）不等于按任务意图分。`rm -r scratch` 如果是任务本身"
                 "要求删的，对用户来说就是被误拦，只是按设计该拦——标的时候按任务意图，不按规则。")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="qianwen", choices=["groq", "gemini", "qianwen"])
    parser.add_argument("--tasks", nargs="*", default=None)
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--token-cap", type=int, default=150_000)
    parser.add_argument("--on-dir", default=None, help="已有的门开组报告目录，只比较不重跑")
    parser.add_argument("--off-dir", default=None, help="已有的门关组报告目录，只比较不重跑")
    args = parser.parse_args(argv)
    if args.on_dir and args.off_dir:
        on_dir, off_dir = Path(args.on_dir), Path(args.off_dir)
    else:
        if not args.tasks:
            print("请用 --tasks 指定同一批题。", file=sys.stderr)
            return 2
        print("=== 门开组 ===")
        on_dir = _run_arm(args.provider, args.tasks, args.repeat, args.temperature,
                          args.token_cap, gate_off=False)
        print("=== 门关组 ===")
        off_dir = _run_arm(args.provider, args.tasks, args.repeat, args.temperature,
                           args.token_cap, gate_off=True)
    on, off = _load(on_dir), _load(off_dir)
    report = _compare(on, off)
    out_dir = (Path(__file__).resolve().parent / "runs-ab" /
               datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H-%M-%S+00-00"))
    out_dir.mkdir(parents=True)
    (out_dir / "ab_report.md").write_text(report, encoding="utf-8")
    (out_dir / "ab_summary.json").write_text(
        json.dumps({"on_dir": str(on_dir), "off_dir": str(off_dir),
                    "on_passed": on["passed"], "on_total": on["total_runs"],
                    "off_passed": off["passed"], "off_total": off["total_runs"]},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(report)
    print(f"\n报告：{out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
