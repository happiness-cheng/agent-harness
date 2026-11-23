"""直接回答与串行工具请求的同步执行引擎。

本文件只做编排：构造校验、会话任务闭合，以及一个按固定顺序调用六个阶段
的主循环。错误判断、消息构造、收尾与提示词投影都在 ``runtime/`` 下的组件里。
"""

from collections.abc import Callable
from dataclasses import replace
from importlib.resources import files
from uuid import UUID, uuid4

from min_agent.ledger.budget import BudgetSegment
from min_agent.ledger.checkpoints import CheckpointPolicy
from min_agent.contracts.models import Message
from min_agent.runtime.closing import CheckpointCloser
from min_agent.runtime.errors import trace_error_code
from min_agent.runtime.prompt import PromptView
from min_agent.runtime.stages import ControlGate, ModelCall, ReserveGate, ResponseRouter, RoundLimit
from min_agent.runtime.state import Model, RunResult, RunState
from min_agent.runtime.tool_loop import ToolBatch
from min_agent.session.store import ExecutionContext, SessionStore
from min_agent.contracts.trace import TraceContext, TraceRecorder


class Runtime:
    """同步模型循环；只依赖调用方注入的模型、工具、预算与控制对象。"""

    def __init__(
        self, model: Model, tools=None, *, max_tool_error_rounds: int = 2,
        max_rounds: int = 64,
        trace: TraceRecorder | None = None, budget: BudgetSegment | None = None,
        checkpoint_policy: CheckpointPolicy | None = None,
        control=None, progress_guard=None,
        recover_unknown_writes: bool = False,
        before_execute=None,
        system_prompt: str | Callable[[], str] | None = None,
        show_state: bool = True,
        tool_gate=None,
    ) -> None:
        if type(max_tool_error_rounds) is not int:
            raise TypeError("工具错误修正轮数必须是整数")
        if max_tool_error_rounds < 0:
            raise ValueError("工具错误修正轮数不能为负")
        if type(max_rounds) is not int:
            raise TypeError("轮数上限必须是整数")
        if max_rounds < 1:
            raise ValueError("轮数上限必须为正")
        if budget is not None and not isinstance(budget, BudgetSegment):
            raise TypeError("budget必须是可信调用方提供的BudgetSegment")
        if checkpoint_policy is not None:
            if not isinstance(checkpoint_policy, CheckpointPolicy):
                raise TypeError("checkpoint_policy必须是CheckpointPolicy")
            if budget is None:
                raise ValueError("收尾策略需要显式共享预算")
            checkpoint_policy = CheckpointPolicy.model_validate(checkpoint_policy.model_dump())
        if type(recover_unknown_writes) is not bool:
            raise TypeError("未知写入恢复开关必须为布尔值")
        if before_execute is not None and not callable(before_execute):
            raise TypeError("before_execute必须是可调用钩子")
        if system_prompt is not None and not isinstance(system_prompt, str) \
                and not callable(system_prompt):
            raise TypeError("system_prompt 必须是字符串或可调用对象")
        if type(show_state) is not bool:
            raise TypeError("show_state 必须是布尔值")
        if tool_gate is not None and not callable(getattr(tool_gate, "check", None)):
            raise TypeError("tool_gate 必须提供 check(call) 方法")
        self._budget = budget
        self._control = control
        self._before_execute = before_execute
        self._tools = tools
        self.trace = trace if trace is not None else TraceRecorder()
        if system_prompt is None:
            system_prompt = (
                files("min_agent").joinpath("prompts/system.md").read_text(encoding="utf-8")
                if tools is not None and tools.is_bound else None
            )
        prompt = PromptView(tools, budget, system_prompt, show_state=show_state)
        closer = CheckpointCloser(budget, control, model, self.trace, prompt)
        self._stages = (
            ControlGate(control),
            RoundLimit(max_rounds),
            ReserveGate(budget, checkpoint_policy, closer),
            ModelCall(model, tools, budget, control, self.trace, prompt),
            ResponseRouter(control, tools),
            ToolBatch(
                tools, control, budget, checkpoint_policy, self.trace, progress_guard, closer,
                recover_unknown_writes=recover_unknown_writes,
                max_tool_error_rounds=max_tool_error_rounds,
                gate=tool_gate,
            ),
        )

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
        resume_checkpoint_id: UUID | None = None, resume_task_id: UUID | None = None,
    ) -> RunResult:
        self._validate_input(text)
        if resume_checkpoint_id is not None and resume_task_id is not None:
            raise ValueError("checkpoint_id 与 resume_task_id 不能同时指定")
        if resume_task_id is not None and not isinstance(resume_task_id, UUID):
            raise TypeError("resume_task_id 必须是UUID")
        if self._tools is not None:
            self._tools.check_context(context, store)
        store.check_budget_binding(context, self._budget)
        if self._control is not None:
            if self._control.sessions is not store or self._control.context != context:
                raise PermissionError("控制入口不属于当前会话")
        task = store.begin(context, checkpoint_id=resume_checkpoint_id,
                           resume_task_id=resume_task_id, budget=self._budget)
        if self._control is not None:
            self._control.started(task.task_id)
        if self._before_execute is not None:
            try:
                self._before_execute(store, context, task)
            except BaseException:
                # 冻结/执行前钩子失败也必须闭合任务，不能留下 running 假状态。
                store.finish(context, task.task_id, status="failed", error_code="unexpected_error")
                if self._control is not None:
                    self._control.finished("failed")
                raise
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
            run_span.finish(result.status, trace_error_code(result.error_code))
            return replace(result, run_id=run_id)

    def _run(
        self, text: str, context: TraceContext, run_id: UUID, parent_call_id: UUID,
        messages: list[Message] | None = None,
    ) -> RunResult:
        """六个阶段按固定顺序推进；任一阶段返回结果即终止本轮。"""
        if messages is None:
            messages = [Message(role="user", content=text)]
        state = RunState(context=context, run_id=run_id,
                         parent_call_id=parent_call_id, messages=messages)
        while True:
            for stage in self._stages:
                result = stage(state)
                if result is not None:
                    return result
