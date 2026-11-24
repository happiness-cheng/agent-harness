"""工具批次阶段：逐条门控、执行、错误映射与进度纠错。

主循环看不到任何工具错误分支；这里返回 ``RunResult`` 表示本轮终止，
返回 ``None`` 表示工具结果已配对，可以继续下一轮模型请求。
"""

import json

from min_agent.ledger.budget import BudgetExceeded, BudgetPersistenceError
from min_agent.contracts.models import Message
from min_agent.runtime.errors import (
    blocked, budget_blocked, budget_checkpoint, needs_recovery, trace_error_code, waiting_user,
)
from min_agent.runtime.state import RunResult, RunState
from min_agent.session.task_state import AuthorizationRequired
from min_agent.tools.registry import InvalidToolResultError


class ToolBatch:
    """按模型给出顺序串行执行一组工具请求；执行异常停止整批。"""

    def __init__(self, tools, control, budget, policy, trace, progress_guard, closer,
                 *, recover_unknown_writes: bool, max_tool_error_rounds: int,
                 gate=None) -> None:
        self._tools = tools
        self._control = control
        self._budget = budget
        self._policy = policy
        self._trace = trace
        self._progress_guard = progress_guard
        self._closer = closer
        self._recover_unknown_writes = recover_unknown_writes
        self._max_tool_error_rounds = max_tool_error_rounds
        self._gate = gate

    def __call__(self, state: RunState) -> RunResult | None:
        if state.request is None:
            return None
        response = state.request.response
        calls = response.tool_calls
        for index, call in enumerate(calls):
            state.seen_call_ids.add(call.id)
            with self._trace.span(
                "tool", run_id=state.run_id, context=state.context,
                parent_call_id=state.request.span_call_id,
                source_call_id=call.id, tool_name=call.name,
            ) as span:
                if self._control is not None:
                    control_state = self._control.snapshot()
                    if control_state["stop"]:
                        span.finish("cancelled", "user_stopped", "not_started")
                        state.messages.extend(
                            blocked(item.id, code="user_stopped") for item in calls[index:])
                        return RunResult(status="cancelled", error_code="user_stopped")
                    if control_state["steers"]:
                        span.finish("partial", "steer_pending", "not_started")
                        for item in calls[index:]:
                            state.seen_call_ids.add(item.id)
                            state.messages.append(blocked(item.id, code="steer_pending"))
                        break
                if self._policy is not None and (
                    self._budget.remaining.tool_calls <= self._policy.reserve_tool_calls
                ):
                    span.finish("partial", "budget_checkpoint", "not_started")
                    state.messages.extend(budget_checkpoint(item.id) for item in calls[index:])
                    return self._closer.close(
                        state.messages, state.context, state.run_id,
                        state.parent_call_id, "tool_reserve")
                if self._budget is not None:
                    try:
                        self._budget.consume("tool_calls", purpose="repair" if state.error_rounds else None)
                    except (BudgetExceeded, BudgetPersistenceError) as error:
                        failed = isinstance(error, BudgetPersistenceError)
                        code = "budget_persistence_failed" if failed else "budget_exhausted"
                        status = "failed" if failed else "partial"
                        recovery = "reload_budget_state" if failed else (
                            "review_task_budget" if error.scope == "task" else "new_segment_budget"
                        )
                        span.finish(status, code, "not_started")
                        scope = "storage" if failed else error.scope
                        state.messages.extend(
                            budget_blocked(item.id, code=code, scope=scope, recovery=recovery)
                            for item in calls[index:])
                        return RunResult(status=status, response=response, error_code=code)
                gate_denied = False
                try:
                    # 执行前分级门（sidecar）：串行、fail-closed。拒绝理由写回
                    # 工具消息；高危待确认时 gate 抛 AuthorizationRequired，走下方停批。
                    if self._gate is not None:
                        decision = self._gate.check(call)
                        if not decision.allowed:
                            gate_denied = True
                            outcome = {"ok": False, "error": {
                                "code": "gate_denied", "message": decision.reason,
                                "level": decision.level, "decider": decision.decider,
                                "execution_state": "not_started",
                                "recovery": "choose_safer_alternative"}}
                            output = self._tools.serialize(outcome)
                    if not gate_denied:
                        identity = str(self._budget.binding.budget_id) \
                            if self._budget is not None and self._budget.binding else str(state.run_id)
                        outcome = self._tools.execute(call, operation_id=identity + ":" + call.id)
                        output = self._tools.serialize(outcome)
                except AuthorizationRequired as error:
                    span.finish("waiting_user", trace_error_code(error.code), "not_started")
                    # 停止整个批次；为当前及其余未执行请求配齐确定的结果。
                    for item in calls[index:]:
                        code = error.code if item.id == call.id else "waiting_user"
                        state.messages.append(waiting_user(item.id, code=code))
                    return RunResult(status="waiting_user", response=response, error_code=error.code)
                except InvalidToolResultError:
                    span.finish("failed", "invalid_tool_result", "returned")
                    if self._recover_unknown_writes:
                        state.messages.extend(
                            needs_recovery(
                                item.id,
                                code="invalid_tool_result" if item.id == call.id else "not_started",
                                execution_state="returned" if item.id == call.id else "not_started",
                            ) for item in calls[index:])
                    return RunResult(
                        status="failed", response=response, error_code="invalid_tool_result")
                except Exception:
                    span.finish("failed", "tool_execution_failed", "unknown")
                    if self._recover_unknown_writes:
                        state.messages.extend(
                            needs_recovery(
                                item.id,
                                code="tool_execution_failed" if item.id == call.id else "not_started",
                                execution_state="unknown" if item.id == call.id else "not_started",
                            ) for item in calls[index:])
                    return RunResult(
                        status="failed", response=response, error_code="tool_execution_failed")
                if outcome["ok"]:
                    span.finish("completed", execution_state="returned")
                else:
                    span.finish("failed", outcome["error"]["code"], "not_started")
            state.messages.append(Message(role="tool", content=output, tool_call_id=call.id))
            if call.name == "propose_goal":
                state.messages.extend(
                    blocked(item.id, code="waiting_user") for item in calls[index + 1:])
                return RunResult(status="waiting_user", response=response, error_code="goal_unconfirmed")
            if self._progress_guard is not None:
                progress = self._progress_guard.observe(call, outcome)
                if progress == "pause":
                    state.messages.extend(
                        blocked(item.id, code="no_progress") for item in calls[index + 1:])
                    return RunResult(status="partial", error_code="no_progress")
                if progress == "correct":
                    state.error_rounds = max(state.error_rounds, 1)
                    state.progress_correction = True
            state.had_request_errors |= outcome["ok"] is False
        if state.progress_correction:
            state.messages.append(Message(
                role="user",
                content="应用检测到三次相同调用且无新结果；只允许一次有变化的纠错，再无进展将暂停。",
            ))
            state.progress_correction = False
        if state.had_request_errors:
            if state.error_rounds >= self._max_tool_error_rounds:
                return RunResult(status="partial", response=response, error_code="tool_repair_limit")
            state.error_rounds += 1
        state.request = None
        return None
