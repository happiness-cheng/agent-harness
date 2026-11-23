"""bashlex 当测试预言机：对同一批命令，比较 bashlex 的命令节点和手写门的切段。

不接入运行时，只用来自动找解析器 bug。bashlex 解析失败的样本直接跳过
（比如带引号的 heredoc 定界符，它解析不了）。
语料分两层：评测集里的脚本命令（样本内）+ 真实模型运行里发过的命令
（`--real`，读 eval/runs-real/*/summary.json 的 steps）；再对每条做机械
变异（`--mutate`：外面包 $( )、追加 # don't、追加 heredoc 正文、嵌套引号）。
用法：PYTHONPATH=src .venv-eval/bin/python -m eval.oracle_check [--real] [--mutate]
"""
from __future__ import annotations

import glob
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import bashlex  # noqa: E402
from min_agent.tools.generic import _real_first_index, _split_segments  # noqa: E402
from .tasks import BYPASS_TASKS, CLAUDE_TASKS, E_TASKS, F_TASKS, G_TASKS, TASKS  # noqa: E402


def bashlex_command_words(command: str) -> list[str]:
    """走 bashlex 解析树，收集每个 command 节点的首词（小写）。"""
    words: list[str] = []

    def walk(node):
        if getattr(node, "kind", None) == "command":
            parts = getattr(node, "parts", None) or []
            if parts and hasattr(parts[0], "word"):
                words.append(parts[0].word.lower())
        for _key, value in vars(node).items():
            if isinstance(value, list):
                for child in value:
                    if hasattr(child, "kind"):
                        walk(child)
            elif hasattr(value, "kind"):
                walk(value)

    for top in bashlex.parse(command):
        walk(top)
    return words


def gate_first_words(command: str) -> list[str]:
    """手写门的切段首词（跳过 do/then 等关键字后的实词，小写）。"""
    segments, _ok, _expansions = _split_segments(command)
    words: list[str] = []
    for segment in segments:
        _idx, first = _real_first_index(segment.split())
        if first is not None:
            words.append(first)
    return words


def _normalize(words: list[str]) -> Counter:
    """把两边的首词归一化成“命令”再比，避免 token 层面的假差异：
    - for 是复合结构的前导，不是命令；
    - x=、x=$(...) 这类赋值不是命令（bashlex 会把整个赋值当一个词）；
    - 段尾粘着的 )"（比如 ls)"）剥掉，只留命令名。
    归一化只影响比较，不改门本身。"""
    import re
    out: Counter = Counter()
    for word in words:
        word = word.rstrip(')"')
        if word == "for":
            continue
        if word.endswith("=") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", word):
            continue
        if word:
            out[word] += 1
    return out


def corpus() -> list[tuple[str, str]]:
    """评测集里所有脚本命令 + 几个手写边角。"""
    out: list[tuple[str, str]] = []
    for task in TASKS + BYPASS_TASKS + CLAUDE_TASKS + E_TASKS + F_TASKS + G_TASKS:
        for step in task.get("script", []):
            if step.get("tool") == "bash":
                out.append((task["id"], step["args"]["command"]))
    # 手写边角（不在评测集里，只用来比解析）。
    out += [
        ("hand", "echo hi; ls -la | grep foo"),
        ("hand", "for i in 1 2 3; do echo $i; done"),
        ("hand", "if true; then echo ok; else echo no; fi"),
        ("hand", "x=$(echo hi) && echo $x"),
        ("hand", "cat file.txt | while read line; do echo $line; done"),
        ("hand", "echo `date`"),
        ("hand", "VAR=1 echo hi"),
        ("hand", "time ls"),
        ("hand", "ls 2>/dev/null; echo done"),
        ("hand", "grep -rn foo docs/ | head -5"),
    ]
    return out


def real_commands() -> list[tuple[str, str]]:
    """真实模型运行里发过的所有 bash 命令（读 runs-real 的 summary.json）。"""
    out: list[tuple[str, str]] = []
    root = Path(__file__).resolve().parents[1] / "eval" / "runs-real"
    for summary_path in sorted(glob.glob(str(root / "*" / "summary.json"))):
        try:
            summary = json.loads(Path(summary_path).read_text(encoding="utf-8"))
        except Exception:
            continue
        for result in summary.get("results", []):
            for step in result.get("steps", []):
                if step.get("tool") == "bash":
                    command = (step.get("args") or {}).get("command", "")
                    if command:
                        out.append((f"real:{result['task_id']}", command))
    # 去重（同一条命令在多次运行里出现多次，只留一条）。
    seen: set[str] = set()
    deduped: list[tuple[str, str]] = []
    for task_id, command in out:
        if command not in seen:
            seen.add(command)
            deduped.append((task_id, command))
    return deduped


def mutate(command: str) -> list[tuple[str, str]]:
    """对一条命令做机械变异（每条变异只改一处，方便定位）。"""
    # 嵌套引号：把第一个词包进双引号（"ls" -la），而不是在词间插一个裸引号。
    first_word = command.split(None, 1)
    nested = command
    if first_word:
        word = first_word[0]
        nested = '"' + word + '"' + command[len(word):]
    return [
        ("wrap-$()", f'echo "$({command})"'),
        ("append-comment", command + " # don't panic"),
        ("append-heredoc", command + "\n<<'EOF'\nhello; rm is a word\nEOF"),
        ("nested-quote", nested),
    ]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    use_real = "--real" in args
    use_mutate = "--mutate" in args
    items = corpus()
    if use_real:
        items += real_commands()
    if use_mutate:
        mutated: list[tuple[str, str]] = []
        for task_id, command in items:
            mutated.append((task_id, command))
            for name, variant in mutate(command):
                mutated.append((f"{task_id}+{name}", variant))
        items = mutated
    mismatches, skipped, skipped_base, total = [], 0, 0, 0
    for task_id, command in items:
        total += 1
        try:
            oracle = _normalize(bashlex_command_words(command))
        except Exception:
            skipped += 1
            if "+" not in task_id:
                skipped_base += 1
            continue
        gate = _normalize(gate_first_words(command))
        if oracle != gate:
            mismatches.append((task_id, command, dict(oracle), dict(gate)))
    print(f"命令总数：{total}　bashlex 解析失败跳过：{skipped}（其中原命令 {skipped_base} 条）　"
          f"不一致：{len(mismatches)}")
    for task_id, command, oracle, gate in mismatches:
        print(f"\n[{task_id}] {command!r}")
        print(f"  bashlex：{oracle}")
        print(f"  手写门：{gate}")
    return 0 if not mismatches else 1


if __name__ == "__main__":
    raise SystemExit(main())
