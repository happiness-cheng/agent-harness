"""直接回答与串行工具请求的同步模型循环。"""

import json
from dataclasses import dataclass, replace
from importlib.resources import files
from typing import Literal, Protocol
from uuid import UUID, uuid4

from min_agent.budget import BudgetExceeded, BudgetPersistenceError, BudgetSegment
from min_agent.context import ContextLimit
from min_agent.clock import Clock
from min_agent.checkpoints import CLOSING_INSTRUCTION, Checkpoint, CheckpointNote, CheckpointPolicy, ToolResultRef
from min_agent.models import Message, ModelResponse
from min_agent.sessions import ExecutionContext, SessionStore
from min_agent.task_state import AuthorizationRequired
from min_agent.tools.registry import InvalidToolResultError, ToolRegistry
from min_agent.trace import TraceContext, TraceRecorder


class Model(Protocol):
    """Runtime 所需的最小模型接口，不绑定服务商。"""

    def generate(
        self, messages: list[Message], *, tools: list[dict[str, object]] | None = None
    ) -> ModelResponse: ...


@dataclass(frozen=True)
class RunResult:
    """本次运行状态；completed 表示模型正常回答，不证明业务目标。"""

    status: Literal["completed", "waiting_user", "partial", "failed", "cancelled"]
    response: ModelResponse | None = None
    error_code: str | None = None
    run_id: UUID | None = None
    checkpoint: Checkpoint | None = None


class Runtime:
    def __init__(
        self, model: Model, tools: ToolRegistry | None = None, *, max_tool_error_rounds: int = 2,
        trace: TraceRecorder | None = None, budget: BudgetSegment | None = None,
        checkpoint_policy: CheckpointPolicy | None = None,
        control=None, progress_guard=None,
        recover_unknown_writes: bool = False,
    ) -> None:
        if type(max_tool_error_rounds) is not int:
            raise TypeError("工具错误修正轮数必须是整数")
        if max_tool_error_rounds < 0:
            raise ValueError("工具错误修正轮数不能为负")
        if budget is not None and not isinstance(budget, BudgetSegment):
            raise TypeError("budget必须是可信调用方提供的BudgetSegment")
        if checkpoint_policy is not None:
            if not isinstance(checkpoint_policy, CheckpointPolicy):
                raise TypeError("checkpoint_policy必须是CheckpointPolicy")
            if budget is None:
                raise ValueError("收尾策略需要显式共享预算")
            checkpoint_policy = CheckpointPolicy.model_validate(checkpoint_policy.model_dump())
        self._model = model
        self._budget = budget
        self._checkpoint_policy = checkpoint_policy
        self._control, self._progress_guard = control, progress_guard
        if type(recover_unknown_writes) is not bool:
            raise TypeError("未知写入恢复开关必须为布尔值")
        self._recover_unknown_writes = recover_unknown_writes
        self._tools = tools
        self._system_prompt = (
            files("min_agent").joinpath("prompts/system.md").read_text(encoding="utf-8")
            if tools is not None and tools.is_bound else None
        )
        self._max_tool_error_rounds = max_tool_error_rounds
        self.trace = trace if trace is not None else TraceRecorder()

    @staticmethod
    def _validate_input(text: str) -> None:
        if not isinstance(text, str):
            raise TypeError("输入必须是字符串")
        if not text.strip():
            raise ValueError("输入不能为空白")

    def run(self, text: str, *, trace_context: TraceContext | None = None,
            store: SessionStore | None = None, execution_context: ExecutionContext | None = None) -> RunResult:
        self._validate_input(text)
        if (store is None) != (execution_context is None):
            raise ValueError("隔离子循环必须同时绑定存储与执行归属")
        if store is not None:
            store.check_budget_binding(execution_context, self._budget)
            parent = store.snapshot(execution_context).task
            if parent is None or parent.status != "running":
                raise RuntimeError("隔离子循环需要活动的父任务")
            actual = TraceContext(session_id=execution_context.session_id, task_id=parent.task_id)
            if trace_context is not None and trace_context != actual:
                raise PermissionError("子循环trace归属不匹配")
            trace_context = actual
        elif self._budget is not None:
            self._budget.check_context()
        if self._tools is not None:
            self._tools.check_context(None)
        context = trace_context if trace_context is not None else TraceContext()
        if not isinstance(context, TraceContext):
            raise TypeError("trace_context 必须是 TraceContext")
        context = TraceContext.model_validate(context.model_dump(warnings=False))
        return self._run_traced(text, context, uuid4())

    def run_session(
        self, text: str, *, store: SessionStore, context: ExecutionContext,
        resume_checkpoint_id: UUID | None = None,
    ) -> RunResult:
        self._validate_input(text)
        if self._tools is not None:
            self._tools.check_context(context, store)
        store.check_budget_binding(context, self._budget)
        if self._control is not None:
            if self._control.sessions is not store or self._control.context != context:
                raise PermissionError("控制入口不属于当前会话")
        task = store.begin(context, checkpoint_id=resume_checkpoint_id, budget=self._budget)
        if self._control is not None:
            self._control.started(task.task_id)
        messages = store.snapshot(context).messages
        original_length = len(messages)
        messages.append(Message(role="user", content=text))
        run_id = uuid4()
        trace_context = TraceContext(session_id=context.session_id, task_id=task.task_id)
        try:
            result = self._run_traced(text, trace_context, run_id, messages)
        except BaseException as error:
            interrupted = not isinstance(error, Exception)
            store.append(context, task.task_id, messages[original_length:])
            store.finish(
                context, task.task_id, status="cancelled" if interrupted else "failed",
                run_id=run_id, error_code="interrupted" if interrupted else "unexpected_error",
            )
            raise
        if result.checkpoint is not None and result.checkpoint.budget_id is not None:
            store.finish(context, task.task_id, status=result.status, run_id=run_id, error_code=result.error_code,
                         messages=messages[original_length:], checkpoint=result.checkpoint)
        else:
            store.append(context, task.task_id, messages[original_length:])
            store.finish(context, task.task_id, status=result.status, run_id=run_id, error_code=result.error_code)
        if self._control is not None:
            self._control.finished(result.status)
        return result

    def resume_session(self, checkpoint_id: UUID, *, store: SessionStore, context: ExecutionContext,
                       text="继续已确认任务；依据已有结果，不重放成功操作。"):
        if not isinstance(checkpoint_id, UUID):
            raise TypeError("检查点ID必须是UUID")
        return self.run_session(text, store=store,
                                context=context, resume_checkpoint_id=checkpoint_id)

    def _run_traced(
        self, text: str, context: TraceContext, run_id: UUID, messages: list[Message] | None = None,
    ) -> RunResult:
        with self.trace.span("run", run_id=run_id, context=context) as run_span:
            result = self._run(text, context, run_id, run_span.call_id, messages)
            run_span.finish(result.status, result.error_code)
            return replace(result, run_id=run_id)

    def _run(
        self, text: str, context: TraceContext, run_id: UUID, parent_call_id: UUID,
        messages: list[Message] | None = None,
    ) -> RunResult:
        if messages is None:
            messages = [Message(role="user", content=text)]
        seen_call_ids: set[str] = set()
        error_rounds = 0
        had_request_errors = False
        progress_correction = False
        while True:
            if self._control is not None:
                stopped, steers = self._control.poll(context.task_id)
                if stopped:
                    return RunResult(status="cancelled", error_code="user_stopped")
                messages.extend(Message(role="user", content="用户显式引导：" + text) for text in steers)
            if self._checkpoint_policy is not None and (
                self._budget.remaining.model_calls <= self._checkpoint_policy.reserve_model_calls
            ):
                return self._close_checkpoint(messages, context, run_id, parent_call_id, "model_reserve")
            with self.trace.span(
                "model", run_id=run_id, context=context, parent_call_id=parent_call_id,
            ) as model_span:
                if self._budget is not None:
                    try:
                        self._budget.consume("model_calls", purpose="repair" if error_rounds else None)
                    except (BudgetExceeded, BudgetPersistenceError) as error:
                        code = "budget_persistence_failed" if isinstance(error, BudgetPersistenceError) else "budget_exhausted"
                        status = "failed" if isinstance(error, BudgetPersistenceError) else "partial"
                        model_span.finish(status, code, "not_started")
                        return RunResult(status=status, error_code=code)
                try:
                    model_messages = [message.model_copy(deep=True) for message in messages]
                    if self._system_prompt is not None:
                        state = self._tools.work_state()
                        model_messages = [
                            Message(role="system", content=self._system_prompt),
                            Message(role="user", content=json.dumps(
                                {"application_state": {**state.model_dump(mode="json"), "current_date": Clock().today().isoformat()}},
                                ensure_ascii=False, allow_nan=False,
                            )),
                        ] + model_messages
                    if self._tools is None:
                        response = self._model.generate(model_messages)
                    else:
                        response = self._model.generate(model_messages, tools=self._tools.schemas())
                except ContextLimit:
                    if self._control is not None and self._control.snapshot()["stop"]:
                        model_span.finish("cancelled", "user_stopped", "not_started")
                        return RunResult(status="cancelled", error_code="user_stopped")
                    model_span.finish("partial", "context_limit", "not_started")
                    return RunResult(status="partial", error_code="context_limit")
                except Exception:
                    # 异常正文不进入返回值或 trace。
                    model_span.finish("failed", "model_call_failed")
                    return RunResult(status="failed", error_code="model_call_failed")

                if not isinstance(response, ModelResponse):
                    model_span.finish("failed", "invalid_model_response")
                    return RunResult(status="failed", error_code="invalid_model_response")
                try:
                    # 先转回数据再解析，重新校验嵌套字段并切断可变引用。
                    response = ModelResponse.model_validate(response.model_dump(warnings="error"))
                except (ValueError, TypeError):
                    model_span.finish("failed", "invalid_model_response")
                    return RunResult(status="failed", error_code="invalid_model_response")
                if response.finish_reason == "length":
                    model_span.finish("partial", "output_limit")
                elif response.finish_reason in {"refusal", "error"}:
                    code = "model_refusal" if response.finish_reason == "refusal" else "model_error"
                    model_span.finish("failed", code)
                else:
                    model_span.finish("completed")

            if response.finish_reason != "tool_calls" and response.content is not None:
                messages.append(Message(
                    role="assistant", content=response.content, provider_state=response.provider_state,
                ))
            if response.finish_reason != "tool_calls" and self._control is not None and self._control.snapshot()["stop"]:
                return RunResult(status="cancelled", response=response, error_code="user_stopped")
            match response.finish_reason:
                case "stop":
                    if self._tools is not None and self._tools.is_bound:
                        state = self._tools.work_state()
                        if state.proposal is not None and state.confirmed_goal_id is None:
                            return RunResult(status="waiting_user", response=response, error_code="goal_unconfirmed")
                    if had_request_errors:
                        return RunResult(
                            status="partial", response=response, error_code="tool_request_failed"
                        )
                    return RunResult(status="completed", response=response)
                case "length":
                    return RunResult(status="partial", response=response, error_code="output_limit")
                case "tool_calls":
                    if self._tools is None:
                        return RunResult(
                            status="partial", response=response,
                            error_code="tool_execution_not_supported",
                        )
                    if any(call.id in seen_call_ids for call in response.tool_calls):
                        return RunResult(
                            status="failed", response=response, error_code="duplicate_tool_call_id"
                        )
                    messages.append(Message(
                        role="assistant", content=response.content or "",
                        tool_calls=response.tool_calls, provider_state=response.provider_state,
                    ))
                    had_request_errors = False
                    for index, call in enumerate(response.tool_calls):
                        seen_call_ids.add(call.id)
                        with self.trace.span(
                            "tool", run_id=run_id, context=context, parent_call_id=model_span.call_id,
                            source_call_id=call.id, tool_name=call.name,
                        ) as tool_span:
                            if self._control is not None:
                                control_state = self._control.snapshot()
                                stopped = control_state["stop"]
                                if stopped:
                                    tool_span.finish("cancelled", "user_stopped", "not_started")
                                    for blocked in response.tool_calls[index:]:
                                        messages.append(Message(role="tool", tool_call_id=blocked.id, content=json.dumps({
                                            "ok": False, "error": {"code": "user_stopped", "execution_state": "not_started"},
                                        })))
                                    return RunResult(status="cancelled", error_code="user_stopped")
                                if control_state["steers"]:
                                    tool_span.finish("partial", "steer_pending", "not_started")
                                    for blocked in response.tool_calls[index:]:
                                        seen_call_ids.add(blocked.id)
                                        messages.append(Message(role="tool", tool_call_id=blocked.id, content=json.dumps({
                                            "ok": False, "error": {"code": "steer_pending", "execution_state": "not_started"},
                                        })))
                                    break
                            if self._checkpoint_policy is not None and (
                                self._budget.remaining.tool_calls <= self._checkpoint_policy.reserve_tool_calls
                            ):
                                tool_span.finish("partial", "budget_checkpoint", "not_started")
                                for blocked in response.tool_calls[index:]:
                                    messages.append(Message(
                                        role="tool", tool_call_id=blocked.id,
                                        content=json.dumps({"ok": False, "error": {
                                            "code": "budget_checkpoint",
                                            "message": "已到工具收尾边界；该请求未执行。",
                                            "execution_state": "not_started",
                                            "recovery": "review_checkpoint",
                                        }}, ensure_ascii=False),
                                    ))
                                return self._close_checkpoint(messages, context, run_id, parent_call_id, "tool_reserve")
                            if self._budget is not None:
                                try:
                                    self._budget.consume("tool_calls", purpose="repair" if error_rounds else None)
                                except (BudgetExceeded, BudgetPersistenceError) as error:
                                    failed = isinstance(error, BudgetPersistenceError)
                                    code = "budget_persistence_failed" if failed else "budget_exhausted"
                                    status = "failed" if failed else "partial"
                                    recovery = "reload_budget_state" if failed else (
                                        "review_task_budget" if error.scope == "task" else "new_segment_budget"
                                    )
                                    tool_span.finish(status, code, "not_started")
                                    for blocked in response.tool_calls[index:]:
                                        messages.append(Message(
                                            role="tool", tool_call_id=blocked.id,
                                            content=json.dumps({"ok": False, "error": {
                                                "code": code,
                                                "message": "预算未放行；该请求未执行。",
                                                "scope": "storage" if failed else error.scope,
                                                "resource": "tool_calls",
                                                "execution_state": "not_started",
                                                "recovery": recovery,
                                            }}, ensure_ascii=False),
                                        ))
                                    return RunResult(status=status, response=response, error_code=code)
                            try:
                                identity = str(self._budget.binding.budget_id) if self._budget is not None and self._budget.binding else str(run_id)
                                outcome = self._tools.execute(call, operation_id=identity + ":" + call.id)
                                output = json.dumps(outcome, ensure_ascii=False, allow_nan=False)
                            except AuthorizationRequired as error:
                                tool_span.finish("waiting_user", error.code, "not_started")
                                # 停止整个批次；为当前及其余未执行请求配齐确定的结果。
                                for blocked in response.tool_calls[index:]:
                                    messages.append(Message(
                                        role="tool", tool_call_id=blocked.id,
                                        content=json.dumps({"ok": False, "error": {
                                            "code": error.code if blocked.id == call.id else "waiting_user",
                                            "message": "等待用户确认或模式选择；该请求未执行。",
                                            "execution_state": "not_started",
                                            "recovery": "directed_user_confirmation",
                                        }}, ensure_ascii=False),
                                    ))
                                return RunResult(status="waiting_user", response=response, error_code=error.code)
                            except InvalidToolResultError:
                                tool_span.finish("failed", "invalid_tool_result", "returned")
                                if self._recover_unknown_writes:
                                    for blocked in response.tool_calls[index:]:
                                        messages.append(Message(role="tool", tool_call_id=blocked.id,
                                            content=json.dumps({"ok": False, "error": {
                                                "code": "invalid_tool_result" if blocked.id == call.id else "not_started",
                                                "execution_state": "returned" if blocked.id == call.id else "not_started",
                                                "recovery": "verify_actual_state_before_retry",
                                            }})))
                                return RunResult(
                                    status="failed", response=response, error_code="invalid_tool_result"
                                )
                            except Exception:
                                tool_span.finish("failed", "tool_execution_failed", "unknown")
                                if self._recover_unknown_writes:
                                    for blocked in response.tool_calls[index:]:
                                        messages.append(Message(role="tool", tool_call_id=blocked.id,
                                            content=json.dumps({"ok": False, "error": {
                                                "code": "tool_execution_failed" if blocked.id == call.id else "not_started",
                                                "execution_state": "unknown" if blocked.id == call.id else "not_started",
                                                "recovery": "verify_actual_state_before_retry",
                                            }})))
                                return RunResult(
                                    status="failed", response=response, error_code="tool_execution_failed"
                                )
                            if outcome["ok"]:
                                tool_span.finish("completed", execution_state="returned")
                            else:
                                code = outcome["error"]["code"]
                                tool_span.finish("failed", code, "not_started")
                        messages.append(Message(role="tool", content=output, tool_call_id=call.id))
                        if call.name == "propose_goal":
                            for blocked in response.tool_calls[index + 1:]:
                                messages.append(Message(role="tool", tool_call_id=blocked.id, content=json.dumps({
                                    "ok": False, "error": {"code": "waiting_user", "execution_state": "not_started"},
                                })))
                            return RunResult(status="waiting_user", response=response, error_code="goal_unconfirmed")
                        if self._progress_guard is not None:
                            progress = self._progress_guard.observe(call, outcome)
                            if progress == "pause":
                                for blocked in response.tool_calls[index + 1:]:
                                    messages.append(Message(role="tool", tool_call_id=blocked.id,
                                        content=json.dumps({"ok": False, "error": {"code": "no_progress", "execution_state": "not_started"}})))
                                return RunResult(status="partial", error_code="no_progress")
                            if progress == "correct":
                                error_rounds = max(error_rounds, 1)
                                progress_correction = True
                        had_request_errors |= outcome["ok"] is False
                    if progress_correction:
                        messages.append(Message(role="user", content="应用检测到三次相同调用且无新结果；只允许一次有变化的纠错，再无进展将暂停。"))
                        progress_correction = False
                    if had_request_errors:
                        if error_rounds >= self._max_tool_error_rounds:
                            return RunResult(
                                status="partial", response=response, error_code="tool_repair_limit"
                            )
                        error_rounds += 1
                case "refusal":
                    return RunResult(status="failed", response=response, error_code="model_refusal")
                case _:
                    return RunResult(status="failed", response=response, error_code="model_error")

    def _close_checkpoint(self, messages, context, run_id, parent_call_id, reason) -> RunResult:
        """仅一次收尾尝试；额度/协议失败也保留真实结果引用，不执行收尾工具。"""
        note, response = None, None
        closing_status, closing_error = "not_started", None
        with self.trace.span("model", run_id=run_id, context=context,
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
                    model_messages = [message.model_copy(deep=True) for message in messages]
                    if self._system_prompt is not None:
                        state = self._tools.work_state()
                        model_messages = [
                            Message(role="system", content=self._system_prompt),
                            Message(role="user", content=json.dumps(
                                {"application_state": {**state.model_dump(mode="json"), "current_date": Clock().today().isoformat()}},
                                ensure_ascii=False, allow_nan=False,
                            )),
                        ] + model_messages
                    model_messages.append(Message(role="user", content=CLOSING_INSTRUCTION))
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
        if stopped: reason = "user_stopped"
        binding = self._budget.binding
        checkpoint = Checkpoint(
            segment_id=self._budget.segment_id, budget_id=binding.budget_id if binding else None,
            session_id=context.session_id, reason=reason, closing_status=closing_status,
            closing_error=closing_error, model_note=note, tool_results=tuple(refs),
            history_length=len(messages), task_usage=self._budget._ledger.usage,
            segment_usage=self._budget.usage,
        )
        failed = closing_error == "budget_persistence_failed"
        return RunResult(status="failed" if failed else "cancelled" if stopped else "partial", response=response,
                         error_code="user_stopped" if stopped and not failed else
                         closing_error if closing_status == "not_started" else "budget_checkpoint",
                         checkpoint=checkpoint)
