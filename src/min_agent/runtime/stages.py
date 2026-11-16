"""运行阶段的组件：控制门、预算预留门、模型调用、响应分派。

每个组件统一签名 ``(state) -> RunResult | None``：返回结果表示本轮终止，
返回 ``None`` 表示交给下一阶段。错误判断与消息追加都在组件内完成。
"""

from min_agent.ledger.budget import BudgetExceeded, BudgetPersistenceError
from min_agent.context.manager import ContextLimit
from min_agent.contracts.models import Message, ModelResponse
from min_agent.runtime.errors import model_error_code
from min_agent.runtime.state import ModelRound, RunResult, RunState, ToolRequest


class ControlGate:
    """模型请求前消费用户控制：停止即取消，引导变成显式用户消息。"""

    def __init__(self, control) -> None:
        self._control = control

    def __call__(self, state: RunState) -> RunResult | None:
        if self._control is None:
            return None
        stopped, steers = self._control.poll(state.context.task_id)
        if stopped:
            return RunResult(status="cancelled", error_code="user_stopped")
        state.messages.extend(
            Message(role="user", content="用户显式引导：" + text) for text in steers)
        return None


class ReserveGate:
    """模型调用额度到达软阈值时，只做一次收尾，不执行工具。"""

    def __init__(self, budget, policy, closer) -> None:
        self._budget = budget
        self._policy = policy
        self._closer = closer

    def __call__(self, state: RunState) -> RunResult | None:
        if self._policy is None or (
            self._budget.remaining.model_calls > self._policy.reserve_model_calls
        ):
            return None
        return self._closer.close(
            state.messages, state.context, state.run_id, state.parent_call_id, "model_reserve")


class ModelCall:
    """一次模型请求：预算扣减、异常映射、响应重校验与 span 结束。"""

    def __init__(self, model, tools, budget, control, trace, prompt) -> None:
        self._model = model
        self._tools = tools
        self._budget = budget
        self._control = control
        self._trace = trace
        self._prompt = prompt

    def __call__(self, state: RunState) -> RunResult | None:
        with self._trace.span(
            "model", run_id=state.run_id, context=state.context,
            parent_call_id=state.parent_call_id,
        ) as span:
            if self._budget is not None:
                try:
                    self._budget.consume("model_calls", purpose="repair" if state.error_rounds else None)
                except (BudgetExceeded, BudgetPersistenceError) as error:
                    code = "budget_persistence_failed" if isinstance(error, BudgetPersistenceError) else "budget_exhausted"
                    status = "failed" if isinstance(error, BudgetPersistenceError) else "partial"
                    span.finish(status, code, "not_started")
                    return RunResult(status=status, error_code=code)
            try:
                model_messages = self._prompt(state.messages)
                if self._tools is None:
                    response = self._model.generate(model_messages)
                else:
                    response = self._model.generate(model_messages, tools=self._tools.schemas())
            except ContextLimit:
                if self._control is not None and self._control.snapshot()["stop"]:
                    span.finish("cancelled", "user_stopped", "not_started")
                    return RunResult(status="cancelled", error_code="user_stopped")
                span.finish("partial", "context_limit", "not_started")
                return RunResult(status="partial", error_code="context_limit")
            except Exception as error:
                # 具体成因（如 http_402/network_error）经白名单码返回给会话；
                # 异常正文一律不进入返回值或 trace。
                code = model_error_code(error)
                span.finish("failed", "model_call_failed")
                return RunResult(status="failed", error_code=code)
            if not isinstance(response, ModelResponse):
                span.finish("failed", "invalid_model_response")
                return RunResult(status="failed", error_code="invalid_model_response")
            try:
                # 先转回数据再解析，重新校验嵌套字段并切断可变引用。
                response = ModelResponse.model_validate(response.model_dump(warnings="error"))
            except (ValueError, TypeError):
                span.finish("failed", "invalid_model_response")
                return RunResult(status="failed", error_code="invalid_model_response")
            if response.finish_reason == "length":
                span.finish("partial", "output_limit")
            elif response.finish_reason in {"refusal", "error"}:
                code = "model_refusal" if response.finish_reason == "refusal" else "model_error"
                span.finish("failed", code)
            else:
                span.finish("completed")
        state.round = ModelRound(response=response, span_call_id=span.call_id)
        return None


class ResponseRouter:
    """按 finish_reason 决定终止还是进入工具批次；不执行任何工具。"""

    def __init__(self, control, tools) -> None:
        self._control = control
        self._tools = tools

    def __call__(self, state: RunState) -> RunResult | None:
        current = state.round
        response = current.response
        if response.finish_reason != "tool_calls" and response.content is not None:
            state.messages.append(Message(
                role="assistant", content=response.content, provider_state=response.provider_state,
            ))
        if response.finish_reason != "tool_calls" and self._control is not None \
                and self._control.snapshot()["stop"]:
            return RunResult(status="cancelled", response=response, error_code="user_stopped")
        match response.finish_reason:
            case "stop":
                if self._tools is not None and self._tools.is_bound:
                    work = self._tools.work_state()
                    if work.proposal is not None and work.confirmed_goal_id is None:
                        return RunResult(status="waiting_user", response=response, error_code="goal_unconfirmed")
                if state.had_request_errors:
                    return RunResult(status="partial", response=response, error_code="tool_request_failed")
                return RunResult(status="completed", response=response)
            case "length":
                return RunResult(status="partial", response=response, error_code="output_limit")
            case "tool_calls":
                if self._tools is None:
                    return RunResult(
                        status="partial", response=response, error_code="tool_execution_not_supported")
                if any(call.id in state.seen_call_ids for call in response.tool_calls):
                    return RunResult(
                        status="failed", response=response, error_code="duplicate_tool_call_id")
                state.messages.append(Message(
                    role="assistant", content=response.content or "",
                    tool_calls=response.tool_calls, provider_state=response.provider_state,
                ))
                state.had_request_errors = False
                state.request = ToolRequest(response=response, span_call_id=current.span_call_id)
                return None
            case "refusal":
                return RunResult(status="failed", response=response, error_code="model_refusal")
            case _:
                return RunResult(status="failed", response=response, error_code="model_error")
