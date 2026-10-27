"""仅供显式授权的固定公开烟测；默认拒绝读取凭据和联网。"""

import argparse
import json
from dataclasses import replace

import httpx

from min_agent.llm.client import LLMError, OpenAIResponsesModel
from min_agent.llm.config import OpenAISettings
from min_agent.llm.deepseek import DeepSeekResponsesModel, DeepSeekSettings
from min_agent.models import Message
from min_agent.runtime import Runtime
from min_agent.tools.calculator import CALCULATOR_TOOL
from min_agent.tools.registry import ToolRegistry


def run_live_smoke(credentials_file, *, allow_live: bool = False, transport: httpx.BaseTransport | None = None, proxy: str | None = None, provider: str = "openai") -> dict:
    if allow_live is not True:
        raise PermissionError("需要明确允许固定公开烟测；尚未读取凭据")
    if provider == "openai":
        settings_type, model_type = OpenAISettings, OpenAIResponsesModel
    elif provider == "deepseek":
        settings_type, model_type = DeepSeekSettings, DeepSeekResponsesModel
    else:
        raise ValueError("未支持的提供商")
    settings = settings_type.from_credentials_file(
        credentials_file, max_requests=3, max_output_tokens=4096,
    )
    with model_type(settings, transport=transport, proxy=proxy) as model:
        plain = model.generate([Message(role="user", content="请只回复：连接成功")])
        plain_ok = plain.finish_reason == "stop" and plain.content.strip() == "连接成功"
        used = []
        if plain_ok:
            def tracked(expression):
                output = CALCULATOR_TOOL.handler(expression=expression)
                used.append((expression, output))
                return output

            tools = ToolRegistry()
            tools.register(replace(CALCULATOR_TOOL, handler=tracked))
            result = Runtime(model, tools=tools).run(
                "请调用calculator计算0.1+0.2，并根据真实工具结果回答；不要心算替代工具。"
            )
            calculator_ok = (
                result.status == "completed" and len(used) == 1
                and used[0][0].replace(" ", "") == "0.1+0.2" and used[0][1] == "0.3"
                and "0.3" in result.response.content
            )
        else:
            calculator_ok = False
        return {
            "plain_ok": plain_ok, "calculator_ok": calculator_ok,
            "requests": model.request_count, "model": settings.model,
            "provider": provider, "reasoning_effort": settings.reasoning_effort,
            "response_model": plain.provider_state.response_model if plain.provider_state else None,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="显式授权后运行固定OpenAI问答/calculator烟测")
    parser.add_argument("--credentials-file", required=True)
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--proxy", help="显式本机HTTP代理URL；不自动读取环境代理")
    parser.add_argument("--provider", choices=["openai", "deepseek"], default="openai")
    options = parser.parse_args()
    try:
        provider_options = {"provider": options.provider} if options.provider != "openai" else {}
        result = run_live_smoke(options.credentials_file, allow_live=options.allow_live, proxy=options.proxy, **provider_options)
    except LLMError as error:
        # 仅输出适配器白名单诊断，不输出错误正文、原始响应头或认证请求。
        payload = {"ok": False, "error": error.code}
        if error.diagnostics:
            payload["diagnostics"] = error.diagnostics
        print(json.dumps(payload))
        return 1
    except (ValueError, PermissionError):
        print(json.dumps({"ok": False, "error": "live_smoke_failed_or_not_authorized"}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["plain_ok"] and result["calculator_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
