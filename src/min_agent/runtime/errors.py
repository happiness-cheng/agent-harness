"""错误码映射与工具结果消息构造；纯函数，不读取全局状态。

主循环与工具阶段只调用这里的函数，不再各自拼错误字典：
错误码、``execution_state``、``recovery`` 与 JSON 序列化差异都集中在一处。
"""

import json
from typing import get_args

from min_agent.contracts.models import Message
from min_agent.contracts.trace import ErrorCode


_ADMISSION_CODES = {
    "task_not_active", "confirmation_grant_missing", "confirmation_mismatch",
    "logical_task_mismatch", "attempt_mismatch", "confirmation_not_bound", "budget_mismatch",
}
_TRACE_CODES = frozenset(get_args(ErrorCode))


def trace_error_code(code: str | None) -> str | None:
    """trace.py 的 error_code 是固定 Literal；精确 admission code 留在 RunResult/工具消息。"""
    if code is None:
        return None
    if code in _ADMISSION_CODES:
        return "waiting_user"
    return code if code in _TRACE_CODES else "model_call_failed"


def model_error_code(error: object) -> str:
    """模型适配器只能透出白名单形状的错误码，其余压成固定码。"""
    code = getattr(error, "code", None)
    if not isinstance(code, str) or not code or len(code) > 40 \
            or not code.replace("_", "").isalnum() or not code[0].isalpha():
        return "model_call_failed"
    return code


def _tool_message(call_id: str, payload: dict[str, object],
                  *, ensure_ascii: bool = True) -> Message:
    return Message(
        role="tool", tool_call_id=call_id,
        content=json.dumps(payload, ensure_ascii=ensure_ascii),
    )


def blocked(call_id: str, *, code: str) -> Message:
    """调用没有开始：停止、等待引导、无进展或计划待确认。"""
    return _tool_message(call_id, {"ok": False, "error": {
        "code": code, "execution_state": "not_started",
    }})


def needs_recovery(call_id: str, *, code: str, execution_state: str) -> Message:
    """结果可能已写入：要求模型先核对真实状态，不自动重试。"""
    return _tool_message(call_id, {"ok": False, "error": {
        "code": code, "execution_state": execution_state,
        "recovery": "verify_actual_state_before_retry",
    }})


def waiting_user(call_id: str, *, code: str) -> Message:
    """等待用户确认或模式选择；该请求没有执行。"""
    return _tool_message(call_id, {"ok": False, "error": {
        "code": code, "message": "等待用户确认或模式选择；该请求未执行。",
        "execution_state": "not_started", "recovery": "directed_user_confirmation",
    }}, ensure_ascii=False)


def budget_checkpoint(call_id: str) -> Message:
    """已到工具收尾边界；该请求没有执行。"""
    return _tool_message(call_id, {"ok": False, "error": {
        "code": "budget_checkpoint", "message": "已到工具收尾边界；该请求未执行。",
        "execution_state": "not_started", "recovery": "review_checkpoint",
    }}, ensure_ascii=False)


def budget_blocked(call_id: str, *, code: str, scope: str, recovery: str) -> Message:
    """预算未放行；该请求没有执行，scope 说明是任务还是片段额度。"""
    return _tool_message(call_id, {"ok": False, "error": {
        "code": code, "message": "预算未放行；该请求未执行。",
        "scope": scope, "resource": "tool_calls",
        "execution_state": "not_started", "recovery": recovery,
    }}, ensure_ascii=False)
