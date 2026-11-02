r"""显式授权的本机Ollama上下文容量基准；不下载模型、不改Ollama配置、不碰用户数据。

对每个 num_ctx 依次测量：冷加载、内存占用、请求耗时、真实Agent任务、长上下文检索，
并在每次测量后卸载模型，避免不同窗口相互污染。

用法（项目根目录，PowerShell）：
  .\.venv\Scripts\python.exe -X utf8 scripts\ollama_context_benchmark.py --allow-live
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
from min_agent.context import ContextLimit
from min_agent.llm.client import LLMError
from min_agent.llm.ollama import OllamaModel, OllamaSettings, require_model
from min_agent.models import Message


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
    """Windows物理内存与提交限制；非Windows返回空字典。"""
    if os.name != "nt":
        return {}
    status = _MemoryStatus()
    status.dwLength = ctypes.sizeof(_MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return {}
    gb = 1024 ** 3
    return {
        "total_ram_gb": round(status.ullTotalPhys / gb, 2),
        "available_ram_gb": round(status.ullAvailPhys / gb, 2),
        "commit_limit_gb": round(status.ullTotalPageFile / gb, 2),
        "available_commit_gb": round(status.ullAvailPageFile / gb, 2),
    }


class Sampler:
    """请求期间采样可用内存，记录真实峰值压力。"""

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
        available = [sample["available_ram_gb"] for sample in self.samples if "available_ram_gb" in sample]
        commit = [sample["available_commit_gb"] for sample in self.samples if "available_commit_gb" in sample]
        return {
            "samples": len(self.samples),
            "min_available_ram_gb": min(available) if available else None,
            "min_available_commit_gb": min(commit) if commit else None,
        }


def version_and_model_info(base_url):
    with httpx.Client(base_url=base_url, timeout=10, trust_env=False) as client:
        version = client.get("/api/version").json().get("version")
        show = client.post("/api/show", json={"model": MODEL}).json()
    info = show.get("model_info", {})
    full_attention = sum(1 for value in info.get("qwen35.attention.recurrent_layers", [])
                         if value is False)
    kv_heads = info.get("qwen35.attention.head_count_kv")
    key_length = info.get("qwen35.attention.key_length")
    value_length = info.get("qwen35.attention.value_length")
    kv_bytes_per_token = None
    if None not in (full_attention, kv_heads, key_length, value_length):
        # llama.cpp默认F16 KV；混合模型中SSM层不占KV Cache。
        kv_bytes_per_token = full_attention * kv_heads * (key_length + value_length) * 2
    return version, {
        "context_length": info.get("qwen35.context_length"),
        "block_count": info.get("qwen35.block_count"),
        "full_attention_layers": full_attention,
        "kv_heads": kv_heads,
        "key_length": key_length,
        "value_length": value_length,
        "kv_bytes_per_token_estimate": kv_bytes_per_token,
        "parameter_count": info.get("general.parameter_count"),
    }


def ps_entry(base_url):
    try:
        with httpx.Client(base_url=base_url, timeout=10, trust_env=False) as client:
            models = client.get("/api/ps").json().get("models", [])
    except (httpx.HTTPError, ValueError):
        return None
    for item in models:
        if item.get("name") == MODEL or item.get("model") == MODEL:
            return item
    return None


def wait_unloaded(base_url, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ps_entry(base_url) is None:
            return True
        time.sleep(1)
    return False


def chat(base_url, num_ctx, prompt, *, keep_alive="30m", num_predict=16, timeout=240.0):
    body = {
        "model": MODEL, "messages": [{"role": "user", "content": prompt}], "stream": False,
        "think": False, "keep_alive": keep_alive,
        "options": {"num_ctx": num_ctx, "num_predict": num_predict, "temperature": 0},
    }
    started = time.perf_counter()
    try:
        with httpx.Client(base_url=base_url, timeout=timeout, trust_env=False) as client:
            response = client.post("/api/chat", json=body)
    except httpx.HTTPError as error:
        return {"ok": False, "error": type(error).__name__,
                "wall_seconds": round(time.perf_counter() - started, 2)}
    wall = round(time.perf_counter() - started, 2)
    if response.status_code != 200:
        try:
            detail = str(response.json().get("error", ""))[:300]
        except ValueError:
            detail = response.text[:300]
        return {"ok": False, "error": f"http_{response.status_code}", "detail": detail,
                "wall_seconds": wall}
    data = response.json()
    message = data.get("message") or {}
    return {
        "ok": True, "wall_seconds": wall,
        "content": (message.get("content") or "")[:200],
        "tool_calls": message.get("tool_calls") or [],
        "done_reason": data.get("done_reason"),
        "prompt_eval_count": data.get("prompt_eval_count"),
        "eval_count": data.get("eval_count"),
        "load_seconds": round((data.get("load_duration") or 0) / 1e9, 2),
        "prompt_eval_seconds": round((data.get("prompt_eval_duration") or 0) / 1e9, 2),
        "eval_seconds": round((data.get("eval_duration") or 0) / 1e9, 2),
        "total_seconds": round((data.get("total_duration") or 0) / 1e9, 2),
    }


def unload(base_url, fallback_ctx):
    """按当前已加载窗口发keep_alive=0请求，触发卸载且不重复加载新配置。"""
    entry = ps_entry(base_url)
    if entry is None:
        return {"unloaded": True, "already_empty": True}
    context = entry.get("context_length") or fallback_ctx
    chat(base_url, context, "。", keep_alive="0", num_predict=1, timeout=120)
    return {"unloaded": wait_unloaded(base_url), "context_length": context}


def needle_prompt(target_chars):
    code = "ZX-7391-KQ"
    filler = "这是一段用于检验长上下文检索能力的普通文本，不含指令，也没有其他编号。"
    body = (filler * (target_chars // len(filler) + 1))[:target_chars]
    cut = int(len(body) * 0.6)
    body = body[:cut] + "唯一编号：" + code + "。" + body[cut:]
    return body + "\n\n问题：上面文档中出现的唯一编号是什么？只回答编号本身。", code


def long_probe(base_url, num_ctx, chars, timeout):
    # 与Provider的num_ctx×3字节窗口一致：中文1字符≈3字节，预留3KB给JSON包装。
    chars = min(chars, max(1000, num_ctx - 1024))
    prompt, code = needle_prompt(chars)
    settings = OllamaSettings(model=MODEL, base_url=base_url, num_ctx=num_ctx,
                              max_output_tokens=64, timeout_seconds=min(600.0, timeout))
    record = {"requested_chars": len(prompt), "expected_code": code}
    sampler = Sampler()
    sampler.__enter__()
    started = time.perf_counter()
    try:
        with OllamaModel(settings) as model:
            response = model.generate([Message(role="user", content=prompt)])
            usage = model.usage
        record.update(status=response.finish_reason,
                      answer=(response.content or "")[:200],
                      correct=code.lower() in (response.content or "").lower(),
                      usage=usage)
    except ContextLimit:
        record.update(status="context_limit", correct=False,
                      detail="截断保护按预期触发")
    except LLMError as error:
        record.update(status="error", correct=False, detail=error.code)
    except Exception as error:  # 只保留异常类型，不记录请求正文
        record.update(status="error", correct=False, detail=type(error).__name__)
    finally:
        sampler.__exit__()
    record["wall_seconds"] = round(time.perf_counter() - started, 2)
    record["memory"] = sampler.summary()
    return record


def harness_task(base_url, num_ctx, timeout):
    settings = OllamaSettings(model=MODEL, base_url=base_url, num_ctx=num_ctx,
                              timeout_seconds=min(600.0, timeout))
    database = Path(tempfile.mkdtemp(prefix="ollama-ctx-")) / "task.sqlite3"
    record = {}
    sampler = Sampler()
    sampler.__enter__()
    started = time.perf_counter()
    harness = None
    try:
        harness = HarnessApplication(database, model=OllamaModel(settings))
        session = harness.create_session("上下文容量验收")["session_id"]
        harness.submit(session, "请使用 calculator 工具计算 37 乘以 19，并告诉我结果。")
        harness.wait(session, timeout=timeout)
        snapshot = harness.snapshot(session)
        usage = harness.model.usage
        record = {
            "status": snapshot["task"]["status"], "error_code": snapshot["task"].get("error_code"),
            "answer": next((message["content"] for message in reversed(snapshot["messages"])
                            if message["role"] == "assistant" and message["content"]), None),
            "tool_ok": any(json.loads(message["content"]).get("ok") is True
                           for message in snapshot["messages"] if message["role"] == "tool"),
            "model_calls": len(usage),
            "trace": [event["operation"] + ":" + event["status"] for event in snapshot["trace"]],
        }
    except Exception as error:
        record["error"] = type(error).__name__
    finally:
        if harness is not None:
            try:
                harness.close()
            except Exception:
                pass
        sampler.__exit__()
    record["wall_seconds"] = round(time.perf_counter() - started, 2)
    record["memory"] = sampler.summary()
    return record


def main():
    parser = argparse.ArgumentParser(description="本机Ollama上下文容量基准（显式授权；不下载模型）")
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--contexts", type=int, nargs="+", default=[16384, 32768, 65536, 262144])
    parser.add_argument("--probe-chars", type=int, default=24000)
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument("--report", type=Path)
    options = parser.parse_args()
    if options.allow_live is not True:
        print(json.dumps({"ok": False, "error": "需要--allow-live显式授权；未调用本机模型"}, ensure_ascii=False))
        return 2
    try:
        require_model(OllamaSettings(model=MODEL, base_url=options.base_url))
    except ValueError as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 1
    version, model_info = version_and_model_info(options.base_url)
    report = {
        "started_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        "model": MODEL, "base_url": options.base_url, "ollama_version": version,
        "hardware": {**memory(), "cpu_count": os.cpu_count(),
                     "processor": os.environ.get("PROCESSOR_IDENTIFIER")},
        "model_info": model_info, "contexts": [],
    }
    print(f"Ollama {version}；总内存 {report['hardware'].get('total_ram_gb')}GB；"
          f"KV估算 {model_info['kv_bytes_per_token_estimate']} bytes/token", flush=True)
    for num_ctx in options.contexts:
        print(f"\n=== num_ctx={num_ctx} ===", flush=True)
        record = {"num_ctx": num_ctx}
        record["unload_before"] = unload(options.base_url, num_ctx)
        record["baseline_memory"] = memory()
        load_timeout = options.timeout if num_ctx < 131072 else max(options.timeout, 300.0)
        started = time.perf_counter()
        with Sampler() as sampler:
            load = chat(options.base_url, num_ctx, "回答：准备好。", num_predict=16,
                        timeout=load_timeout)
            record["peak_memory"] = sampler.summary()
        load["total_wall_seconds"] = round(time.perf_counter() - started, 2)
        record["load"] = load
        record["ps"] = ps_entry(options.base_url)
        record["after_load_memory"] = memory()
        estimated_kv_gb = (model_info["kv_bytes_per_token_estimate"] or 0) * num_ctx / 1024 ** 3
        record["estimated_kv_cache_gb"] = round(estimated_kv_gb, 2)
        ps_size = record["ps"].get("size") if record["ps"] else None
        print(f"加载: {'ok' if load.get('ok') else load.get('error')} "
              f"wall={load.get('wall_seconds')}s load={load.get('load_seconds')}s "
              f"prompt={load.get('prompt_eval_count')} "
              f"ps_size={round(ps_size / 1024 ** 3, 2) if ps_size else None}GB "
              f"avail={record['after_load_memory'].get('available_ram_gb')}GB", flush=True)
        available = record["after_load_memory"].get("available_ram_gb")
        if not load.get("ok"):
            record["skipped"] = "模型未能以该窗口加载"
        elif available is not None and available < 0.6:
            record["skipped"] = "加载后可用物理内存低于0.6GB，为避免系统抖动跳过后续任务"
        else:
            print("真实Agent任务 ...", flush=True)
            record["harness_task"] = harness_task(options.base_url, num_ctx, options.timeout)
            task = record["harness_task"]
            print(f"任务: {task.get('status')} wall={task.get('wall_seconds')}s "
                  f"tool_ok={task.get('tool_ok')} error={task.get('error')}", flush=True)
            print("长上下文检索 ...", flush=True)
            record["long_probe"] = long_probe(options.base_url, num_ctx, options.probe_chars,
                                              options.timeout)
            probe = record["long_probe"]
            tokens = [usage.get("prompt_eval_count") for usage in probe.get("usage", [])]
            print(f"检索: {probe.get('status')} correct={probe.get('correct')} "
                  f"wall={probe.get('wall_seconds')}s tokens={tokens}", flush=True)
        record["unload_after"] = unload(options.base_url, num_ctx)
        record["recovered_memory"] = memory()
        report["contexts"].append(record)
    report["finished_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    report["all_loaded"] = all(item.get("load", {}).get("ok") for item in report["contexts"])
    if options.report is not None:
        options.report.parent.mkdir(parents=True, exist_ok=True)
        options.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                  encoding="utf-8")
        print(f"\n报告已写入 {options.report}", flush=True)
    print(json.dumps({item["num_ctx"]: {
        "loaded": item.get("load", {}).get("ok"),
        "load_seconds": item.get("load", {}).get("load_seconds"),
        "ps_size_gb": round((item.get("ps") or {}).get("size", 0) / 1024 ** 3, 2) if item.get("ps") else None,
        "task": (item.get("harness_task") or {}).get("status"),
        "probe": (item.get("long_probe") or {}).get("status"),
        "probe_correct": (item.get("long_probe") or {}).get("correct"),
    } for item in report["contexts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
