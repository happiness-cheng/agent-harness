"""真实模型层入口：python -m eval.run_real --provider groq --tasks C1 C2 --repeat 1

Groq 走已接通的 custom.groq 连接器（key 不进本进程）；
Gemini 暂时只支持传 key 的方式（连接器还没接通，见 run 时的提示）。

额度上限（有界授权，不烧光免费档）：
- Groq gpt-oss-120b 免费档：每天 1000 次请求、20 万 tokens。
  本脚本默认 token 上限 15 万，到上限就停，留 5 万余量。
- 每题每轮最多 40 次模型请求（HarnessApplication 的 total_models），
  超了 Harness 自己停。
- 429（限流）出现一次就停整批，不重试、不换接口。
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from eval.real import OpenAICompatibleModel, OllamaEvalModel, ollama_available  # noqa: E402
from eval.real_runner import run_real_one  # noqa: E402
from eval.tasks import TASKS, task_by_id  # noqa: E402

GROQ_HELPER = Path.home() / "workspace/custom-connectors/custom.groq/bin/groq-chat.py"
GEMINI_HELPER = Path.home() / "workspace/custom-connectors/custom.gemini/bin/gemini-chat.py"
QIANWEN_HELPER = Path.home() / "workspace/custom-connectors/custom.qianwen/bin/qianwen-chat.py"
GEMINI_SUBSET = ["C1", "C2", "C3", "C4", "C5", "S1", "S4", "S5"]


def _report(results: list[dict], info: dict) -> str:
    lines = ["# 真实模型层报告", ""]
    # 报告头（EVAL-DIAG-002，Claude 审批第 1 点）：模型名、参数、日期、每题跑几次。
    model = info.get("model") or {}
    lines.append(f"模型：{info.get('provider')} / {model.get('model')}，"
                 f"温度 {model.get('temperature')}，max_tokens {model.get('max_tokens')}")
    lines.append(f"日期：{info.get('started_at')}（UTC），每题跑 {info.get('repeat')} 次")
    lines.append(f"门：{'开' if info.get('gate_enabled', True) else '关（A/B 对照组）'}")
    lines.append(f"工具面：{info.get('mode', 'generic')}")
    lines.append("")
    passed = sum(1 for r in results if r["verdict"] == "PASS")
    errors = sum(1 for r in results if r["verdict"] == "ERROR")
    na = sum(1 for r in results if r["verdict"] == "NOT_APPLICABLE")
    total_tokens = sum(
        (u.get("prompt_tokens") or 0) + (u.get("completion_tokens") or 0)
        for r in results for u in r.get("usage", []))
    lines.append(f"通过 {passed}/{len(results)}，运行出错 {errors} 题，不适用 {na} 题"
                 f"（ERROR/NOT_APPLICABLE 不计入通过/失败），累计 tokens 约 {total_tokens}。")
    lines.append("")
    for r in results:
        failed = [f"{c['id']}: {c['reason']}" for c in r["checks"]
                  if c["verdict"] not in ("PASS", "NOT_APPLICABLE")]
        tokens = sum((u.get("prompt_tokens") or 0) + (u.get("completion_tokens") or 0)
                     for u in r.get("usage", []))
        lines.append(f"## {r['task_id']} {r['verdict']}（工具调用 {r['tool_calls']} 次，"
                     f"模型请求 {r.get('model_requests', 0)} 次，tokens 约 {tokens}，"
                     f"{r.get('elapsed_ms', 0)}ms）")
        if failed:
            lines.append(f"未通过的检查：{', '.join(failed)}")
        term = r.get("termination") or {}
        if term:
            lines.append(f"终止原因：task_status={term.get('task_status')}，"
                         f"最后一步错误码={term.get('last_error_code')}，"
                         f"预算消耗={term.get('budget_usage')}")
        gate = r.get("gate") or {}
        if gate:
            # 绕过按最终状态判（EVAL-DIAG-002）：被拦后受保护的效果真的发生了才算。
            lines.append(f"门触发次数：{gate.get('gate_trigger_count', 0)}"
                         + (f"，被拦的命令：{gate.get('blocked_commands')}" if gate.get('blocked_commands') else "")
                         + (f"，被拦后绕过：是，证据：{gate.get('bypass_evidence')}" if gate.get('bypass_after_block') else "，被拦后绕过：否"))
            if gate.get('suspicious_followups'):
                lines.append(f"被拦后又试了门拦不住的路径（只记现象，不定性）：{gate.get('suspicious_followups')}")
        if r.get("error"):
            lines.append(f"错误：{r['error']}")
        if r.get("model_last_error"):
            lines.append(f"模型最后一次报错：{r['model_last_error']}")
        if r.get("model_retries"):
            lines.append(f"瞬时失败重试：{r['model_retries']} 次")
        if r.get("answer"):
            lines.append(f"模型最后回答：{r['answer'][:200]}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="groq", choices=["groq", "gemini", "qianwen", "ollama"])
    parser.add_argument("--model", default=None)
    parser.add_argument("--mode", default="generic", choices=["generic", "dedicated"],
                        help="generic=四工具面；dedicated=默认专用工具面（无 bash，"
                        "blocked_command 检查标 NOT_APPLICABLE，不改判据）")
    parser.add_argument("--ollama-base-url", default="http://127.0.0.1:11434",
                        help="只用于 ollama；loopback，无 key、无代理")
    parser.add_argument("--tasks", nargs="*", default=None,
                        help="不传则 groq 跑全部 21 题，gemini 只跑子集")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=120, help="每题等待上限（秒）")
    parser.add_argument("--temperature", type=float, default=0.0,
                        help="模型温度。默认 0.0（与 Ollama 默认一致）；"
                        "稳定性数据应在 0.7 下跑，温度 0 的重复几乎没有方差信息"
                        "（EVAL-DIAG-003，Claude 第三轮审批）。")
    parser.add_argument("--token-cap", type=int, default=150_000,
                        help="整批累计 tokens 上限，到上限就停")
    parser.add_argument("--api-key", default=None,
                        help="只用于 gemini；绝不贴聊天，用环境外的方式传")
    parser.add_argument("--gate-off", action="store_true",
                        help="关掉门再跑（A/B 对比的对照组）。评测时 monkeypatch 门，"
                        "不改门代码；报告头会写明 gate_enabled=False")
    args = parser.parse_args()

    if args.gate_off:
        # A/B 对照组：把门整个短路掉（返回 None = 不拦）。只影响本进程，
        # 门代码本身不动；run_real_one 走同一条路，所以对照是干净的。
        import min_agent.tools.generic as _generic
        _generic._blocked_command = lambda command, root: None

    if args.provider == "gemini" and args.api_key is None and not GEMINI_HELPER.exists():
        print("Gemini 连接器还没接通：先在聊天里填 Gemini 的安全卡片，"
              "或用 --api-key 传（不要贴聊天）。", file=sys.stderr)
        return 2
    # S7 的注入只活在脚本化 script 里，真实模型层跑它会得一个假 PASS，
    # 所以默认改跑 S7R（注入放进 prompt，模型看得到）；S7 只留给脚本化层。
    task_ids = args.tasks or (GEMINI_SUBSET if args.provider == "gemini"
                              else [t["id"] for t in TASKS if t["id"] != "S7"])
    tasks = [task_by_id(task_id) for task_id in task_ids]

    if args.provider == "ollama":
        ok, detail = ollama_available(args.model or "qwen3.5:4b", args.ollama_base_url)
        if not ok:
            print(f"Ollama 基线 NOT RUN：{detail}。不下载模型、不回落云端。", file=sys.stderr)
            return 2

    def factory():
        if args.provider == "ollama":
            return OllamaEvalModel(model=args.model, temperature=args.temperature,
                                   base_url=args.ollama_base_url, max_requests=300)
        if args.provider == "groq":
            return OpenAICompatibleModel(provider="groq", model=args.model,
                                         connector_helper=str(GROQ_HELPER),
                                         temperature=args.temperature,
                                         max_requests=300)
        if args.provider == "qianwen":
            # 优先走已接通的 custom.qianwen 连接器（key 不进本进程）；
            # 没接通时才走 --api-key（由调用方从 Secure Vault 传入，不贴聊天）。
            if QIANWEN_HELPER.exists():
                return OpenAICompatibleModel(provider="qianwen", model=args.model,
                                             connector_helper=str(QIANWEN_HELPER),
                                             temperature=args.temperature,
                                             max_requests=300)
            if args.api_key is None:
                print("千问需要连接器或 --api-key：先在聊天里填千问的安全卡片，"
                      "再把 key 交给这条命令。", file=sys.stderr)
                raise SystemExit(2)
            return OpenAICompatibleModel(provider="qianwen", model=args.model,
                                         api_key=args.api_key, temperature=args.temperature, max_requests=300)
        if args.api_key is not None:
            return OpenAICompatibleModel(provider="gemini", model=args.model,
                                         api_key=args.api_key, temperature=args.temperature, max_requests=300)
        return OpenAICompatibleModel(provider="gemini", model=args.model,
                                     connector_helper=str(GEMINI_HELPER),
                                     temperature=args.temperature,
                                     max_requests=300)

    out_dir = (Path(__file__).resolve().parent / "runs-real" /
               datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H-%M-%S+00-00"))
    out_dir.mkdir(parents=True)
    # 报告头：先建一个模型实例读实际参数（只读 describe，不发请求），再关掉。
    probe = factory()
    model_info = probe.describe()
    try:
        probe.close()
    except Exception:
        pass
    started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    info = {"provider": args.provider, "model": model_info,
            "started_at": started_at, "repeat": args.repeat,
            "gate_enabled": not args.gate_off, "mode": args.mode}
    results: list[dict] = []
    spent = 0
    import time as _time
    for task in tasks:
        for _ in range(args.repeat):
            _time.sleep(2)  # 题与题之间停 2 秒，别连着打爆免费档的瞬时限流
            if spent >= args.token_cap:
                print(f"累计 tokens 已到上限 {args.token_cap}，停下不继续烧额度。")
                break
            result = run_real_one(task, factory, timeout_seconds=args.timeout,
                                  generic=(args.mode == "generic"))
            spent += sum((u.get("prompt_tokens") or 0) + (u.get("completion_tokens") or 0)
                         for u in result.get("usage", []))
            results.append(result)
            print(f"{result['task_id']} {result['verdict']} "
                  f"工具 {result['tool_calls']} 次 请求 {result.get('model_requests', 0)} 次 "
                  f"累计 tokens 约 {spent}")
            if "429" in str(result.get("error") or ""):
                print("遇到 429 限流，停整批。")
                break
    passed = sum(1 for r in results if r["verdict"] == "PASS")
    errors = sum(1 for r in results if r["verdict"] == "ERROR")
    na = sum(1 for r in results if r["verdict"] == "NOT_APPLICABLE")
    summary = {"suite": "generic-tools-real-v0.1", "provider": args.provider,
               "model": args.model, "model_resolved": model_info,
               "started_at": started_at, "repeat": args.repeat,
               "gate_enabled": not args.gate_off, "mode": args.mode,
               "total_runs": len(results), "passed": passed,
               "failed": len(results) - passed - errors - na, "errors": errors,
               "not_applicable": na,
               "total_tokens": spent,
               "token_cap": args.token_cap,
               "honesty_note": ("真实模型层：模型自己选工具，判据和脚本化层同一套。"
                                "小样本只能看大差异。"),
               "results": results}
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "report.md").write_text(_report(results, info), encoding="utf-8")
    print(f"\n通过 {passed}/{len(results)}，累计 tokens 约 {spent}。报告：{out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
