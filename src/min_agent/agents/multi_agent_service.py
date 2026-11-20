"""MA-P5-013：Multi-Agent 生产接入层（**默认关闭、由可信 host 显式构造**）。

本模块把「Manager/Worker」接到生产 Runtime 的一次工具执行里（DECISION_LOG → MA-P5-013
D8–D14）。它自己**不注册任何入口**：`application.py` 只在
``enable_multi_agent=True`` 且仍有可信工厂时才注册同步工具 ``delegate_task``，而
该工具的处理函数在每次调用时创建一个 :class:`MultiAgentService`，跑完即释放。

三条硬边界：

1. **不是一个新会话控制循环**。Manager 不调 ``SessionEvents.started()``；它的
   ``ThreadPoolExecutor`` 只在一次工具执行内部并行 worker。停止只**读**
   ``control.snapshot()["stop"]``，**不消费**主循环 steers，也不调
   ``started`` / ``finished`` / ``poll``。因此 ``events.py`` 一行不用改。
2. **权限不重写**。工具执行复用生产 ``ToolRegistry.execute`` / ``execute_once`` /
   SQLite CAS / Plan-Build 授权；本模块只做「每次调用前的上下文与范围复核」和
   「白名单交集」，**不另写一套权限判断**。
3. **预算不重复扣**。外层 Runtime 已把 ``delegate_task`` 本身计为一次工具调用；
   内部只消耗**子预算**（``parent_budget.child(...)``），不回头扣外层片段。

明确不做（不得当作已实现）：

* 不做跨会话恢复：结果快照只**保存现场**，不恢复 ``in_progress`` 任务、不重放
  ``unknown`` 工具、不恢复 ``provider_state``；
* 不做 ``shared`` 上下文的生产接线——:class:`min_agent.agents.context_policy.ContextPolicy`
  支持它，但生产提示词链路没有可靠的历史通道，本批**显式拒绝**而不是假装支持
  （见 :class:`MultiAgentService` 构造期的 ``shared_context_not_wired``）；
* ``provider_state`` 只在**同一个适配器实例内**按 assistant 出现顺序重绑；换实例、
  重放或跨轮恢复都不保证；它不进入 Mailbox、日志或快照；
* ``usage`` 拿不到时按预占上界记并标 ``usage_source="reserved_upper_bound"``，
  **不是真实用量**；``http_status`` 不可知时保持 ``None``，**不伪造 200**。
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Sequence
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from min_agent.agents.agent_messages import PointToPointMailbox
from min_agent.ledger.budget import BudgetLimits, BudgetSegment
from min_agent.agents.context_policy import ContextMode, ContextPolicy
from min_agent.agents.manager import (
    Manager, ManagerRun, ModelProfile, ToolExecutionResult, ToolExecutor,
    WorkerSpec,
)
from min_agent.contracts.models import Message, ModelResponse, ProviderState, ToolCall
from min_agent.ledger.resource_budget import PriceTable, ResourceLedger, ResourceLimits
from min_agent.session.store import ExecutionContext, SessionStore
from min_agent.agents.subagent import receipt_ref
from min_agent.agents.subagent_collab import (
    BackendMode, ChatResult, ChatTransport,
)
from min_agent.agents.subagent_state import LifecycleRegistry
from min_agent.session.task_state import AuthorizationRequired
from min_agent.tools.registry import ToolRegistry

__all__ = [
    "DelegateRequest", "ProductionModelTransport", "ScopedToolExecutor",
    "MultiAgentConfig", "MultiAgentService", "DEFAULT_DENIED_TOOLS",
]

DEFAULT_DENIED_TOOLS: frozenset[str] = frozenset({"delegate_task", "research"})
"""生产 worker 默认禁用的工具：前者防递归委派，后者默认不纳入（计划 §5.6）。"""

_USAGE_KEYS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("prompt_tokens", ("prompt_tokens", "input_tokens", "prompt_eval_count")),
    ("completion_tokens", ("completion_tokens", "output_tokens", "eval_count")),
    ("total_tokens", ("total_tokens",)),
)
"""不同提供商的 usage 键名归一化（OpenAI / DeepSeek / Ollama）。"""


class _Strict(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class DelegateRequest(_Strict):
    """模型能提交的全部内容：只有一个有界目标字符串。

    **没有** owner / session / budget / 角色 / 工具字段——这些只能来自可信闭包。
    """

    objective: str = Field(min_length=1, max_length=2000)


class ProductionModelTransport:
    """把生产 ``Model`` 适配成 :class:`ChatTransport`（**构造不发请求**）。

    * ``max_output_tokens`` 必须来自可信配置，且与调用方请求的 ``max_tokens``
      一致；不一致直接失败，**不静默忽略**（计划 §5.6）。
    * 异常只映射成固定安全错误码，不保留响应正文或凭据。
    * ``vendor`` / ``model_id`` 由可信调用方显式声明，用于独立性机械判定。
    """

    def __init__(self, model, *, vendor: str, model_id: str,
                 max_output_tokens: int, mode: BackendMode = "online") -> None:
        if not isinstance(vendor, str) or not vendor.strip():
            raise ValueError("vendor 必须是非空白字符串")
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError("model_id 必须是非空白字符串")
        if isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int) \
                or not 1 <= max_output_tokens <= 8192:
            raise ValueError("max_output_tokens 必须在 1..8192 之间")
        if mode not in ("offline", "online"):
            raise ValueError("mode 只能是 offline 或 online")
        if not callable(getattr(model, "generate", None)):
            raise TypeError("model 必须实现 generate(messages, tools=...)")
        self._model = model
        self._vendor = vendor.strip()
        self._model_id = model_id.strip()
        self._max_output_tokens = max_output_tokens
        self._mode = mode
        self._calls = 0
        self._provider_states: list[ProviderState | None] = []
        self._closed = False

    @property
    def mode(self) -> BackendMode:
        return self._mode

    @property
    def calls(self) -> int:
        return self._calls

    @property
    def vendor(self) -> str | None:
        return self._vendor

    @property
    def model_id(self) -> str | None:
        return self._model_id

    @property
    def max_output_tokens(self) -> int:
        return self._max_output_tokens

    def close(self) -> None:
        """释放适配器自身状态；**不关闭底层 model**（其生命周期归应用）。"""
        self._closed = True
        self._provider_states.clear()

    def _normalized_usage(self) -> dict[str, int] | None:
        raw = getattr(self._model, "usage", None)
        if not isinstance(raw, Mapping):
            return None
        out: dict[str, int] = {}
        for target, candidates in _USAGE_KEYS:
            for name in candidates:
                value = raw.get(name)
                if isinstance(value, int) and not isinstance(value, bool):
                    out[target] = value
                    break
        if "total_tokens" not in out and ("prompt_tokens" in out or "completion_tokens" in out):
            out["total_tokens"] = out.get("prompt_tokens", 0) + out.get("completion_tokens", 0)
        return out or None

    def _to_messages(self, messages: Sequence[Mapping[str, object]]) -> list[Message]:
        """普通 dict 消息 → :class:`Message`；``provider_state`` 按 assistant 顺序重绑。"""
        converted: list[Message] = []
        assistant_index = 0
        for item in messages:
            role = item.get("role")
            content = str(item.get("content", ""))
            if role == "tool":
                call_id = item.get("tool_call_id")
                if not isinstance(call_id, str) or not call_id.strip():
                    raise ValueError("tool_message_without_call_id")
                converted.append(Message(role="tool", content=content,
                                         tool_call_id=call_id))
                continue
            if role == "assistant":
                raw_calls = item.get("tool_calls") or []
                if not isinstance(raw_calls, (list, tuple)):
                    raise ValueError("assistant_tool_calls_not_sequence")
                calls = [
                    ToolCall(id=str(call["id"]), name=str(call["name"]),
                             arguments=dict(call["arguments"]))
                    for call in raw_calls
                ]
                state = (self._provider_states[assistant_index]
                         if assistant_index < len(self._provider_states) else None)
                assistant_index += 1
                converted.append(Message(role="assistant", content=content,
                                         tool_calls=calls, provider_state=state))
                continue
            if role in ("system", "user"):
                converted.append(Message(role=role, content=content))
                continue
            raise ValueError("unknown_message_role")
        return converted

    def complete(self, messages: Sequence[Mapping[str, object]],
                 max_tokens: int, *,
                 tools: list[dict[str, object]] | None = None) -> ChatResult:
        if self._closed:
            return ChatResult(finish_reason="error", error="transport_closed")
        if max_tokens != self._max_output_tokens:
            # 输出上限必须与可信配置一致；不一致宁可失败，也不悄悄截断或放宽。
            raise ValueError("max_tokens_mismatch")
        self._calls += 1
        try:
            converted = self._to_messages(messages)
        except (ValueError, TypeError, KeyError):
            return ChatResult(finish_reason="error", error="invalid_messages")
        try:
            if tools:
                response = self._model.generate(converted, tools=list(tools))
            else:
                response = self._model.generate(converted)
        except TimeoutError:
            return ChatResult(finish_reason="error", error="model_timeout")
        except Exception:
            # 固定错误码：不把异常正文（可能含端点/凭据片段）带出去。
            return ChatResult(finish_reason="error", error="model_call_failed")
        if not isinstance(response, ModelResponse):
            return ChatResult(finish_reason="error", error="invalid_model_response")
        try:
            response = ModelResponse.model_validate(response.model_dump(warnings="error"))
        except (ValueError, TypeError):
            return ChatResult(finish_reason="error", error="invalid_model_response")
        self._provider_states.append(response.provider_state)
        if response.finish_reason == "tool_calls":
            finish = "tool_calls"
        elif response.finish_reason == "length":
            finish = "length"
        elif response.finish_reason in ("refusal", "error"):
            return ChatResult(
                finish_reason="error",
                error="model_refusal" if response.finish_reason == "refusal"
                else "model_error",
                usage=self._normalized_usage(),
            )
        else:
            finish = "stop"
        return ChatResult(
            content=response.content or "",
            finish_reason=finish,
            # HTTP 状态在 Model 接口上不可知：保持 None，**不伪造 200**。
            http_status=None,
            usage=self._normalized_usage(),
            tool_calls=tuple(response.tool_calls),
        )


class ScopedToolExecutor:
    """把生产工具面收窄到 ``WorkerSpec.allowed_tools`` 的交集后再执行。

    每次执行前做的是**复核**，不是另写一套权限：会话归属、存储版本、工具集绑定
    三项与生产 Runtime 一致；真正的业务权限仍由工具 handler / ``execute_once`` /
    SQLite CAS / Plan-Build 授权决定。
    """

    def __init__(self, registry: ToolRegistry, sessions: SessionStore,
                 context: ExecutionContext, allowed_tools: tuple[str, ...], *,
                 check_names: Mapping[str, str] | None = None,
                 denied_tools: frozenset[str] = DEFAULT_DENIED_TOOLS) -> None:
        if not isinstance(registry, ToolRegistry):
            raise TypeError("registry 必须是 ToolRegistry")
        if not isinstance(sessions, SessionStore):
            raise TypeError("sessions 必须是 SessionStore")
        cleaned = tuple(dict.fromkeys(allowed_tools))
        if not cleaned:
            raise ValueError("allowed_tools 不能为空")
        forbidden = sorted(set(cleaned) & set(denied_tools))
        if forbidden:
            raise ValueError(f"tool_not_allowed_in_production:{','.join(forbidden)}")
        self._registry = registry
        self._sessions = sessions
        self._context = sessions.check_access(context)
        self._allowed = frozenset(cleaned)
        self._check_names = dict(check_names or {})
        self._denied = frozenset(denied_tools)

    @property
    def allowed_tools(self) -> tuple[str, ...]:
        return tuple(sorted(self._allowed))

    def schemas(self, allowed_tools: tuple[str, ...]) -> list[dict[str, object]]:
        """只暴露**白名单交集内的**工具 Schema；未注册的工具静默不存在。"""
        wanted = frozenset(allowed_tools) & self._allowed
        return [item for item in self._registry.schemas()
                if item.get("name") in wanted]

    def _request_error(self, code: str) -> dict[str, object]:
        return {"ok": False, "error": {
            "code": code, "message": "该请求未执行。",
            "execution_state": "not_started",
            "recovery": "correct_request_with_new_id",
        }}

    def execute(self, call: ToolCall, *, operation_id: str) -> ToolExecutionResult:
        if not isinstance(call, ToolCall):
            raise TypeError("call 必须是 ToolCall")
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError("operation_id 必须是非空白字符串")
        # 白名单在契约层已校验；这里是第二道门，**返请求级错误而不是拒绝副作用**。
        if call.name not in self._allowed or call.name in self._denied:
            outcome = self._request_error("tool_not_allowed")
            receipt = json.dumps(outcome, ensure_ascii=False,
                                 sort_keys=True).encode("utf-8")
            return ToolExecutionResult(outcome=outcome,
                                       execution_state="not_started",
                                       raw_receipt=receipt, host_checks={})
        # 上下文与版本复核：与生产 Runtime 一致，不另写权限判断。
        self._sessions.check_access(self._context)
        self._sessions.authoritative_snapshot(self._context)
        self._registry.check_context(self._context, self._sessions)
        outcome = self._registry.execute(call, operation_id=operation_id)
        receipt = json.dumps(outcome, ensure_ascii=False,
                             sort_keys=True).encode("utf-8")
        ok = outcome.get("ok") is True
        error = outcome.get("error")
        state = "returned" if ok else (
            "not_started"
            if isinstance(error, Mapping) and error.get("execution_state") == "not_started"
            else "unknown"
        )
        checks: dict[str, str] = {}
        if ok:
            # 检查项名默认 = 工具名；可信调用方可显式改名（D13）。
            checks[self._check_names.get(call.name, call.name)] = receipt_ref(receipt)
        return ToolExecutionResult(outcome=outcome, execution_state=state,
                                   raw_receipt=receipt, host_checks=checks)


@dataclass(frozen=True)
class MultiAgentConfig:
    """可信配置：所有能扩大能力的字段都只能由 host 提供，模型参数碰不到。"""

    manager_profile: ModelProfile
    manager_transport: ChatTransport
    worker_profiles: Mapping[str, ModelProfile]
    worker_specs: Mapping[str, WorkerSpec]
    worker_transport_factory: Callable[[str], ChatTransport]
    resource_limits: ResourceLimits
    max_workers: int = 1
    max_subtasks: int = 6
    delegate_model_calls: int = 14
    delegate_tool_calls: int = 2
    price_table: PriceTable | None = None
    task_directory: Path | None = None
    context_mode: ContextMode = "isolated"
    tool_check_names: Mapping[str, str] = field(default_factory=dict)
    progress_directory: Path | None = None


class MultiAgentService:
    """一次 ``delegate_task`` 调用对应一个实例；``close()`` 之后不可复用。"""

    def __init__(self, config: MultiAgentConfig, *,
                 sessions: SessionStore, context: ExecutionContext,
                 parent_budget: BudgetSegment, registry: ToolRegistry,
                 control: object | None = None) -> None:
        if not isinstance(config, MultiAgentConfig):
            raise TypeError("config 必须是 MultiAgentConfig")
        if not isinstance(sessions, SessionStore):
            raise TypeError("sessions 必须是 SessionStore")
        if not isinstance(parent_budget, BudgetSegment):
            raise TypeError("parent_budget 必须是可信的 BudgetSegment")
        if not isinstance(registry, ToolRegistry):
            raise TypeError("registry 必须是 ToolRegistry")
        self._config = config
        self._sessions = sessions
        self._context = sessions.check_access(context)
        self._parent_budget = parent_budget
        self._registry = registry
        self._control = control
        self._context_policy = ContextPolicy(mode=config.context_mode)
        if self._context_policy.mode != "isolated":
            # 显式拒绝：生产提示词链路没有可信历史通道，假装支持会制造错误上下文。
            raise NotImplementedError("shared_context_not_wired")
        self._closed = False
        self._run_id = uuid4().hex
        self._temp: tempfile.TemporaryDirectory | None = None
        self._owned_transports: list[ChatTransport] = []
        self._abort_reason = ""
        self._resources: ResourceLedger | None = None
        self._lifecycle: LifecycleRegistry | None = None
        self._mailbox: PointToPointMailbox | None = None
        self._task_root: Path | None = None
        self._snapshot_saved = False
        self._entry = self._capture_scope()

    # ── 权限与范围 ────────────────────────────────────────────────────────
    def _capture_scope(self) -> dict[str, object]:
        """执行前 ①–⑦ 项核对并返回基线；任一项不成立就拒绝开始。"""
        self._sessions.check_access(self._context)
        snapshot = self._sessions.authoritative_snapshot(self._context)
        task = snapshot.task
        if task is None or task.status != "running":
            raise PermissionError("delegate_requires_running_task")
        self._sessions.check_budget_binding(self._context, self._parent_budget)
        work = self._sessions.work_state(self._context)
        return {
            "task_id": task.task_id,
            "active_budget_id": snapshot.active_budget_id,
            "goal_version": work.confirmed_goal_id,
            "mode": work.mode,
            "grant_status": work.grant.status if work.grant is not None else None,
            "grant_proposal_id": (work.grant.proposal_id
                                  if work.grant is not None else None),
        }

    def _verify_scope(self) -> None:
        """复核基线；任务/预算/目标/授权范围变了就抛，不继续用旧权限。"""
        current = self._capture_scope()
        if current != self._entry:
            raise AuthorizationRequired("delegate_scope_changed")

    def _stop_requested(self) -> bool:
        """安全点：只**读**会话控制的 stop 标志，不消费 steers、不写控制状态。"""
        if self._control is not None:
            try:
                state = self._control.snapshot()
                if bool(state.get("stop")):
                    self._abort_reason = "user_stopped"
                    return True
            except Exception:
                # 读不到控制状态就按停止处理（fail-closed），不继续消耗预算。
                self._abort_reason = "control_unavailable"
                return True
        try:
            self._verify_scope()
        except (AuthorizationRequired, PermissionError):
            self._abort_reason = "scope_changed"
            return True
        return False

    # ── 构造与运行 ────────────────────────────────────────────────────────
    def _task_root_for_run(self) -> Path:
        config = self._config
        if config.task_directory is None:
            self._temp = tempfile.TemporaryDirectory(prefix="ma-service-")
            return Path(self._temp.name)
        derived = (Path(config.task_directory)
                   / str(self._context.owner_id)
                   / str(self._context.session_id)
                   / self._run_id)
        if derived.exists():
            raise RuntimeError("task_directory 派生目录已存在；拒绝覆盖历史现场")
        derived.mkdir(parents=True)
        return derived

    def _build_transport(self, profile_key: str) -> ChatTransport:
        # Manager 的 transport_factory 契约传入的是档位 key（字符串），不是 ModelProfile。
        transport = self._config.worker_transport_factory(profile_key)
        self._owned_transports.append(transport)
        return transport

    def _build_manager(self, root: Path) -> Manager:
        config = self._config
        self._resources = ResourceLedger(config.resource_limits)
        progress_root = config.progress_directory or root
        self._lifecycle = LifecycleRegistry(progress_root)
        self._task_root = root / "tasks"

        def mailbox_factory(participants: tuple[str, ...]) -> PointToPointMailbox:
            self._mailbox = PointToPointMailbox(participants=participants)
            return self._mailbox

        def executor_factory(spec: WorkerSpec) -> ToolExecutor | None:
            declared = tuple(name for name in spec.allowed_tools if name != "none")
            if not declared:
                return None
            return ScopedToolExecutor(
                self._registry, self._sessions, self._context, declared,
                check_names=config.tool_check_names,
            )

        transports = {config.manager_profile.key: config.manager_transport}
        return Manager(
            manager_profile=config.manager_profile,
            worker_profiles=config.worker_profiles,
            transports=transports,
            worker_specs=config.worker_specs,
            root_budget=self._parent_budget.child(BudgetLimits(
                model_calls=config.delegate_model_calls,
                tool_calls=config.delegate_tool_calls,
            )),
            max_workers=config.max_workers,
            max_subtasks=config.max_subtasks,
            resources=self._resources,
            hooks=None,
            price_table=config.price_table,
            transport_factory=self._build_transport,
            executor_factory=executor_factory,
            mailbox_factory=mailbox_factory,
            task_directory=self._task_root,
            stop_poller=self._stop_requested,
            max_total_tool_calls=config.delegate_tool_calls,
            lifecycle=self._lifecycle,
        )

    def run(self, request: DelegateRequest) -> ManagerRun:
        if self._closed:
            raise RuntimeError("service_closed")
        if not isinstance(request, DelegateRequest):
            raise TypeError("request 必须是 DelegateRequest")
        self._verify_scope()
        root = self._task_root_for_run()
        manager = self._build_manager(root)
        # ``AuthorizationRequired`` 只在「还没有任何内部工具业务动作」时才会冒到这里
        # （worker 内部已把「工具已返回后才失去授权」降级为 blocked），因此直接上抛，
        # 交给现有 Runtime 走标准等待用户路径——这里**不做**自动重放。
        run = manager.run(request.objective)
        total_tool_calls = sum(row.tool_calls for row in run.results)
        if self._abort_reason == "scope_changed" and total_tool_calls == 0:
            # 一条工具都没跑过：把「权限已过期」如实上抛，而不是伪造 partial。
            raise AuthorizationRequired("delegate_scope_changed")
        self._save_snapshot(run)
        return run

    def _save_snapshot(self, run: ManagerRun) -> None:
        """脱敏结果快照存会话扩展：只保存现场，**不承诺恢复**。"""
        payload: dict[str, object] = {
            "run_id": self._run_id,
            "status": run.status,
            "error": run.error,
            "cancelled": run.cancelled,
            "manager_model": run.manager_model,
            "worker_models": list(run.worker_models),
            "model_calls": run.model_calls,
            "usage_source": run.usage_source,
            "aggregate_length": len(run.aggregate),
            "plan": ([{"subtask_id": item.subtask_id, "role": item.role,
                       "worker_profile": item.worker_profile,
                       "depends_on": list(item.depends_on)}
                      for item in run.plan.subtasks] if run.plan is not None else []),
            "results": [{
                "subtask_id": row.subtask_id, "role": row.role,
                "profile": row.profile, "tier": row.tier,
                "status": row.status, "verification": row.verification,
                "verification_reason": row.verification_reason,
                "model_calls": row.model_calls, "tool_calls": row.tool_calls,
                "elapsed_seconds": row.elapsed_seconds,
                "usage_source": row.usage_source,
                "error": row.error,
            } for row in run.results],
            "task_graph": list(run.task_graph),
            "lifecycle": (list(self._lifecycle.snapshot())
                          if self._lifecycle is not None else []),
            "mailbox": self._mailbox.snapshot() if self._mailbox is not None else None,
            "resources": (self._resources.snapshot()
                          if self._resources is not None else None),
        }
        # 快照只存**现场**：统一转成 JSON 安全字形（tuple/Path 归一化），并去掉
        # provider_state、key、原始回执正文、Mailbox 全文与临时目录绝对路径。
        safe = json.loads(json.dumps(payload, ensure_ascii=False, default=str))
        try:
            self._sessions.set_extension(self._context, "multi_agent_last_run", safe)
        except Exception:
            # 快照是观测，不是业务结果：写失败不改变已完成的结论，也不假装写成。
            self._snapshot_saved = False
        else:
            self._snapshot_saved = True

    def close(self) -> None:
        """释放本实例拥有的 transport 与临时目录；可重复调用。"""
        if self._closed:
            return
        self._closed = True
        for transport in self._owned_transports:
            closer = getattr(transport, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    pass
        self._owned_transports.clear()
        if self._temp is not None:
            try:
                self._temp.cleanup()
            finally:
                self._temp = None
