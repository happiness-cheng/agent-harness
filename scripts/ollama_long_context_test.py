r"""32K 长上下文多轮真实任务验收；默认拒绝运行，不下载模型、不碰用户数据。

流程：校准CSV的token密度 -> 加载num_ctx=32768 -> 第一轮带约20K+ tokens日志的
calculator任务 -> 第二轮基于工具结果继续计算 -> 记录内存峰值、prompt tokens、耗时、
工具执行、trace、Ollama截断日志。

用法（项目根目录）：
  .\.venv\Scripts\python.exe -X utf8 scripts\ollama_long_context_test.py --allow-live
"""

import argparse
import ctypes
import json
import os
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from min_agent.application import HarnessApplication
from min_agent.llm.ollama import OllamaModel, OllamaSettings, require_model


MODEL = "qwen3.5:4b"


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
    return {
        "available_ram_gb": round(status.ullAvailPhys / gb, 2),
        "available_commit_gb": round(status.ullAvailPageFile / gb, 2),
        "total_ram_gb": round(status.ullTotalPhys / gb, 2),
    }


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


def server_log():
    return Path(os.environ.get("LOCALAPPDATA", "")) / "Ollama" / "server.log"


def log_size():
    path = server_log()
    return path.stat().st_size if path.exists() else 0


def truncation_events(offset):
    path = server_log()
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        stream.seek(offset)
        return [line.strip() for line in stream if "truncating input prompt" in line]


def build_log_document(target_chars, *, needle_line, needle_at=0.6):
    """生成CSV风格长日志；唯一记录放在指定比例位置。"""
    lines, size, index = [], 0, 0
    while size < target_chars:
        if size >= target_chars * needle_at and needle_line not in lines:
            lines.append(needle_line)
            size += len(needle_line)
            continue
        index += 1
        line = (f"2026-10-{index % 28 + 1:02d}T{index % 24:02d}:{index * 7 % 60:02d}:"
                f"{index * 13 % 60:02d}Z,node-{index % 97:03d},amount={index % 5000},"
                f"status={'ok' if index % 7 else 'retry'},ref=OP-{index:06d}\n")
        lines.append(line)
        size += len(line)
    if needle_line not in lines:
        lines.insert(int(len(lines) * needle_at), needle_line)
    return "".join(lines)


NEEDLE_1 = "2026-10-15T09:41:07Z,node-042,amount=37,status=ok,ref=ZX-7391-KQ\n"
NEEDLE_2 = "2026-10-16T11:22:33Z,node-077,amount=5,status=ok,ref=ZX-9999-AB\n"


def calibrate(base_url, num_ctx, sample_chars=8000):
    sample = build_log_document(sample_chars, needle_line=NEEDLE_1, needle_at=0.5)
    body = {"model": MODEL, "messages": [{"role": "user", "content": sample}], "stream": False,
            "think": False, "keep_alive": "30m",
            "options": {"num_ctx": num_ctx, "num_predict": 1, "temperature": 0}}
    with httpx.Client(timeout=300, trust_env=False) as client:
        data = client.post(base_url + "/api/chat", json=body).json()
    tokens = data.get("prompt_eval_count")
    return {"sample_bytes": len(sample.encode()), "sample_tokens": tokens,
            "bytes_per_token": round(len(sample.encode()) / tokens, 2) if tokens else None}


def unload(base_url):
    with httpx.Client(timeout=120, trust_env=False) as client:
        models = client.get(base_url + "/api/ps").json().get("models", [])
        for item in models:
            body = {"model": MODEL, "messages": [{"role": "user", "content": "。"}],
                    "stream": False, "keep_alive": 0,
                    "options": {"num_ctx": item.get("context_length") or 8192, "num_predict": 1}}
            client.post(base_url + "/api/chat", json=body)
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
    with httpx.Client(timeout=300, trust_env=False) as client:
        response = client.post(base_url + "/api/chat", json=body)
    wall = round(time.perf_counter() - started, 2)
    data = response.json() if response.status_code == 200 else {}
    with httpx.Client(timeout=10, trust_env=False) as client:
        entries = client.get(base_url + "/api/ps").json().get("models", [])
    entry = entries[0] if entries else {}
    return {"ok": response.status_code == 200, "http_status": response.status_code,
            "wall_seconds": wall, "load_seconds": round((data.get("load_duration") or 0) / 1e9, 2),
            "ps_size_gb": round(entry.get("size", 0) / 1024 ** 3, 2),
            "ps_vram_gb": round(entry.get("size_vram", 0) / 1024 ** 3, 2),
            "context_length": entry.get("context_length")}


def run_turn(harness, session, text, timeout):
    started = time.perf_counter()
    harness.submit(session, text)
    harness.wait(session, timeout=timeout)
    snapshot = harness.snapshot(session)
    return snapshot, round(time.perf_counter() - started, 2)


def message_view(messages):
    return [{"role": message["role"],
             "content": (message["content"] or "")[:300],
             "tool_calls": [{"name": call["name"], "arguments": call["arguments"]}
                            for call in message.get("tool_calls") or []]} for message in messages]


def main():
    parser = argparse.ArgumentParser(description="32K长上下文多轮真实任务验收（显式授权）")
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--num-ctx", type=int, default=32768)
    parser.add_argument("--target-total-tokens", type=int, default=24000,
                        help="目标总prompt tokens（含系统提示与工具Schema）")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--report", type=Path)
    options = parser.parse_args()
    if options.allow_live is not True:
        print(json.dumps({"ok": False, "error": "需要--allow-live显式授权"}, ensure_ascii=False))
        return 2
    settings = OllamaSettings(model=MODEL, base_url=options.base_url, num_ctx=options.num_ctx,
                              timeout_seconds=options.timeout)
    try:
        require_model(settings)
    except ValueError as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 1

    report = {"started_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
              "model": MODEL, "num_ctx": options.num_ctx,
              "target_total_tokens": options.target_total_tokens,
              "preload_memory": memory()}
    print("卸载旧实例 ...", flush=True)
    report["unloaded"] = unload(options.base_url)
    print("校准CSV token密度 ...", flush=True)
    calibration = calibrate(options.base_url, options.num_ctx)
    report["calibration"] = calibration
    ratio = calibration["bytes_per_token"] or 1.4
    tokens_per_char = 1.0 / ratio
    # 网页输入上限为20000字符：第一轮文档约19000字符，第二轮再补一段，
    # 使第二轮累计prompt tokens进入20K–26K，同时保持单条消息不超限。
    doc1_chars = 19000
    doc2_chars = min(15000, max(5000, int((options.target_total_tokens - 17500) / tokens_per_char)))
    document1 = build_log_document(doc1_chars, needle_line=NEEDLE_1, needle_at=0.6)
    document2 = build_log_document(doc2_chars, needle_line=NEEDLE_2, needle_at=0.5)
    report["document_bytes"] = len(document1.encode()) + len(document2.encode())
    report["document1_chars"] = len(document1)
    report["document2_chars"] = len(document2)
    print(f"文档1 {len(document1)} 字符；文档2 {len(document2)} 字符", flush=True)

    log_offset = log_size()
    sampler = Sampler()
    sampler.__enter__()
    harness = None
    try:
        print("加载模型 ...", flush=True)
        report["load"] = load(options.base_url, options.num_ctx)
        report["after_load_memory"] = memory()
        if not report["load"]["ok"]:
            report["ok"] = False
            report["error"] = "load_failed"
            return 1
        database = Path(tempfile.mkdtemp(prefix="ollama-long-")) / "task.sqlite3"
        harness = HarnessApplication(database, model=OllamaModel(settings))
        session = harness.create_session("32K长上下文验收")["session_id"]
        turn1_text = ("下面是一份很长的运行日志（CSV）。请阅读全文，找到 ref=ZX-7391-KQ 的那一行，"
                      "取它的 amount 值，并调用 calculator 工具计算 amount 乘以 19。"
                      "必须使用工具，不要心算。最后告诉我计算结果。\n\n" + document1)
        print("第一轮：长日志检索 + calculator ...", flush=True)
        first, first_wall = run_turn(harness, session, turn1_text, options.timeout)
        print("第二轮：追加长日志并基于工具结果继续计算 ...", flush=True)
        turn2_text = ("下面是一段补充日志。请找到 ref=ZX-9999-AB 的 amount，"
                      "并用 calculator 把上一轮的计算结果加上这个 amount，告诉我最终值。"
                      "必须使用工具。\n\n" + document2)
        second, second_wall = run_turn(harness, session, turn2_text, options.timeout)
        report["turn1"] = {"wall_seconds": first_wall, "status": first["task"]["status"],
                           "error_code": first["task"].get("error_code"),
                           "messages": message_view(first["messages"]),
                           "trace": [event["operation"] + ":" + event["status"] for event in first["trace"]],
                           "budget": first["budget"]["usage"]}
        report["turn2"] = {"wall_seconds": second_wall, "status": second["task"]["status"],
                           "error_code": second["task"].get("error_code"),
                           "messages": message_view(second["messages"]),
                           "trace": [event["operation"] + ":" + event["status"] for event in second["trace"]],
                           "budget": second["budget"]["usage"]}
        report["usage"] = harness.model.usage
        tool_results = []
        for message in second["messages"]:
            if message["role"] != "tool":
                continue
            try:
                value = json.loads(message["content"])
            except (TypeError, ValueError):
                value = {}
            tool_results.append({"ok": value.get("ok"), "result": value.get("result")})
        report["tool_results"] = tool_results
        first_answer = next((message["content"] for message in reversed(first["messages"])
                             if message["role"] == "assistant" and message["content"]), None)
        second_answer = next((message["content"] for message in reversed(second["messages"])
                              if message["role"] == "assistant" and message["content"]), None)
        report["turn1_answer"] = first_answer
        report["turn2_answer"] = second_answer
        report["final_answer"] = second_answer
        report["truncation_logs"] = truncation_events(log_offset)
        prompt_tokens = [item.get("prompt_eval_count") for item in report["usage"]]
        report["prompt_tokens"] = prompt_tokens
        report["turn2_prompt_tokens"] = prompt_tokens[2] if len(prompt_tokens) > 2 else None
        report["prompt_tokens_in_range"] = bool(
            len(prompt_tokens) > 2 and prompt_tokens[2] is not None
            and 20000 <= prompt_tokens[2] <= 26000)
        report["ok"] = bool(
            first["task"]["status"] == "completed" and second["task"]["status"] == "completed"
            and tool_results and tool_results[0]["result"] == "703"
            and len(tool_results) >= 2 and tool_results[-1]["result"] == "708"
            and "703" in (first_answer or "") and "708" in (second_answer or "")
            and report["prompt_tokens_in_range"] and not report["truncation_logs"]
        )
    finally:
        sampler.__exit__()
        report["memory"] = sampler.summary()
        if harness is not None:
            try:
                harness.close()
            except Exception:
                pass
        report["unloaded_after"] = unload(options.base_url)
        report["recovered_memory"] = memory()
    report["finished_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    if options.report is not None:
        options.report.parent.mkdir(parents=True, exist_ok=True)
        options.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"报告已写入 {options.report}", flush=True)
    print(json.dumps({"ok": report.get("ok"),
                      "prompt_tokens": report.get("prompt_tokens"), "turn2_prompt_tokens": report.get("turn2_prompt_tokens"), "in_range": report.get("prompt_tokens_in_range"),
                      "tool_results": [item.get("result") for item in report.get("tool_results", [])],
                      "truncation_logs": len(report.get("truncation_logs", [])),
                      "memory": report.get("memory")}, ensure_ascii=False))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
