"""软阈值收尾：最多一次模型调用整理检查点，不执行任何工具。"""

import json

from min_agent.ledger.budget import BudgetExceeded, BudgetPersistenceError
from min_agent.ledger.checkpoints import (
    CLOSING_INSTRUCTION, Checkpoint, CheckpointNote, ToolResultRef,
)
from min_agent.contracts.models import Message, ModelResponse
from min_agent.runtime.state import RunResult


class CheckpointCloser:
    """仅一次收尾尝试；额度/协议失败也保留真实结果引用，不执行收尾工具。"""

    def __init__(self, budget, control, model, trace, prompt) -> None:
        self._budget = budget
        self._control = control
        self._model = model
        self._trace = trace
        self._prompt = prompt

    def close(self, messages, context, run_id, parent_call_id, reason) -> RunResult:
        note, response = None, None
        closing_status, closing_error = "not_started", None
        with self._trace.span("model", run_id=run_id, context=context,
                              parent_call_id=parent_call_id) as span:
            try:
                self._budget.consume("model_calls")
            except BudgetExceeded:
                reason, closing_error = "hard_budget", "budget_exhausted"
                span.finish("partial", closing_error, "not_started")
            except BudgetPersistenceError:
                closing_error = "budget_persistence_failed"
                span.finish("failed", closing_error, "not_started")
            else:
                try:
                    model_messages = self._prompt(messages + [Message(role="user", content=CLOSING_INSTRUCTION)])
                    response = self._model.generate(model_messages)
                except Exception:
                    closing_status, closing_error = "failed", "model_call_failed"
                    span.finish("failed", closing_error)
                else:
                    try:
                        if not isinstance(response, ModelResponse):
                            raise ValueError("收尾必须是模型响应")
                        response = ModelResponse.model_validate(response.model_dump(warnings="error"))
                        if response.finish_reason != "stop":
                            raise ValueError("收尾必须为无工具的完整文字")
                        note = CheckpointNote.model_validate_json(response.content)
                    except (ValueError, TypeError):
                        closing_status, closing_error = "invalid_response", "checkpoint_invalid_response"
                        response = None
                        span.finish("partial", closing_error)
                    else:
                        closing_status = "returned"
                        messages.append(Message(role="assistant", content=response.content,
                                                provider_state=response.provider_state))
                        span.finish("completed", execution_state="returned")
        refs = []
        for index, message in enumerate(messages):
            if message.role != "tool":
                continue
            try:
                outcome = json.loads(message.content)
                ok = outcome.get("ok") if isinstance(outcome, dict) else None
                ok = ok if type(ok) is bool else None
                error = outcome.get("error", {}) if isinstance(outcome, dict) else {}
                state = "returned" if ok is True else (
                    "not_started" if isinstance(error, dict) and error.get("execution_state") == "not_started"
                    else "unknown"
                )
            except (ValueError, TypeError):
                ok, state = None, "unknown"
            refs.append(ToolResultRef(message_index=index, call_id=message.tool_call_id,
                                      ok=ok, execution_state=state))
        stopped = self._control is not None and self._control.snapshot()["stop"]
        if stopped:
            reason = "user_stopped"
        binding = self._budget.binding
        checkpoint = Checkpoint(
            segment_id=self._budget.segment_id, budget_id=binding.budget_id if binding else None,
            session_id=context.session_id, reason=reason, closing_status=closing_status,
            closing_error=closing_error, model_note=note, tool_results=tuple(refs),
            history_length=len(messages), task_usage=self._budget._ledger.usage,
            segment_usage=self._budget.usage,
        )
        failed = closing_error == "budget_persistence_failed"
        return RunResult(status="failed" if failed else "cancelled" if stopped else "partial",
                         response=response,
                         error_code="user_stopped" if stopped and not failed else
                         closing_error if closing_status == "not_started" else "budget_checkpoint",
                         checkpoint=checkpoint)
