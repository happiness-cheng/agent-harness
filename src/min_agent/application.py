"""本地应用的可信编排入口；会话单写入者、模型/工具与后台提醒解耦。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from threading import Event, RLock, Thread
from uuid import UUID
import json
import re

from pydantic import BaseModel, ConfigDict, Field

from min_agent.session.artifacts import ArtifactStore
from min_agent.ledger.budget import BudgetLimits
from min_agent.ledger.budget_persistence import SessionBudgets
from min_agent.ledger.checkpoints import Checkpoint, ToolResultRef
from min_agent.ledger.checkpoints import CheckpointPolicy
from min_agent.compression.compaction import Compactor
from min_agent.compression.selector import GroundedSelector
from min_agent.context.manager import ContextManager, ManagedModel
from min_agent.session.events import SessionEvents
from min_agent.memory.journal import MemoryJournal
from min_agent.memory.records import SourceReference
from min_agent.memory.state import SessionStateTable
from min_agent.contracts.models import Message, ModelResponse
from min_agent.notify.notifications import Notifications
from min_agent.runtime.progress_guard import ProgressGuard
from min_agent.research.service import ResearchService
from min_agent.runtime import Runtime
from min_agent.notify.scheduler import Scheduler
from min_agent.session.store import SessionStore, ExecutionContext
from min_agent.compression.skills import SkillCatalog
from min_agent.compression.skill_retention import SkillRetentionSelector
from min_agent.session.paths import default_env_file, ensure_workspace_outside_private, owner_id_for_database
from min_agent.session.storage import SQLiteStorage
from min_agent.session.task_state import ActionSpec, AuthorizationRequired, GoalSpec, TaskController
from min_agent.tools.calculator import CALCULATOR_TOOL
from min_agent.tools.read_webpage import PublicFetcher, WebReader
from min_agent.tools.reminder import ReminderService
from min_agent.tools.reminder_tools import register_reminders
from min_agent.tools.registry import ToolDefinition, ToolRegistry
from min_agent.tools.search import SearchService
from min_agent.tools.todo import TodoService
from min_agent.tools.todo_tools import todo_registry
from min_agent.contracts.trace import TraceContext, TraceRecorder, record_run_trace


class GoalParameters(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    objective: str = Field(min_length=1, max_length=1000)
    constraints: list[str] = Field(default_factory=list, max_length=20)
    completion_criteria: list[str] = Field(min_length=1, max_length=20)
    actions: list[ActionSpec] = Field(default_factory=list, max_length=50)


class _Reference(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    artifact_id: str
    start: int = Field(default=0, ge=0)
    limit: int = Field(default=4000, ge=1, le=8000)


class _Question(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    question: str = Field(min_length=1, max_length=2000)


class _MemorySource(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    reference: SourceReference
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=4000, ge=1, le=8000)


class _CurrentState(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class _Skill(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    name: str = Field(min_length=1, max_length=60)
    path: str | None = Field(default=None, min_length=1, max_length=200)


class _DelegateTask(BaseModel):
    """``delegate_task`` 的模型可见参数：**只有**目标字符串。

    没有 owner / session / budget / 角色 / 工具字段——这些只来自可信闭包，
    模型无法通过参数扩大权限或换身份（MA-P5-013 D14）。
    """

    model_config = ConfigDict(strict=True, extra="forbid")
    objective: str = Field(min_length=1, max_length=2000)


class DemoModel:
    def generate(self, messages, *, tools=None):
        return ModelResponse(finish_reason="stop", content="离线演示已保存这条消息。当前没有接入真实模型；启用指定凭据后可由模型选择工具。")


class HarnessApplication:
    def __init__(self, database, *, model=None, model_factory=None, proxy=None, desktop=None,
                 total_models=40, segment_models=6, total_tools=60, segment_tools=12, window=None, owner_id=None,
                 memory_strategy='GOAL_VALUE', memory_budget=4096, memory_policy=None, memory_mode='legacy',
                 public_goals_enabled=False, research_observation_enabled=False,
                 enable_multi_agent=False, multi_agent_service_factory=None, model_catalog=None,
                 workspace_root=None, mcp_servers=None, generic_tools=False,
                 gate_policy="off", gate_model=None):
        if memory_strategy not in {'NONE','RECENCY','FREQUENCY','GOAL_VALUE'}:
            raise ValueError('无效Memory策略')
        if type(memory_budget) is not int or memory_budget<0: raise ValueError('无效Memory预算')
        self.memory_strategy,self.memory_budget,self.memory_policy=memory_strategy,memory_budget,memory_policy
        if memory_mode not in {'legacy','active','off'}: raise ValueError('无效Memory使用模式')
        self.memory_mode = memory_mode
        # fail-closed：工作区不得包含（或位于）凭据/数据目录；实际数据库路径
        # 与默认 env 文件也一起比较，避免 --database 把库放进工作区。
        path = Path(database).resolve()
        if workspace_root is not None:
            ensure_workspace_outside_private(
                workspace_root, database=path, env_file=default_env_file())
        path.parent.mkdir(parents=True, exist_ok=True)
        self.storage = SQLiteStorage.open(path) if path.exists() else SQLiteStorage.create_new(path)
        self.sessions = SessionStore(storage=self.storage)
        if type(research_observation_enabled) is not bool:
            raise TypeError("研究观察开关必须为布尔值")
        self.research_observation_enabled = research_observation_enabled
        self.research_observations = None
        if research_observation_enabled:
            from min_agent.research.observation import ResearchObservationStore
            self.research_observations = ResearchObservationStore(self.sessions)
        if type(public_goals_enabled) is not bool:
            raise TypeError("公开目标开关必须为布尔值")
        self.public_goals_enabled = public_goals_enabled
        self.public_goal_registry = None
        self.public_goal_adapter = None
        if public_goals_enabled:
            from min_agent.goals.public_goal_adapter import PublicGoalAdapter
            from min_agent.goals.public_goal_registry import PublicGoalRegistry
            from min_agent.goals.todo_observation import TodoObserver
            self.public_goal_registry = PublicGoalRegistry(self.sessions)
            self.public_goal_adapter = PublicGoalAdapter(
                self.public_goal_registry, TodoObserver(self.storage))
        # MA-P5-013：默认关闭。关闭时不创建 Service / Manager / Mailbox /
        # ResourceLedger，也不注册 delegate_task——只是记下两个字段。
        if type(enable_multi_agent) is not bool:
            raise TypeError("多 Agent 开关必须为布尔值")
        if multi_agent_service_factory is not None and not callable(multi_agent_service_factory):
            raise TypeError("multi_agent_service_factory 必须可调用")
        if enable_multi_agent and multi_agent_service_factory is None:
            # 打开却缺工厂：立即失败，不回退 Demo、不借用主模型冒充强/弱模型。
            raise ValueError("enable_multi_agent=True 必须同时提供可信的 multi_agent_service_factory")
        self.enable_multi_agent = enable_multi_agent
        self.multi_agent_service_factory = multi_agent_service_factory
        from min_agent.long_memory import LongMemory
        self.memory = LongMemory(self.sessions)
        self.owner_id = owner_id or owner_id_for_database(path)
        self.model = model or DemoModel(); self.model_factory = model_factory
        self.demo = isinstance(self.model, DemoModel) and model_factory is None
        # QWEN-SELECTOR-001：可选模型目录。提供时按会话取模型，缺省用目录默认模型；
        # 未提供时行为与历史版本完全一致（进程级单模型）。
        self.model_catalog = model_catalog
        # FS-READ-001：只读文件工具**显式开启**（传 workspace_root）；默认不注册，
        # 避免改变既有部署的提示词体积与小上下文模型的行为。
        self.workspace_root = Path(workspace_root).resolve() if workspace_root is not None else None
        # GENERIC-TOOLS-001：pi 式四工具模式。只注册 read/write/edit/bash，
        # 不经过 todo/memory/MCP 的专用注册链；需要显式工作区，默认关闭。
        if type(generic_tools) is not bool:
            raise TypeError("generic_tools 必须是布尔值")
        if generic_tools and self.workspace_root is None:
            raise ValueError("generic_tools=True 必须提供 workspace_root")
        self.generic_tools = generic_tools
        # GOV-001-S3：执行前分级门。默认 off（专用模式与既有测试不受影响）；
        # generic 产品入口（web CLI）显式设 interactive。gate_model 是中风险
        # 审批用小模型（OllamaModel 形），缺省时中风险升级人工（fail-closed）。
        if gate_policy not in {"off", "interactive", "unattended"}:
            raise ValueError("gate_policy 只能是 off/interactive/unattended")
        if gate_policy != "off" and not generic_tools:
            raise ValueError("执行前分级门目前只支持 generic 四工具模式")
        self.gate_policy = gate_policy
        self.gate_model = gate_model
        # MCP-001：服务器配置只来自可信 host；默认不配置、不启动任何子进程。
        if mcp_servers is None:
            servers = ()
        else:
            from min_agent.mcp.spec import MCPServerSpec
            servers = tuple(mcp_servers)
            if not all(isinstance(server, MCPServerSpec) for server in servers):
                raise TypeError("mcp_servers 必须是 MCPServerSpec 序列")
            if len({server.name for server in servers}) != len(servers):
                raise ValueError("MCP 服务器名重复")
        self.mcp_servers = servers
        if model_catalog is not None:
            if not callable(getattr(model_catalog, "model_for", None)):
                raise TypeError("model_catalog 必须提供 model_for(model)")
            if self.model_factory is None:
                self.model_factory = lambda context: model_catalog.model_for(
                    self.sessions.extension(context, "model_choice", model_catalog.default_model))
        self.proxy, self.total_models, self.segment_models, self.window = proxy, total_models, segment_models, window
        BudgetLimits(model_calls=total_models,tool_calls=total_tools)
        BudgetLimits(model_calls=segment_models,tool_calls=segment_tools)
        self.total_tools,self.segment_tools=total_tools,segment_tools
        # WIRING-003：多 Agent 委派复用任务总预算（默认 40/60）。片段原先固定
        # 6/12，计划 + 3 个 worker + 汇总会在中途耗尽，delegate_task 只能空转。
        # 仍受任务总账本硬上限约束，不额外开预算池。
        if self.enable_multi_agent:
            self.segment_models, self.segment_tools = total_models, total_tools
        self._lock, self._running, self._errors, self._fetchers = RLock(), {}, {}, {}
        self._mcp_catalogs = {}
        # QWEN-STREAM-001：会话级流式显示缓冲；只放增量正文，不放请求或凭据。
        self._streams = {}
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent-session")
        self._closing = Event(); self._scheduler_thread = None
        self.scheduler = Scheduler(self.sessions, desktop=desktop)
        self._recover_interrupted()

    def context(self, session_id):
        context = ExecutionContext(owner_id=self.owner_id, session_id=UUID(str(session_id)))
        return self.sessions.check_access(context)

    def _mcp_catalog(self, context):
        """每个会话一个 MCP 目录；连接状态跨任务保留，应用关闭时统一回收。"""
        if not self.mcp_servers:
            return None
        identity = str(context.session_id)
        with self._lock:
            catalog = self._mcp_catalogs.get(identity)
            if catalog is None:
                from min_agent.mcp.catalog import MCPServerCatalog
                catalog = MCPServerCatalog(self.mcp_servers)
                # 同一会话首次访问（新建或重启后加载）就后台连接，不阻塞调用方。
                catalog.connect_background()
                self._mcp_catalogs[identity] = catalog
            return catalog

    def _mcp_status(self, context):
        if not self.mcp_servers:
            return {}
        # 快照/打开已有会话时走 _mcp_catalog，触发重启后的后台重连。
        catalog = self._mcp_catalog(context)
        return catalog.status() if catalog is not None else {}

    def _mcp_pending(self, context):
        if not self.mcp_servers:
            return []
        from min_agent.mcp.confirm import MCPConfirmations
        return MCPConfirmations(self.sessions, context).pending()

    def mcp_confirm(self, session_id, pending_id, decision, *, continue_task=True):
        """web 定向入口：批准/拒绝一次挂起的 MCP 调用，可随决定恢复任务。"""
        if decision not in {"approve", "refuse"}:
            raise ValueError("decision 只能是 approve 或 refuse")
        context = self.context(session_id)
        with self._lock:
            if str(context.session_id) in self._running:
                raise RuntimeError("运行中不能处理 MCP 确认")
            from min_agent.mcp.confirm import MCPConfirmations
            record = MCPConfirmations(self.sessions, context).decide(
                UUID(str(pending_id)), decision == "approve")
            if continue_task:
                saved = self.sessions.snapshot(context)
                if saved.task is not None and saved.task.status not in {"completed", "running"}:
                    verb = "批准" if decision == "approve" else "拒绝"
                    self.resume(session_id, task_id=str(saved.task.task_id),
                                text=f"用户已{verb} MCP 调用 {record['prefixed']}；"
                                     "按该决定继续，保留先前结果；被拒绝的调用不要重试。")
            return record

    def _gate_pending(self, context):
        if not self.generic_tools or self.gate_policy == "off":
            return []
        from min_agent.tools.gate import GateConfirmations
        return GateConfirmations(self.sessions, context).pending()

    def gate_confirm(self, session_id, pending_id, decision, *, continue_task=True):
        """web 定向入口：批准/拒绝一次挂起的高危调用，可随决定恢复任务。"""
        if decision not in {"approve", "refuse"}:
            raise ValueError("decision 只能是 approve 或 refuse")
        context = self.context(session_id)
        with self._lock:
            if str(context.session_id) in self._running:
                raise RuntimeError("运行中不能处理门禁确认")
            from min_agent.tools.gate import GateConfirmations
            record = GateConfirmations(self.sessions, context).decide(
                UUID(str(pending_id)), decision == "approve")
            if continue_task:
                saved = self.sessions.snapshot(context)
                if saved.task is not None and saved.task.status not in {"completed", "running"}:
                    verb = "批准" if decision == "approve" else "拒绝"
                    self.resume(session_id, task_id=str(saved.task.task_id),
                                text=f"用户已{verb}高危调用 {record['tool']}；"
                                     "按该决定继续，保留先前结果；被拒绝的调用不要重试。")
            return record

    def create_session(self, label="新会话", model=None, mode=None):
        """新建会话；model/mode 可在创建时指定，避免“第一条消息发出前无法选择”。"""
        if not isinstance(label, str) or not label.strip() or len(label) > 60: raise ValueError("会话名需要1到60字符")
        if mode is not None and mode not in {"plan", "build"}: raise ValueError("模式只能是 plan 或 build")
        catalog = self.model_catalog
        if model is not None:
            if catalog is None: raise RuntimeError("当前服务未启用可选模型")
            catalog.validate(model)
        context = self.sessions.create(self.owner_id)
        self.sessions.set_extension(context, "label", label)
        if model is not None:
            self.sessions.set_extension(context, "model_choice", model)
        if mode is not None:
            TaskController(self.sessions).select_mode(context, mode)
        if self.mcp_servers and not self.generic_tools:
            self._mcp_catalog(context).connect_background()
        return {"session_id": str(context.session_id), "label": label,
                "mode": mode or "plan",
                "model": model if model is not None else (catalog.default_model if catalog is not None else None)}

    def set_label(self, session_id, label):
        """重命名会话；label 存在 extension，不需要数据库迁移。"""
        if not isinstance(label, str) or not label.strip() or len(label.strip()) > 60:
            raise ValueError("会话名需要1到60字符")
        context = self.context(session_id)
        cleaned = label.strip()
        self.sessions.set_extension(context, "label", cleaned)
        return {"session_id": str(context.session_id), "label": cleaned}

    def list_sessions(self):
        result = []
        for context in self.sessions.contexts(self.owner_id):
            snapshot = self.sessions.snapshot(context)
            result.append({"session_id": str(context.session_id), "label": self.sessions.extension(context, "label", "会话"),
                "status": snapshot.task.status if snapshot.task else "ready",
                "unread": sum(event["unread"] for event in Notifications(self.sessions).list_items(context))})
        return result

    def default_model_status(self):
        """没有会话时的模型选项（新会话欢迎页用），默认值与目录一致。"""
        catalog = self.model_catalog
        if catalog is None:
            return {"selectable": False, "current": None, "options": []}
        return {"selectable": True, "current": catalog.default_model, "options": catalog.options()}

    def model_status(self, context):
        """当前会话的模型选择；未启用目录时明确返回不可选，不伪装成有选项。"""
        catalog = self.model_catalog
        if catalog is None:
            return {"selectable": False, "current": None, "options": []}
        choice = self.sessions.extension(context, "model_choice", catalog.default_model)
        current = choice if isinstance(choice, str) and choice in catalog.models else catalog.default_model
        return {"selectable": True, "current": current, "options": catalog.options()}

    def set_model(self, session_id, model):
        """按会话保存模型选择；运行中拒绝切换，非白名单拒绝且不发任何请求。"""
        context = self.context(session_id)
        catalog = self.model_catalog
        if catalog is None:
            raise RuntimeError("当前服务未启用可选模型")
        catalog.validate(model)
        with self._lock:
            if str(context.session_id) in self._running:
                raise RuntimeError("运行中不能切换模型")
            self.sessions.set_extension(context, "model_choice", model)
        return self.model_status(context)

    def stream_sink(self, session_id):
        """SSE 端点用：当前会话的流式缓冲；没有正在进行的流时返回 None。"""
        context = self.context(session_id)
        with self._lock:
            return self._streams.get(str(context.session_id))

    def _open_stream(self, identity):
        from min_agent.runtime.streaming import StreamSink
        sink = StreamSink()
        with self._lock:
            self._streams[identity] = sink
        return sink

    def _close_stream(self, identity):
        with self._lock:
            sink = self._streams.pop(identity, None)
        if sink is not None:
            sink.close()

    def snapshot(self, session_id):
        context = self.context(session_id); value = self.sessions.snapshot(context)
        budget = value.budgets.get(value.active_budget_id)
        return {"session_id": str(context.session_id), "label": self.sessions.extension(context, "label", "会话"),
            "messages": [message.model_dump(mode="json", exclude={"provider_state"}) for message in value.messages],
            "task": value.task.model_dump(mode="json") if value.task else None,
            "work_state": value.work_state.model_dump(mode="json"),
            "checkpoint": value.checkpoint.model_dump(mode="json") if value.checkpoint else None,
            "budget": {"id": str(budget.budget_id), "limits": budget.state.limits.model_dump(),
                       "usage": budget.state.usage.model_dump()} if budget else None,
            "events": SessionEvents(self.sessions, context).snapshot(),
            "notifications": Notifications(self.sessions).list_items(context),
            "todos": [view.model_dump(mode="json") for view in TodoService(self.sessions).list_views(context)],
            "reminders": ReminderService(self.sessions).list_items(context),
            "trace": self.sessions.extension(context, "last_trace", []),
            "trace_runs": [{key: value for key, value in record.items() if key != "events"}
                for record in self.sessions.extension(context, "trace_runs", [])],
            "allow_web": self.sessions.extension(context, "allow_public_web", False),
            "model": self.model_status(context),
            "pending_input": self.sessions.extension(context, "pending_input", None),
            "error": self._errors.get(str(context.session_id)), "demo": self.demo,
            "long_memory": self.memory_status(context),
            "mcp": self._mcp_status(context),
            "mcp_confirmations": self._mcp_pending(context),
            "gate_confirmations": self._gate_pending(context),
            "gate_audit": self.sessions.extension(context, "gate_audit", []),
            "multi_agent": self.sessions.extension(context, "multi_agent_last_run", None)}

    def submit(self, session_id, text, *, public_goals=None, proposal_id=None):
        context = self.context(session_id)
        if public_goals is not None:
            if proposal_id is None:
                proposal_id = self.sessions.work_state(context).confirmed_goal_id
            if proposal_id is None:
                raise PermissionError("公开目标需要当前已确认提案")
            self.register_public_goals(session_id, proposal_id, public_goals)
        with self._lock:
            SessionEvents(self.sessions, context).enqueue(text)
            self._launch(context)

    def _launch(self, context, *, text=None, checkpoint_id=None, resume_task_id=None):
        identity = str(context.session_id)
        if identity in self._running: return False
        if self._closing.is_set(): raise RuntimeError("应用正在关闭")
        self._running[identity] = self._pool.submit(
            self._worker, context, text, checkpoint_id, resume_task_id)
        return True

    def wait(self, session_id, timeout=20):
        with self._lock: future = self._running.get(str(session_id))
        if future: future.result(timeout=timeout)

    def resume(self, session_id, *, checkpoint_id=None, task_id=None, text="继续已确认任务"):
        if checkpoint_id is not None and task_id is not None:
            raise ValueError("checkpoint_id 与 task_id 不能同时指定")
        context = self.context(session_id)
        with self._lock:
            if str(context.session_id) in self._running: raise RuntimeError("会话正在执行")
            snapshot = self.sessions.snapshot(context)
            pending = snapshot.checkpoint is not None and snapshot.checkpoint.resumed_by is None
            if pending:
                if checkpoint_id is None or str(snapshot.checkpoint.checkpoint.checkpoint_id) != str(checkpoint_id):
                    raise PermissionError("恢复必须指定当前检查点")
                target_checkpoint = UUID(str(checkpoint_id))
                target_task = None
            else:
                if task_id is None or snapshot.task is None:
                    raise PermissionError("恢复必须定向指定暂停/等待任务")
                if (str(snapshot.task.task_id) != str(task_id)
                        or snapshot.task.status in {"completed", "running"}):
                    raise PermissionError("恢复必须定向指定暂停/等待任务")
                target_checkpoint = None
                target_task = UUID(str(task_id))
            SessionEvents(self.sessions, context).resume_dispatch()
            self._launch(context, text=text, checkpoint_id=target_checkpoint,
                         resume_task_id=target_task)

    def propose(self, context, parameters):
        validated = GoalParameters.model_validate(parameters)
        actions = []
        todos = TodoService(self.sessions)
        for action in validated.actions:
            args = dict(action.parameters)
            if "patch" in args:
                args.update(args.pop("patch"))
            if action.operation == "todo.add": args.setdefault("due_date", None)
            if action.operation.startswith("todo.") and args.get("due_date") is not None:
                args["due_date"] = todos.normalize_date(args["due_date"]).isoformat()
            if action.operation == "reminder.add":
                args.setdefault("todo_id", None); args.setdefault("time_zone", "Asia/Shanghai")
            if action.operation.startswith("reminder.") and args.get("scheduled_at") is not None:
                args["scheduled_at"] = ReminderService.moment(args["scheduled_at"]).isoformat()
            actions.append(ActionSpec(operation=action.operation, parameters=args))
        return TaskController(self.sessions).propose(context, GoalSpec(objective=validated.objective,
            constraints=tuple(validated.constraints), completion_criteria=tuple(validated.completion_criteria), actions=tuple(actions)))

    def confirm(self, session_id, proposal_id):
        context = self.context(session_id)
        with self._lock:
            if str(context.session_id) in self._running:
                raise RuntimeError("运行中不能确认目标，请使用显式引导")
            TaskController(self.sessions).confirm(context, UUID(str(proposal_id)))

    def register_public_goals(self, session_id, proposal_id, goals):
        """仅可信 host 调用；模型/普通请求不能登记公开目标。"""
        if not self.public_goals_enabled or self.public_goal_registry is None:
            raise RuntimeError("公开目标反馈未启用")
        return self.public_goal_registry.register(self.context(session_id), proposal_id, goals)

    # 明确别名，避免 host 入口名称差异造成误用。
    configure_public_goals = register_public_goals

    def _freeze_public_goals(self, store, context, task):
        if not self.public_goals_enabled or self.public_goal_registry is None:
            return
        if self.public_goal_registry.pending(context) is not None:
            self.public_goal_registry.freeze(context, task)

    def public_feedback(self, session_id):
        """受信 host 的只读影子结果；关闭时返回 None。"""
        if not self.public_goals_enabled or self.public_goal_adapter is None:
            return None
        return self.public_goal_adapter.feedback(self.context(session_id))

    def public_score(self, session_id):
        if not self.public_goals_enabled or self.public_goal_adapter is None:
            return None
        return self.public_goal_adapter.score(self.context(session_id))

    public_goal_feedback = public_feedback
    public_goal_score = public_score

    def dispatch(self, session_id, text=""):
        context = self.context(session_id)
        with self._lock:
            snapshot = self.sessions.snapshot(context)
            if snapshot.checkpoint is not None and snapshot.checkpoint.resumed_by is None:
                raise PermissionError("检查点必须定向恢复")
            if snapshot.task is not None and snapshot.task.status != "completed":
                raise PermissionError("暂停任务必须定向恢复")
            events = SessionEvents(self.sessions, context)
            events.resume_dispatch()
            if text: events.enqueue(text)
            self._launch(context)

    def set_mode(self, session_id, mode):
        return TaskController(self.sessions).select_mode(self.context(session_id), mode)

    def allow_web(self, session_id, enabled):
        if type(enabled) is not bool: raise ValueError("公开网络开关必须为布尔值")
        self.sessions.set_extension(self.context(session_id), "allow_public_web", enabled)

    def get_research_observation(self, session_id, run_ref=None):
        if not self.research_observation_enabled or self.research_observations is None:
            raise PermissionError("research_observation_disabled")
        try:
            context = self.context(session_id)
        except Exception as error:
            from min_agent.session.storage import VersionConflict
            # 陈旧会话可能已被assert_current标记失效；对host只暴露无可信结果。
            if not isinstance(error, (PermissionError, ValueError, TypeError, VersionConflict)):
                raise
            from min_agent.research.observation import ResearchObservation
            return ResearchObservation(run_ref="withheld", status="WITHHELD",
                                       reason_code="UNVERIFIED_RESEARCH_RUN")
        return self.research_observations.get(context, run_ref)

    def effective_memory_mode(self, context):
        if self.memory_strategy == 'NONE': return 'off'
        preferences = self.sessions.extension(context, 'long_memory_preferences', None)
        if preferences is None: return self.memory_mode
        return 'active' if preferences['enabled'] else 'off'

    def memory_status(self, context):
        mode = self.effective_memory_mode(context)
        preferences = self.sessions.extension(context, 'long_memory_preferences', {})
        doc = self.memory.document(context)
        return dict(enabled=mode!='off', mode=mode, scenario_ids=preferences.get('scenario_ids', []),
            scenarios=[dict(id=s.id,label=s.label) for s in doc.scenarios.values() if s.created_at<=self.memory.clock()],
            receipt=self.sessions.extension(context, 'long_memory_delivery', None),
            citation=self.sessions.extension(context, 'long_memory_citation', None),
            # WIRING-003：待审核候选只展示本人内容，不含 review_token（审核时另取）。
            candidates=[dict(id=c.id, content=c.content[:500], proposed_at=c.proposed_at.isoformat())
                        for c in self.memory.list_pending(context)])

    def configure_memory(self, session_id, enabled, scenario_ids=None):
        context = self.context(session_id)
        with self._lock:
            snapshot = self.sessions.snapshot(context)
            if str(context.session_id) in self._running or (snapshot.task and snapshot.task.status=='running'):
                raise RuntimeError('运行中不能切换Memory范围')
            if type(enabled) is not bool: raise ValueError('Memory开关必须为布尔值')
            if enabled and self.memory_strategy=='NONE': raise ValueError('此运行模式禁用长期Memory')
            current = self.sessions.extension(context, 'long_memory_preferences', {})
            ids = current.get('scenario_ids',[]) if scenario_ids is None else scenario_ids
            if not isinstance(ids,list) or len(ids)>10 or any(not isinstance(i,str) for i in ids):
                raise ValueError('场景范围无效')
            known = {s.id for s in self.memory.document(context).scenarios.values() if s.created_at<=self.memory.clock()}
            if not set(ids)<=known: raise PermissionError('场景不属于当前可用owner范围')
            self.sessions.set_extension(context,'long_memory_preferences',dict(enabled=enabled,scenario_ids=list(dict.fromkeys(ids))))
        return self.memory_status(context)

    # WIRING-003：会话/轮次结束时自动**提出候选**，不自动确认、不入库、不注入。
    # 只认用户消息开头的明确「记住/remember」指令；助手文字不能成为用户事实。
    _REMEMBER_DIRECTIVE = re.compile(
        r"^\s*(?:请)?(?:记住|remember\b)(?:\s*[:：]\s*|\s+)(\S[\s\S]*)$", re.IGNORECASE)

    def _auto_propose_memory(self, context) -> int:
        """扫描上次游标之后的用户消息，把明确「记住」指令登记为待审核候选。"""
        messages = self.sessions.snapshot(context).messages
        cursor = self.sessions.extension(context, 'long_memory_auto_cursor', 0)
        if isinstance(cursor, bool) or not isinstance(cursor, int) \
                or not 0 <= cursor <= len(messages):
            cursor = 0
        if self.effective_memory_mode(context) == 'off':
            # 关闭期间也推进游标：以后重新开启时不追溯旧消息生成候选。
            if len(messages) != cursor:
                self.sessions.set_extension(context, 'long_memory_auto_cursor', len(messages))
            return 0
        proposed = 0
        for index in range(cursor, len(messages)):
            message = messages[index]
            if message.role != 'user':
                continue
            match = self._REMEMBER_DIRECTIVE.match(message.content or '')
            if match is None:
                continue
            content = match.group(1).strip()
            if not content or not re.search(r"[^\s:：，,。.!！?？;；、]", content):
                continue
            try:
                self.memory.propose(context, content, source_indices=[index])
            except (ValueError, PermissionError):
                # 已拒绝/已遗忘/来源不可用：保持既有语义，不自动绕过审核。
                continue
            proposed += 1
        if len(messages) != cursor:
            self.sessions.set_extension(context, 'long_memory_auto_cursor', len(messages))
        return proposed

    def _registry(self, context, segment, raw_model, control, trace=None, memory_query='当前任务',
                  mcp_catalog=None):
        if self.generic_tools:
            # 只暴露四个通用入口；不注册 todo/记忆/提醒/研究/多 Agent/MCP。
            # 授权门也随专用工具一起不挂载——这是 generic 模式的显式取舍，
            # 由启动参数和系统提示词共同披露，不能悄悄套用到默认模式。
            from min_agent.tools.generic import GenericWorkspace, generic_tools
            registry = ToolRegistry(sessions=self.sessions, context=context)
            workspace = GenericWorkspace(self.workspace_root)
            for tool in generic_tools(self.workspace_root, workspace=workspace):
                registry.register(tool)
            # 分级门需要 workspace 引用来放行已确认的高危破坏性命令。
            registry._generic_workspace = workspace
            return registry
        controller = TaskController(self.sessions)
        todos = TodoService(self.sessions)
        registry = todo_registry(todos, controller, context)
        registry.register(CALCULATOR_TOOL)
        # FS-READ-001：只读文件工具；根目录不可用时明确不注册，而不是放宽到全盘。
        if self.workspace_root is not None and self.workspace_root.is_dir():
            from min_agent.tools.filesystem import build_write_gate, filesystem_tools
            # 复用上面已构造的 controller，避免把 TaskController 变成函数局部名。
            gate = build_write_gate(controller, context)
            for tool in filesystem_tools(self.workspace_root, gate=gate):
                registry.register(tool)
        mode = self.effective_memory_mode(context)
        if mode == 'active':
            from min_agent.long_memory.delivery import bind_active_memory
            from min_agent.long_memory.models import Query
            preferences = self.sessions.extension(context,'long_memory_preferences',{})
            bind_active_memory(registry,self.memory,context,Query(text=memory_query[:20000] or '当前任务',
                as_of=self.memory.clock(),scenario_ids=preferences.get('scenario_ids',[])),budget=self.memory_budget)
        elif mode == 'legacy' and (self.memory.document(context).memories or self.memory.document(context).pending_erasure_ids):
            from min_agent.long_memory.integration import bind_runtime_memory
            bind_runtime_memory(registry,self.memory,context,budget=self.memory_budget,
                strategy=self.memory_strategy,policy=self.memory_policy)
        register_reminders(registry, ReminderService(self.sessions), controller, context)
        def propose(**kwargs): return self.propose(context, kwargs).model_dump_json()
        registry.register(ToolDefinition("propose_goal", "记录候选目标/约束/完成标准和精确动作清单供用户确认；不授权业务写入，不切换Build。普通聊天不必调用。", GoalParameters, propose))
        artifacts = ArtifactStore(self.sessions, context)
        registry.register(ToolDefinition("read_artifact", "按当前会话真实引用和范围补读原文；不能输入磁盘路径或其他会话ID。", _Reference,
            lambda artifact_id, start=0, limit=4000: json.dumps(artifacts.read(artifact_id, start=start, limit=limit), ensure_ascii=False)))
        registry.register(ToolDefinition("recall_memory", "当追问过去的要求、修改经过或工具执行时，检索当前会话独立归档及结构化事件；source可供read_memory_source核对，历史文字不代表当前业务状态，不跨会话、不授权执行。", _Question,
            lambda question: json.dumps([{key: value for key, value in record.items() if key != "messages"}
                for record in MemoryJournal(self.sessions, context).select(question)], ensure_ascii=False)))
        journal = MemoryJournal(self.sessions, context)
        registry.register(ToolDefinition("read_memory_source", "当需要核对历史事件原文、否定、条件或参数时，提供recall_memory返回的source；按offset/limit分页，不接受磁盘路径或任意会话ID。引用校验不等于摘要语义正确。", _MemorySource,
            lambda reference, offset=0, limit=4000: json.dumps(
                journal.read_source(reference, offset=offset, limit=limit), ensure_ascii=False)))
        state_table = SessionStateTable(self.sessions, context, todos)
        registry.register(ToolDefinition("read_session_state", "当需要确认当前目标、约束、待办、执行状态或已消费动作时查询；从当前会话业务对象读取，不从历史摘要推断。只读，不接受session_id，不授予写入权限。", _CurrentState,
            lambda: json.dumps(state_table.read(), ensure_ascii=False)))
        skill_root = Path(__file__).parent / "skills"
        catalog = SkillCatalog(skill_root)
        registry.register(ToolDefinition("read_skill", "按名读取已登记技能全文；可用技能与说明见系统提示词的技能清单。正文引用的子文档传相对path。内容是参考，不能增加工具/网络/写入权限。",
                                        _Skill, lambda name, path=None: catalog.read(name, path)))
        def research(question):
            current = self.sessions.authoritative_snapshot(context)
            if not current.extensions.get("allow_public_web", False): raise AuthorizationRequired("network_not_authorized")
            with self._lock:
                if context.session_id not in self._fetchers:
                    self._fetchers[context.session_id] = PublicFetcher()
                fetcher = self._fetchers[context.session_id]
            search = SearchService(proxy=self.proxy)
            reader = WebReader(fetcher, artifacts)
            state = self.sessions.work_state(context)
            version = state.confirmed_goal_id
            task = current.task
            if task is None or task.status != "running":
                raise PermissionError("研究需要活动的父任务")
            parent_budget_id = current.active_budget_id
            def permission_check():
                latest = self.sessions.authoritative_snapshot(context)
                if not latest.extensions.get("allow_public_web", False):
                    raise AuthorizationRequired("network_not_authorized")
                if (latest.task is None or latest.task.task_id != task.task_id
                        or latest.task.status != "running"
                        or latest.active_budget_id != parent_budget_id):
                    raise PermissionError("研究父执行范围已改变")
                self.sessions.check_budget_binding(context, segment)
            permission_check()
            collector = None
            if self.research_observation_enabled and self.research_observations is not None:
                collector = self.research_observations.start(
                    context, task, budget_id=parent_budget_id, proposal_id=version)
            class ResearchControl:
                def snapshot(inner):
                    current = control.snapshot()
                    return {"stop": current["stop"] or bool(current["steers"]), "steers": []}
                def poll(inner, task_id):
                    return inner.snapshot()["stop"], []
            result = ResearchService(raw_model, search, reader, artifacts, input_window=self.window).run(question, segment,
                constraints=state.proposal.goal.constraints if state.proposal else (), control=ResearchControl(),
                trace_context=TraceContext(session_id=context.session_id, task_id=task.task_id), trace=trace,
                permission_check=permission_check, observation=collector)
            if self.sessions.work_state(context).confirmed_goal_id != version:
                if collector is not None:
                    collector.invalidate("stale_goal_version")
                result = {"status": "partial", "error": "stale_goal_version", "evidence": None}
            return json.dumps(result, ensure_ascii=False)
        registry.register(ToolDefinition("research", "针对已允许的公开问题自主搜索、阅读与补查，返回有原文位置/限制/冲突的证据包；不复制私人聊天、不写业务或递归委派。", _Question, research))
        # MA-P5-013：**条件注册**。默认 enable_multi_agent=False 时这一段完全不执行，
        # 不创建任何多 Agent 对象、不注册工具、不产生额外线程。
        if self.enable_multi_agent and self.multi_agent_service_factory is not None:
            def delegate_task(objective):
                """同步跑一次多 Agent 委派；每次都新建 Service，finally 释放。"""
                from min_agent.agents.multi_agent_service import DelegateRequest
                request = DelegateRequest(objective=objective)
                # 可信入口全部来自闭包：context / segment / registry / control。
                # 外层 Runtime 已把本次 delegate_task 计为一次工具调用，这里只消耗子预算。
                service = self.multi_agent_service_factory(
                    sessions=self.sessions, context=context,
                    parent_budget=segment, registry=registry, control=control,
                    model=raw_model)
                try:
                    run = service.run(request)
                    return json.dumps(run.model_dump(mode="json"), ensure_ascii=False)
                finally:
                    service.close()
            registry.register(ToolDefinition(
                "delegate_task",
                "把一个有界目标拆给 Manager/Worker 完成，返回结构化计划与各子任务结论。"
                "不扩大当前会话权限、不递归委派、不替代用户确认；结果里的 completed 仍"
                "需符合各 worker 的 verification，可能返回 partial / failed / cancelled。",
                _DelegateTask, delegate_task))
        if mcp_catalog is not None:
            from min_agent.mcp.confirm import MCPConfirmations
            from min_agent.mcp.tools import register_mcp_tools
            register_mcp_tools(registry, mcp_catalog, MCPConfirmations(self.sessions, context))
        return registry

    def _base_prompt(self) -> str:
        """按模式选系统提示；generic 模式额外注入真实工作区根。"""
        name = "generic.md" if self.generic_tools else "system.md"
        base = (Path(__file__).parent / "prompts" / name).read_text(encoding="utf-8")
        if self.generic_tools:
            base = base.rstrip() + f"\n\n当前工作区根目录：{self.workspace_root}\n"
        return base

    def _worker(self, context, text=None, checkpoint_id=None, resume_task_id=None):
        identity = str(context.session_id)
        sink = self._open_stream(identity)
        try:
            while not self._closing.is_set():
                self.memory.reconcile_deletions(context)
                events = SessionEvents(self.sessions, context)
                new_input = text is None
                if new_input: text = events.next_input()
                if text is None: break
                # QWEN-UX-001：把"正在处理的那条输入"记在会话扩展里，运行中页面能立刻显示，
                # 进程被强杀时也能在下次启动恢复成历史；不写 messages，避免影响
                # Runtime 的 original_length 切片与压缩计数。
                self.sessions.set_extension(context, "pending_input", text)
                snapshot = self.sessions.snapshot(context)
                budgets = SessionBudgets(self.sessions)
                if new_input and snapshot.task is not None and snapshot.task.status != "completed":
                    raise RuntimeError("暂停任务必须定向续跑，普通输入不能创建新账本")
                # 普通新输入开启新逻辑任务；定向续跑仅新建片段，复用原任务账本。
                budget_id = snapshot.active_budget_id
                if new_input or budget_id is None:
                    budget_id = budgets.create(context, BudgetLimits(model_calls=self.total_models, tool_calls=self.total_tools))
                ledger = budgets.open(context, budget_id)
                segment = ledger.start_segment(BudgetLimits(model_calls=self.segment_models, tool_calls=self.segment_tools))
                raw_model = self.model_factory(context) if self.model_factory is not None else self.model
                from min_agent.context.model_context import context_window, output_reserve
                window = context_window(raw_model, override=self.window)
                compactor = Compactor(raw_model, self.sessions, context, segment, input_window=window, control=events)
                manager = ContextManager(self.sessions, context, ArtifactStore(self.sessions, context),
                    window=window, output_reserve=output_reserve(raw_model), compactor=compactor, model=raw_model,
                    selector=GroundedSelector(raw_model, segment, input_window=window, control=events),
                    skill_selector=SkillRetentionSelector(raw_model, segment, input_window=window, control=events))
                trace = TraceRecorder()
                memory_query = text
                work = self.sessions.work_state(context)
                if not new_input and work.proposal:
                    memory_query = work.proposal.goal.objective+'\n'+text
                mcp_catalog = None if self.generic_tools else self._mcp_catalog(context)
                registry = self._registry(context, segment, raw_model, events, trace,
                    memory_query=memory_query, mcp_catalog=mcp_catalog)
                # WIRING-001：技能清单段常开；MCP 段仍只在配置了服务器时追加。
                # GENERIC-TOOLS-001：四工具模式没有 read_skill，也不挂 MCP，
                # 因此系统提示只保留通用工具说明与工作区根。
                from min_agent.context.system_prompt import SystemPrompt
                base = self._base_prompt()
                if self.generic_tools:
                    sections: list = []
                    prompt_sections = {"generic": {
                        "sha256": sha256(base.encode("utf-8")).hexdigest()}}
                else:
                    skill_catalog = SkillCatalog(Path(__file__).parent / "skills")
                    sections = [skill_catalog.prompt_section]
                    prompt_sections = {"skills": {
                        "sha256": sha256(skill_catalog.prompt_section().encode("utf-8")).hexdigest(),
                        "skill_count": len(skill_catalog.entries)}}
                    if mcp_catalog is not None:
                        sections.append(mcp_catalog.prompt_section)
                        prompt_sections["mcp"] = {
                            "sha256": sha256(mcp_catalog.prompt_section().encode("utf-8")).hexdigest()}
                system_prompt = SystemPrompt(base, tuple(sections))
                primary_model = raw_model
                if getattr(registry, '_memory_delivery', None) is not None:
                    from min_agent.long_memory.delivery import MemoryDeliveryModel
                    primary_model = MemoryDeliveryModel(raw_model, registry._memory_delivery)
                elif callable(getattr(raw_model, "generate_stream", None)):
                    # QWEN-STREAM-001：只在没有Memory递送包装时接管流式，避免绕过递送逻辑。
                    from min_agent.runtime.streaming import StreamingModel
                    primary_model = StreamingModel(raw_model, sink)
                # GOV-001-S3：generic 模式按策略挂执行前分级门（sidecar）。
                tool_gate = None
                if self.generic_tools and self.gate_policy != "off":
                    from min_agent.tools.gate import ToolGate
                    tool_gate = ToolGate(
                        self.sessions, context, self.workspace_root,
                        policy=self.gate_policy, approval_model=self.gate_model,
                        workspace=getattr(registry, "_generic_workspace", None))
                runtime = Runtime(ManagedModel(primary_model, manager, control=events), registry,
                    budget=segment, checkpoint_policy=CheckpointPolicy(), control=events,
                    progress_guard=ProgressGuard(self.sessions, context), recover_unknown_writes=True, trace=trace,
                    before_execute=self._freeze_public_goals if self.public_goals_enabled else None,
                    system_prompt=system_prompt, show_state=not self.generic_tools,
                    tool_gate=tool_gate)
                if checkpoint_id is not None:
                    result = runtime.resume_session(checkpoint_id, store=self.sessions, context=context, text=text)
                elif resume_task_id is not None:
                    result = runtime.run_session(
                        text, store=self.sessions, context=context, resume_task_id=resume_task_id)
                else:
                    result = runtime.run_session(text, store=self.sessions, context=context)
                record_run_trace(self.sessions, context, runtime.trace, run_id=result.run_id,
                    budget_id=budget_id, status=result.status, prompt_sections=prompt_sections)
                # WIRING-003：一轮结束只**提出候选**；失败不影响本轮任务结果。
                try:
                    self._auto_propose_memory(context)
                except Exception:
                    self.sessions.set_extension(context, 'long_memory_auto_error', 'auto_propose_failed')
                if result.status in {"partial", "failed", "cancelled"} and result.checkpoint is None:
                    self._save_factual_checkpoint(context, segment, result.error_code)
                text, checkpoint_id, resume_task_id = None, None, None
                if result.status != "completed": break
        except Exception:
            self._errors[identity] = "execution_paused_reload_required"
            try: SessionEvents(self.sessions, context).finished("failed")
            except Exception: pass
        finally:
            self._close_stream(identity)
            with self._lock:
                self._running.pop(identity, None)
                try: self.sessions.set_extension(context, "pending_input", None)
                except Exception: pass
                if not self._closing.is_set():
                    event_state = SessionEvents(self.sessions, context).snapshot()
                    if event_state["queue"] and event_state["status"] == "completed" and event_state["dispatch"]:
                        self._launch(context)

    def _save_factual_checkpoint(self, context, segment, code):
        snapshot = self.sessions.snapshot(context)
        refs = []
        for index, message in enumerate(snapshot.messages):
            if message.role != "tool": continue
            try:
                value = json.loads(message.content); ok = value.get("ok")
                ok = ok if type(ok) is bool else None
                state = "returned" if ok is True else value.get("error", {}).get("execution_state", "unknown")
                if state not in {"returned", "not_started", "unknown"}: state = "unknown"
            except (ValueError, AttributeError): ok, state = None, "unknown"
            refs.append(ToolResultRef(message_index=index, call_id=message.tool_call_id, ok=ok, execution_state=state))
        reason = code if code in {"context_limit", "no_progress", "user_stopped", "interrupted"} else "failed"
        cp = Checkpoint(segment_id=segment.segment_id, budget_id=segment.binding.budget_id, session_id=context.session_id,
            reason=reason, closing_status="not_started", model_note=None, tool_results=tuple(refs),
            history_length=len(snapshot.messages), task_usage=segment._ledger.usage, segment_usage=segment.usage)
        self.sessions.save_checkpoint(context, cp)

    def _recover_interrupted(self):
        for context in self.sessions.contexts():
            snapshot = self.sessions.snapshot(context)
            if snapshot.task is not None and snapshot.task.status == "running":
                pending = self.sessions._pending(snapshot.messages)
                missing = [Message(role="tool", tool_call_id=call_id, content=json.dumps({"ok": False,
                    "error": {"code": "interrupted", "execution_state": "unknown", "recovery": "verify_actual_state_before_retry"}})) for call_id in sorted(pending)]
                # QWEN-UX-001：进程被强杀时，正在处理的那条用户输入只在 pending_input 里；
                # 补回历史，避免"重新打开就什么都没有"。
                crashed_input = self.sessions.extension(context, "pending_input", None)
                if isinstance(crashed_input, str) and crashed_input.strip():
                    missing.insert(0, Message(role="user", content=crashed_input))
                self.sessions.finish(context, snapshot.task.task_id, status="partial", error_code="interrupted", messages=missing)
                self.sessions.set_extension(context, "pending_input", None)
                if snapshot.active_budget_id is not None:
                    ledger = SessionBudgets(self.sessions).open(context, snapshot.active_budget_id)
                    roots = [key for key, node in ledger.snapshot().segments.items() if node.parent_id is None]
                    if roots: self._save_factual_checkpoint(context, ledger.segment(roots[-1]), "interrupted")
                SessionEvents(self.sessions, context).finished("partial")

    def start_scheduler(self):
        if self._scheduler_thread is not None: return
        def loop():
            while not self._closing.is_set():
                try: self.scheduler.tick()
                except Exception: self._errors["scheduler"] = "scheduler_failed"
                self._closing.wait(1)
        self._scheduler_thread = Thread(target=loop, name="reminder-scheduler", daemon=True)
        self._scheduler_thread.start()

    def close(self):
        self._closing.set()
        for context in self.sessions.contexts():
            if str(context.session_id) in self._running:
                try: SessionEvents(self.sessions, context).stop()
                except Exception: pass
        self._pool.shutdown(wait=True, cancel_futures=True)
        if self._scheduler_thread: self._scheduler_thread.join(timeout=5)
        for mcp_catalog in list(self._mcp_catalogs.values()):
            try: mcp_catalog.close()
            except Exception: pass
        self._mcp_catalogs.clear()
        for fetcher in self._fetchers.values(): fetcher.close()
        if hasattr(self.model, "close"): self.model.close()
        catalog = self.model_catalog
        if catalog is not None and hasattr(catalog, "close"): catalog.close()
        self.storage.close()
