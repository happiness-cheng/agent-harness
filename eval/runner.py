"""Runner：reset -> run -> snapshot -> verifier -> record。

每题跑在一个全新的临时工作区里（tempfile.mkdtemp），跑完不删，
路径写进报告，方便失败时直接回放。工作区外再放一个 outside/ 目录，
放 S2/S5 要保护的哨兵文件，跑前跑后都记 hash。

只用真实代码：ToolRegistry + generic_tools()，和 application.py
_generic 模式注册的是同一批工具（application.py:578-586）。
不经过 HarnessApplication / SQLite / 模型，所以跑得快、离线、可重复，
代价是：它不证明模型会选对工具。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import time
from pathlib import Path

from min_agent.contracts.models import ToolCall
from min_agent.tools.generic import generic_tools
from min_agent.tools.registry import ToolRegistry

from .tasks import TASKS
from .verifiers import verify


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot(root: Path) -> dict:
    out = {}
    if not root.exists():
        return out
    for path in sorted(root.rglob("*")):
        if path.is_file() and ".adrian" not in path.parts:
            out[str(path.relative_to(root))] = _sha256(path)
    return out


def _schema_report() -> dict:
    """通用 4 工具 vs 专用文件族 7 工具的 schema 体积对比。

    专用侧用 filesystem_tools() 的真实 schema，不用手写数字。
    token 数是估算（约 1 token ≈ 4 字节），只用来看量级，不当精确值。
    专用侧要走完整写门（expected_sha256 + Plan/Build 确认），
    这里只比 schema 体积，不比端到端——后者要上真实模型层。
    """
    from min_agent.tools.filesystem import filesystem_tools

    with tempfile.TemporaryDirectory() as tmp:
        generic = generic_tools(tmp)
        # filesystem_tools 不传 gate 只返回 fs_list/fs_read 两个只读工具；
        # 要量完整的 7 个文件工具，必须传一个恒放行的 gate，
        # 只用来量 schema，不执行写操作。
        try:
            specialized = filesystem_tools(tmp, gate=lambda: (True, "", ""))
        except Exception as error:  # 专用构造失败也要显形
            return {"generic": None, "specialized": None, "error": str(error)}
    generic_json = json.dumps(
        [{"name": t.name, "description": t.description,
          "parameters": t.parameters.model_json_schema()} for t in generic],
        ensure_ascii=False)
    specialized_json = json.dumps(
        [{"name": t.name, "description": t.description,
          "parameters": t.parameters.model_json_schema()} for t in specialized],
        ensure_ascii=False)
    return {
        "generic": {"tools": [t.name for t in generic], "bytes": len(generic_json.encode()),
                    "approx_tokens": len(generic_json.encode()) // 4},
        "specialized_fs": {"tools": [t.name for t in specialized],
                           "bytes": len(specialized_json.encode()),
                           "approx_tokens": len(specialized_json.encode()) // 4},
        "note": "专用侧只是文件族 7 个工具，不是默认 28 个的全部；完整 28 个要走 HarnessApplication 注册链。",
    }


def _substitute_args(args, work: Path):
    """把 script 参数里的 {work} 换成工作区绝对路径（B11 用：测门放不放行
    落在工作区内的绝对路径）。只换字符串里的占位符，不改别的东西。
    用 as_posix() 不用 str()：命令在 bash 下跑，反斜杠会被 bash 当转义
    吃掉（Windows 上 str(work) 是 D:\\...，cat 拿到的是残缺路径）；
    posix 斜杠 bash 原生认，Linux 上 as_posix() == str()，无行为变化。"""
    if isinstance(args, dict):
        return {k: _substitute_args(v, work) for k, v in args.items()}
    if isinstance(args, str):
        return args.replace("{work}", work.as_posix())
    return args


def run_one(task: dict, *, repeat: int = 1) -> dict:
    """跑一题 repeat 次（默认 1 次；小样本只能看大差异，见 README）。"""
    runs = []
    for seed in range(repeat):
        base = Path(tempfile.mkdtemp(prefix=f"eval-{task['id']}-"))
        work = base / "work"
        outside = base / "outside"
        work.mkdir()
        outside.mkdir()
        for rel, content in task.get("fixture", {}).items():
            target = work / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            # {"symlink_to": "../outside"} 建符号链接（B6 用，测门能不能看穿链接）。
            if isinstance(content, dict) and "symlink_to" in content:
                target.symlink_to(content["symlink_to"])
            else:
                # newline="\n"：Windows 文本模式会把 \n 翻成 \r\n，fixture
                # 必须确定是 LF（C1 的判据要 "l2\nl3"；read 返回磁盘原样，不归一）。
                target.write_text(content, encoding="utf-8", newline="\n")
        for rel, content in task.get("outside", {}).items():
            target = outside / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")
        before = {"work": _snapshot(work), "outside": _snapshot(outside)}
        registry = ToolRegistry()
        for tool in generic_tools(work):
            registry.register(tool)
        steps = []
        started = time.perf_counter()
        for i, call in enumerate(task["script"]):
            call_started = time.perf_counter()
            try:
                result = registry.execute(
                    ToolCall(id=f"c{i}", name=call["tool"],
                             arguments=_substitute_args(call["args"], work)))
            except Exception as error:  # Registry 抛异常也要记下来，不能吞掉
                result = {"ok": False, "error": {"code": "registry_exception",
                                                 "message": f"{type(error).__name__}: {error}"}}
            steps.append({
                "tool": call["tool"], "args": call["args"], "result": result,
                "duration_ms": int((time.perf_counter() - call_started) * 1000),
            })
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        checked = verify(task, work=work, outside=outside, steps=steps)
        context_bytes = sum(
            len(json.dumps(s["result"], ensure_ascii=False).encode("utf-8")) for s in steps)
        runs.append({
            "seed": seed, "verdict": checked["verdict"], "checks": checked["checks"],
            "tool_calls": len(steps), "context_bytes": context_bytes,
            "elapsed_ms": elapsed_ms, "steps": steps,
            "workspace": str(work), "before": before,
            "after": {"work": _snapshot(work), "outside": _snapshot(outside)},
        })
    verdicts = [r["verdict"] for r in runs]
    return {
        "task_id": task["id"], "category": task["category"], "title": task["title"],
        "decision_if": task["decision_if"], "expected": task.get("expected", "PASS"),
        "verdict": "PASS" if all(v == "PASS" for v in verdicts) else "FAIL",
        "runs": runs,
        "median_tool_calls": sorted(r["tool_calls"] for r in runs)[len(runs) // 2],
        "median_context_bytes": sorted(r["context_bytes"] for r in runs)[len(runs) // 2],
    }


def run_suite(task_ids: list[str] | None = None, *, repeat: int = 1,
              tasks: list[dict] | None = None) -> dict:
    # real_only 的题（S7R）只给真实模型层，脚本化层跳过：script 为空，跑了也是假 PASS。
    pool = TASKS if tasks is None else tasks
    tasks = ([t for t in pool if not t.get("real_only")] if task_ids is None
             else [t for t in pool if t["id"] in task_ids])
    if task_ids is not None and len(tasks) != len(task_ids):
        missing = set(task_ids) - {t["id"] for t in tasks}
        raise KeyError(f"找不到任务：{sorted(missing)}")
    results = [run_one(task, repeat=repeat) for task in tasks]
    passed = sum(1 for r in results if r["verdict"] == "PASS")
    by_category: dict[str, dict] = {}
    for r in results:
        bucket = by_category.setdefault(r["category"], {"total": 0, "passed": 0})
        bucket["total"] += 1
        bucket["passed"] += 1 if r["verdict"] == "PASS" else 0
    return {
        "suite": "generic-tools-v0.1", "repeat": repeat,
        "total": len(results), "passed": passed, "failed": len(results) - passed,
        "by_category": by_category,
        "schema": _schema_report(),
        "results": results,
        "honesty_note": (
            "SCRIPTED 层：脚本化工具调用 + 确定性验证，证明工具本身能不能做成，"
            "不证明真实模型会选对工具、会拦住注入、会在高风险前停下。"
            "真实模型层（HarnessApplication + Ollama/Qwen）是下一步，见 README。"
        ),
    }
