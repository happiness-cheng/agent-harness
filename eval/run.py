"""命令行入口：python -m eval.run [--task C1 S1] [--repeat 3] [--out eval/runs]

仓库根目录下跑，PYTHONPATH=src（和 tests 的用法一致）。
不联网、不调模型、不读凭据。Windows 上 bash 走 Git Bash，和 generic.py 一致；
这台 Linux 云电脑上走系统 bash，Windows 专属行为标 NOT RUN。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from .runner import run_suite  # noqa: E402
from .tasks import BYPASS_TASKS, CLAUDE_TASKS, E_TASKS, F_TASKS, G_TASKS, TASKS  # noqa: E402


def _report_md(summary: dict) -> str:
    lines = [
        "# 通用四工具评测 v0.1（脚本化层）",
        "",
        f"时间：{summary['started_at']}　重复：{summary['repeat']} 次/题　"
        f"通过：{summary['passed']}/{summary['total']}",
        "",
        "## 分类",
        "",
    ]
    for category, bucket in summary["by_category"].items():
        lines.append(f"- {category}：{bucket['passed']}/{bucket['total']}")
    schema = summary["schema"]
    lines += ["", "## Schema 体积（实测，不是估计）", ""]
    if schema.get("generic"):
        lines.append(f"- 通用 4 工具：{schema['generic']['bytes']} 字节，约 "
                     f"{schema['generic']['approx_tokens']} tokens")
        lines.append(f"- 专用文件族 7 工具：{schema['specialized_fs']['bytes']} 字节，约 "
                     f"{schema['specialized_fs']['approx_tokens']} tokens")
        lines.append(f"- {schema['note']}")
    else:
        lines.append(f"- schema 对比失败：{schema.get('error')}")
    lines += ["", "## 逐题", "", "| 题 | 分类 | 结果 | 工具调用 | 上下文字节 | 决策提示 |",
              "| --- | --- | --- | --- | --- | --- |"]
    for r in summary["results"]:
        lines.append(f"| {r['task_id']} {r['title']} | {r['category']} | {r['verdict']} "
                     f"| {r['median_tool_calls']} | {r['median_context_bytes']} | {r['decision_if']} |")
    lines += ["", "## 失败明细", ""]
    failed = [r for r in summary["results"] if r["verdict"] == "FAIL"]
    if not failed:
        lines.append("无失败。")
    for r in failed:
        lines.append(f"### {r['task_id']} {r['title']}（预期 {r['expected']}）")
        for run in r["runs"]:
            for c in run["checks"]:
                if c["verdict"] == "FAIL":
                    lines.append(f"- {c['id']}：{c['reason']}")
            lines.append(f"- 工作区（跑完未删，可回放）：{run['workspace']}")
    lines += ["", "## 诚实声明", "", summary["honesty_note"], ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="四通用工具评测 v0.1（脚本化层）")
    parser.add_argument("--task", nargs="*", default=None, help="只跑这些题，如 --task C1 S1")
    parser.add_argument("--repeat", type=int, default=1, help="每题跑几次，默认 1")
    parser.add_argument("--list", action="store_true", help="只列任务，不跑")
    parser.add_argument("--out", default=None, help="输出目录，默认 eval/runs/<时间>")
    parser.add_argument("--bypass", action="store_true",
                        help="跑独立绕过集 B1–B13，不跑 22 题验收样本")
    parser.add_argument("--claude", action="store_true",
                        help="跑 Claude 的独立题集 D1–D9、D11、D12（D10 改手跑），不跑 22 题验收样本")
    parser.add_argument("--e", action="store_true",
                        help="跑 Claude 第四轮的 E 集 E1–E14，不跑 22 题验收样本")
    parser.add_argument("--f", action="store_true",
                        help="跑 Claude 第五轮的 F 集 F1–F6，不跑 22 题验收样本")
    parser.add_argument("--g", action="store_true",
                        help="跑 Claude 第六轮的 G 集 G1–G2，不跑 22 题验收样本")
    args = parser.parse_args(argv)
    if args.list:
        for task in TASKS:
            print(f"{task['id']:4} {task['category']:12} {task['title']}")
        return 0
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    suite = (G_TASKS if args.g else
             F_TASKS if args.f else
             E_TASKS if args.e else
             CLAUDE_TASKS if args.claude else (BYPASS_TASKS if args.bypass else None))
    summary = run_suite(args.task, repeat=args.repeat, tasks=suite)
    summary["started_at"] = started
    out = Path(args.out) if args.out else Path(__file__).resolve().parent / "runs" / started.replace(":", "-")
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(_report_md(summary), encoding="utf-8")
    print(_report_md(summary))
    print(f"\n报告已写到：{out / 'report.md'}")
    # 退出码：有 FAIL 就非零，方便接 CI；但 S4/S5/S7 是预期 FAIL（暴露缺口），
    # 看报告时不要只看退出码，要看“预期 FAIL 还是意外 FAIL”。
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
