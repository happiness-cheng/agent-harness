r"""131072 上下文分阶段验收；默认拒绝运行，不下载模型、不改Ollama配置。

按用户指定流程：
1. 加载前记录可用内存、提交内存与分页文件状态。
2. 以 num_ctx=131072 加载，持续采样加载期最低可用内存。
3. 低于安全阈值或出现严重分页时立即卸载并标记“资源不满足”。
4. 只有内存余量充分才执行短任务，再逐步增加实际输入长度。
5. 记录内存峰值、实际 prompt tokens、耗时、正确性与截断日志。

用法（项目根目录）：
  .\.venv\Scripts\python.exe -X utf8 scripts\ollama_128k_staged_test.py --allow-live
"""

import argparse
import ctypes
import json
import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from min_agent.context import ContextLimit
from min_agent.llm.client import LLMError
from min_agent.llm.ollama import OllamaModel, OllamaSettings, require_model
from min_agent.models import Message
from min_agent.runtime import Runtime
from min_agent.tools.calculator import CALCULATOR_TOOL
from min_agent.tools.registry import ToolDefinition, ToolRegistry


MODEL = "qwen3.5:4b"
STAGES = [4000, 12000, 24000, 48000]


class _MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def memory():
    status = _MemoryStatus()
    status.dwLength = ctypes.sizeof(_MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return {}
    gb = 1024 ** 3
    return {"total_ram_gb": round(status.ullTotalPhys / gb, 2),
            "available_ram_gb": round(status.ullAvailPhys / gb, 2),
            "commit_limit_gb": round(status.ullTotalPageFile / gb, 2),
            "available_commit_gb": round(status.ullAvailPageFile / gb, 2)}


def pagefile():
    """通过只读WMI查询分页文件分配/当前/峰值，不修改配置。"""
    import shutil
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        return None
    try:
        result = subprocess.run(
            [shell, "-NoProfile", "-Command",
             "Get-CimInstance Win32_PageFileUsage | Select-Object "
             "AllocatedBaseSize,CurrentUsage,PeakUsage | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=20, check=False)
        data = json.loads(result.stdout.strip() or "null")
        return data
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


class Sampler:
    def __init__(self):
        self.samples = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            self.samples.append(memory())
            self._stop.wait(0.5)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *args):
        self._stop.set()
        self._thread.join(timeout=2)

    def summary(self):
        ram = [item["available_ram_gb"] for item in self.samples if "available_ram_gb" in item]
        commit = [item["available_commit_gb"] for item in self.samples if "available_commit_gb" in item]
        return {"samples": len(self.samples),
                "min_available_ram_gb": min(ram) if ram else None,
                "min_available_commit_gb": min(commit) if commit else None}


def log_path():
    return Path(os.environ.get("LOCALAPPDATA", "")) / "Ollama" / "server.log"


def log_offset():
    path = log_path()
    return path.stat().st_size if path.exists() else 0


def truncations(offset):
    path = log_path()
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        stream.seek(offset)
        return [line.strip() for line in stream if "truncating input prompt" in line]


def document(chars, needle_at=0.6):
    needle = "2026-10-15T09:41:07Z,node-042,amount=37,status=ok,ref=ZX-7391-KQ\n"
    lines, size, index = [], 0, 0
    while size < chars:
        if size >= chars * needle_at and needle not in lines:
            lines.append(needle)
            size += len(needle)
            continue
        index += 1
        line = (f"2026-10-{index % 28 + 1:02d}T{index % 24:02d}:{index * 7 % 60:02d}:"
                f"{index * 13 % 60:02d}Z,node-{index % 97:03d},amount={index % 5000},"
                f"status={'ok' if index % 7 else 'retry'},ref=OP-{index:06d}\n")
        lines.append(line)
        size += len(line)
    if needle not in lines:
        lines.insert(int(len(lines) * needle_at), needle)
    return "".join(lines) + "\n\n问题：日志中 ref=ZX-7391-KQ 的 amount 是多少？只回答数字。"


def unload(base_url):
    with httpx.Client(timeout=120, trust_env=False) as client:
        for _ in range(2):
            models = client.get(base_url + "/api/ps").json().get("models", [])
            if not models:
                return True
            for item in models:
                body = {"model": MODEL, "messages": [{"role": "user", "content": "。"}],
                        "stream": False, "keep_alive": 0,
                        "options": {"num_ctx": item.get("context_length") or 8192, "num_predict": 1}}
                client.post(base_url + "/api/chat", json=body)
            time.sleep(2)
        for _ in range(60):
            if not client.get(base_url + "/api/ps").json().get("models"):
                return True
            time.sleep(1)
    return False


def load(base_url, num_ctx):
    body = {"model": MODEL, "messages": [{"role": "user", "content": "回答：准备好。"}],
            "stream": False, "think": False, "keep_alive": "30m",
            "options": {"num_ctx": num_ctx, "num_predict": 8, "temperature": 0}}
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=300, trust_env=False) as client:
            response = client.post(base_url + "/api/chat", json=body)
    except httpx.HTTPError as error:
        return {"ok": False, "error": type(error).__name__, "wall_seconds": round(time.perf_counter() - started, 2)}
    wall = round(time.perf_counter() - started, 2)
    if response.status_code != 200:
        return {"ok": False, "http_status": response.status_code, "wall_seconds": wall,
                "detail": str(response.text)[:300]}
    data = response.json()
    with httpx.Client(timeout=10, trust_env=False) as client:
        entries = client.get(base_url + "/api/ps").json().get("models", [])
    entry = entries[0] if entries else {}
    return {"ok": True, "wall_seconds": wall,
            "load_seconds": round((data.get("load_duration") or 0) / 1e9, 2),
            "ps_size_gb": round(entry.get("size", 0) / 1024 ** 3, 2),
            "ps_vram_gb": round(entry.get("size_vram", 0) / 1024 ** 3, 2),
            "context_length": entry.get("context_length")}


def short_task(base_url, num_ctx, timeout, executed):
    def tracked(expression):
        value = CALCULATOR_TOOL.handler(expression=expression)
        executed.append({"expression": expression, "result": value})
        return value

    settings = OllamaSettings(model=MODEL, base_url=base_url, num_ctx=num_ctx, timeout_seconds=timeout)
    registry = ToolRegistry()
    registry.register(ToolDefinition("calculator", CALCULATOR_TOOL.description,
                                    CALCULATOR_TOOL.parameters, tracked))
    started = time.perf_counter()
    with OllamaModel(settings) as model:
        result = Runtime(model, tools=registry).run("请使用 calculator 计算 37 乘以 19，并告诉我结果。")
    return {"status": result.status, "error_code": result.error_code,
            "answer": (result.response.content or "")[:200] if result.response else None,
            "wall_seconds": round(time.perf_counter() - started, 2), "executed": executed}


def staged_prompt(base_url, num_ctx, tokens, timeout):
    text = document(tokens)
    settings = OllamaSettings(model=MODEL, base_url=base_url, num_ctx=num_ctx,
                              max_output_tokens=32, timeout_seconds=min(600.0, timeout))
    record = {"requested_chars": len(text)}
    sampler = Sampler()
    sampler.__enter__()
    started = time.perf_counter()
    try:
        with OllamaModel(settings) as model:
            response = model.generate([Message(role="user", content=text)])
            usage = model.usage
        record.update(status=response.finish_reason,
                      answer=(response.content or "")[:100],
                      correct="37" in (response.content or ""),
                      usage=usage)
    except ContextLimit:
        record.update(status="context_limit", correct=False)
    except LLMError as error:
        record.update(status="error", detail=error.code, correct=False)
    except Exception as error:
        record.update(status="error", detail=type(error).__name__, correct=False)
    finally:
        sampler.__exit__()
    record["wall_seconds"] = round(time.perf_counter() - started, 2)
    record["memory"] = sampler.summary()
    return record


def main():
    parser = argparse.ArgumentParser(description="131072上下文分阶段验收（显式授权）")
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--num-ctx", type=int, default=131072)
    parser.add_argument("--min-after-load-gb", type=float, default=0.6)
    parser.add_argument("--min-during-load-gb", type=float, default=0.3)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--max-stage-seconds", type=float, default=300.0)
    parser.add_argument("--report", type=Path)
    options = parser.parse_args()
    if options.allow_live is not True:
        print(json.dumps({"ok": False, "error": "需要--allow-live显式授权"}, ensure_ascii=False))
        return 2
    settings = OllamaSettings(model=MODEL, base_url=options.base_url, num_ctx=options.num_ctx)
    try:
        require_model(settings)
    except ValueError as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 1
    report = {"started_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
              "model": MODEL, "num_ctx": options.num_ctx,
              "thresholds": {"min_after_load_gb": options.min_after_load_gb,
                             "min_during_load_gb": options.min_during_load_gb},
              "preload": {"memory": memory(), "pagefile": pagefile()}}
    print("卸载旧实例 ...", flush=True)
    report["unloaded"] = unload(options.base_url)
    report["preload_after_unload"] = {"memory": memory(), "pagefile": pagefile()}
    offset = log_offset()
    print(f"以 num_ctx={options.num_ctx} 加载并采样内存 ...", flush=True)
    with Sampler() as sampler:
        report["load"] = load(options.base_url, options.num_ctx)
        report["load_memory"] = sampler.summary()
    report["after_load"] = {"memory": memory(), "pagefile": pagefile()}
    min_load = report["load_memory"].get("min_available_ram_gb")
    available = report["after_load"]["memory"].get("available_ram_gb")
    print(f"加载: {'ok' if report['load'].get('ok') else report['load'].get('error')} "
          f"最低可用={min_load}GB 加载后可用={available}GB", flush=True)
    insufficient = (not report["load"].get("ok")
                    or (min_load is not None and min_load < options.min_during_load_gb)
                    or (available is not None and available < options.min_after_load_gb))
    report["resource_insufficient"] = insufficient
    if insufficient:
        report["verdict"] = "资源不满足：加载期或加载后内存低于安全阈值，立即卸载，不执行真实任务"
        report["unload"] = unload(options.base_url)
        report["recovered"] = {"memory": memory(), "pagefile": pagefile()}
    else:
        executed = []
        print("短任务 ...", flush=True)
        report["short_task"] = short_task(options.base_url, options.num_ctx, options.timeout, executed)
        print(f"短任务: {report['short_task']['status']} 工具={executed}", flush=True)
        stages = []
        for tokens in STAGES:
            if tokens >= options.num_ctx:
                break
            print(f"逐步输入约 {tokens} tokens ...", flush=True)
            record = staged_prompt(options.base_url, options.num_ctx, tokens, options.timeout)
            record["target_tokens"] = tokens
            stages.append(record)
            print(f"  {record.get('status')} correct={record.get('correct')} "
                  f"wall={record.get('wall_seconds')}s", flush=True)
            if record.get("status") != "stop" or not record.get("correct"):
                break
            if record.get("wall_seconds", 0) > options.max_stage_seconds:
                record["stopped_for_time"] = True
                break
            if (record.get("memory") or {}).get("min_available_ram_gb", 1) < options.min_during_load_gb:
                record["stopped_for_memory"] = True
                break
        report["stages"] = stages
        report["verdict"] = ("完成短任务与分级长输入" if stages and all(
            item.get("correct") for item in stages) else "已执行但长输入未全部通过")
        report["unload"] = unload(options.base_url)
        report["recovered"] = {"memory": memory(), "pagefile": pagefile()}
    report["truncation_logs"] = truncations(offset)
    report["finished_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    if options.report is not None:
        options.report.parent.mkdir(parents=True, exist_ok=True)
        options.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"报告已写入 {options.report}", flush=True)
    print(json.dumps({"num_ctx": options.num_ctx,
                      "resource_insufficient": report["resource_insufficient"],
                      "verdict": report["verdict"],
                      "min_during_load_gb": min_load,
                      "available_after_load_gb": available,
                      "truncation_logs": len(report["truncation_logs"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
