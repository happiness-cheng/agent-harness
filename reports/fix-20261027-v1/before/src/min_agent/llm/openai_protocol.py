"""仅映射本项目支持的文本及原生函数协议，不执行远端工具。"""

import json
import re
from copy import deepcopy

from min_agent.models import Message, ModelResponse, ProviderState, ToolCall
from min_agent.sessions import SessionStore


def load_json(value):
    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("JSON含重复字段")
            result[key] = item
        return result

    def invalid_constant(value):
        raise ValueError("JSON含非有限数值")

    return json.loads(value, object_pairs_hook=unique, parse_constant=invalid_constant)


def _text(value):
    if not isinstance(value, str):
        raise ValueError("文本字段类型错误")
    return value


def _project(output: list, *, complete: bool, provider: str):
    texts, summaries, calls = [], [], []
    refused = False
    if not isinstance(output, list):
        raise ValueError("output必须为列表")
    for item in output:
        if not isinstance(item, dict):
            raise ValueError("原生条目标识无效")
        if provider == "openai.responses" or "id" in item:
            if not _text(item["id"]).strip():
                raise ValueError("原生条目标识无效")
        if complete and item.get("status") not in {None, "completed"}:
            raise ValueError("完成响应中存在未完成条目")
        kind = item["type"]
        if kind == "message":
            if item["role"] != "assistant" or not isinstance(item["content"], list):
                raise ValueError("原生消息无效")
            for part in item["content"]:
                if part["type"] == "output_text":
                    texts.append(_text(part["text"]))
                elif part["type"] == "refusal":
                    refused = True
                    texts.append(_text(part["refusal"]))
                else:
                    raise ValueError("未支持的消息内容")
        elif kind == "reasoning":
            if provider == "deepseek.responses":
                if "content" not in item and (item.get("summary") or item.get("encrypted_content")):
                    raise ValueError("DeepSeek不支持OpenAI摘要或加密状态")
                parts = item.get("content", [])
                if not isinstance(parts, list):
                    raise ValueError("原生推理状态格式错误")
                for part in parts:
                    if part["type"] != "reasoning_text":
                        raise ValueError("未支持的原生状态内容")
                    _text(part["text"])
                # 有效的原生内容仅用于协议续传，不展示成回答或摘要。
            else:
                for part in item.get("summary", []):
                    if part["type"] != "summary_text":
                        raise ValueError("未支持的摘要内容")
                    summaries.append(_text(part["text"]))
                if complete and not _text(item.get("encrypted_content", "")).strip():
                    raise ValueError("缺少可回传的加密状态")
                if item.get("content"):
                    raise ValueError("不支持原始推理正文")
        elif kind == "function_call":
            if complete:
                if item.get("status", "completed") != "completed":
                    raise ValueError("工具调用尚未完成")
                calls.append(ToolCall(
                    id=item["call_id"], name=item["name"],
                    arguments=load_json(_text(item["arguments"])),
                ))
        else:
            raise ValueError("未支持的远端输出类型")
    return "".join(texts) or None, "\n".join(summaries) or None, calls, refused


def _state_output(output: list, provider: str) -> list:
    result = deepcopy(output)
    if provider == "deepseek.responses":
        for item in result:
            if item["type"] == "reasoning":
                # 仅保留已验证的原生内容，丢弃不支持的兼容元数据。
                item.pop("summary", None)
                item.pop("encrypted_content", None)
    return result


def parse_response(raw: dict, *, model: str = "gpt-6.1-sol", provider: str = "openai.responses") -> ModelResponse:
    if provider not in {"openai.responses", "deepseek.responses"}:
        raise ValueError("未支持的提供商")
    if not isinstance(raw, dict):
        raise ValueError("响应必须是对象")
    status = raw["status"]
    if status in {"failed", "cancelled", "queued", "in_progress"}:
        return ModelResponse(finish_reason="error")
    if status not in {"completed", "incomplete"}:
        raise ValueError("未知响应状态")
    content, summary, calls, refused = _project(raw["output"], complete=status == "completed", provider=provider)
    if status == "incomplete":
        reason = raw["incomplete_details"]["reason"]
        finish = {"max_output_tokens": "length", "content_filter": "refusal"}.get(reason, "error")
    else:
        finish = "refusal" if refused else "tool_calls" if calls else "stop"
    state = None
    if finish in {"stop", "tool_calls"}:
        state = ProviderState(
            provider=provider, model=model, response_model=raw["model"],
            output=_state_output(raw["output"], provider),
        )
    return ModelResponse(
        finish_reason=finish, content=content, reasoning_summary=summary,
        tool_calls=calls if finish == "tool_calls" else [], provider_state=state,
    )


def build_input(messages: list[Message], *, model: str = "gpt-6.1-sol", provider: str = "openai.responses") -> list[dict]:
    if provider not in {"openai.responses", "deepseek.responses"}:
        raise ValueError("未支持的提供商")
    if not isinstance(messages, list) or not messages:
        raise ValueError("必须提供非空消息列表")
    checked = []
    for message in messages:
        if not isinstance(message, Message):
            raise TypeError("消息类型错误")
        checked.append(Message.model_validate(message.model_dump(warnings=False)))
    if SessionStore._pending(checked):
        raise ValueError("工具结果尚未配齐")
    result = []
    for message in checked:
        state = message.provider_state
        if state is not None:
            if state.provider != provider or state.model != model:
                raise ValueError("提供商或模型状态不匹配")
            projected = parse_response({"status": "completed", "model": state.response_model, "output": state.output}, model=model, provider=provider)
            visible = lambda content, calls: json.dumps(
                {"content": content or "", "calls": [call.model_dump() for call in calls]},
                sort_keys=True, allow_nan=False,
            )
            if visible(projected.content, projected.tool_calls) != visible(message.content, message.tool_calls):
                raise ValueError("原生状态与可见消息不一致")
            result.extend(_state_output(state.output, provider))
        elif message.role == "tool":
            result.append({"type": "function_call_output", "call_id": message.tool_call_id, "output": message.content})
        else:
            if message.content or not message.tool_calls:
                result.append({"role": message.role, "content": message.content})
            result.extend({
                "type": "function_call", "call_id": call.id, "name": call.name,
                "arguments": json.dumps(call.arguments, ensure_ascii=False, allow_nan=False),
            } for call in message.tool_calls)
    return result


def build_tools(tools: list[dict] | None) -> list[dict]:
    if tools is None:
        return []
    if not isinstance(tools, list):
        raise TypeError("工具定义必须为列表")
    result, names = [], set()
    for tool in tools:
        if set(tool) != {"name", "description", "parameters"}:
            raise ValueError("工具定义字段不符")
        name = tool["name"]
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) or name in names:
            raise ValueError("工具名称无效或重复")
        if not isinstance(tool["description"], str) or tool["parameters"].get("type") != "object":
            raise ValueError("工具Schema无效")
        names.add(name)
        result.append({"type": "function", "strict": False, **deepcopy(tool)})
    return load_json(json.dumps(result, ensure_ascii=False, allow_nan=False))
