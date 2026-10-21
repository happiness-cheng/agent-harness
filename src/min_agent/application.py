"""本地应用的可信编排入口；会话单写入者、模型/工具与后台提醒解耦。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, RLock, Thread
from uuid import UUID, NAMESPACE_URL, uuid5
import json

from pydantic import BaseModel, ConfigDict, Field

from min_agent.artifacts import ArtifactStore
from min_agent.budget import BudgetLimits
from min_agent.budget_persistence import SessionBudgets
from min_agent.checkpoints import Checkpoint, ToolResultRef
from min_agent.checkpoints import CheckpointPolicy
from min_agent.compaction import Compactor
from min_agent.context import ContextManager, ManagedModel
from min_agent.events import SessionEvents
from min_agent.memory import MemoryJournal
from min_agent.models import Message, ModelResponse
from min_agent.notifications import Notifications
from min_agent.progress_guard import ProgressGuard
from min_agent.research import ResearchService
from min_agent.runtime import Runtime
from min_agent.scheduler import Scheduler
from min_agent.sessions import SessionStore, ExecutionContext
from min_agent.skills import SkillCatalog
from min_agent.storage import SQLiteStorage
from min_agent.task_state import ActionSpec, AuthorizationRequired, GoalSpec, TaskController
from min_agent.tools.calculator import CALCULATOR_TOOL
from min_agent.tools.read_webpage import PublicFetcher, WebReader
from min_agent.tools.reminder import ReminderService
from min_agent.tools.reminder_tools import register_reminders
from min_agent.tools.registry import ToolDefinition
from min_agent.tools.search import SearchService
from min_agent.tools.todo import TodoService
from min_agent.tools.todo_tools import todo_registry
from min_agent.trace import TraceContext, TraceRecorder, record_run_trace


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


class _Skill(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    name: str = Field(min_length=1, max_length=60)


class DemoModel:
    def generate(self, messages, *, tools=None):
        return ModelResponse(finish_reason="stop", content="离线演示已保存这条消息。当前没有接入真实模型；启用指定凭据后可由模型选择工具。")


class HarnessApplication:
    def __init__(self, database, *, model=None, model_factory=None, proxy=None, desktop=None,
                 total_models=40, segment_models=6, window=32768, owner_id=None):
        path = Path(database).resolve(); path.parent.mkdir(parents=True, exist_ok=True)
        self.storage = SQLiteStorage.open(path) if path.exists() else SQLiteStorage.create_new(path)
        self.sessions = SessionStore(storage=self.storage)
        self.owner_id = owner_id or uuid5(NAMESPACE_URL, "agent-harness-local:" + str(path))
        self.model = model or DemoModel(); self.model_factory = model_factory
        self.demo = isinstance(self.model, DemoModel) and model_factory is None
        self.proxy, self.total_models, self.segment_models, self.window = proxy, total_models, segment_models, window
        self._lock, self._running, self._errors, self._fetchers = RLock(), {}, {}, {}
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent-session")
        self._closing = Event(); self._scheduler_thread = None
        self.scheduler = Scheduler(self.sessions, desktop=desktop)
        self._recover_interrupted()

    def context(self, session_id):
        context = ExecutionContext(owner_id=self.owner_id, session_id=UUID(str(session_id)))
        return self.sessions.check_access(context)

    def create_session(self, label="新会话"):
        if not isinstance(label, str) or not label.strip() or len(label) > 60: raise ValueError("会话名需要1到60字符")
        context = self.sessions.create(self.owner_id)
        self.sessions.set_extension(context, "label", label)
        return {"session_id": str(context.session_id), "label": label}

    def list_sessions(self):
        result = []
        for context in self.sessions.contexts(self.owner_id):
            snapshot = self.sessions.snapshot(context)
            result.append({"session_id": str(context.session_id), "label": self.sessions.extension(context, "label", "会话"),
                "status": snapshot.task.status if snapshot.task else "ready",
                "unread": sum(event["unread"] for event in Notifications(self.sessions).list_items(context))})
        return result

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
            "error": self._errors.get(str(context.session_id)), "demo": self.demo}

    def submit(self, session_id, text):
        context = self.context(session_id)
        with self._lock:
            SessionEvents(self.sessions, context).enqueue(text)
            self._launch(context)

    def _launch(self, context, *, text=None, checkpoint_id=None):
        identity = str(context.session_id)
        if identity in self._running: return False
        if self._closing.is_set(): raise RuntimeError("应用正在关闭")
        self._running[identity] = self._pool.submit(self._worker, context, text, checkpoint_id)
        return True

    def wait(self, session_id, timeout=20):
        with self._lock: future = self._running.get(str(session_id))
        if future: future.result(timeout=timeout)

    def resume(self, session_id, *, checkpoint_id=None, task_id=None, text="继续已确认任务"):
        context = self.context(session_id)
        with self._lock:
            if str(context.session_id) in self._running: raise RuntimeError("会话正在执行")
            snapshot = self.sessions.snapshot(context)
            pending = snapshot.checkpoint is not None and snapshot.checkpoint.resumed_by is None
            if pending:
                if str(snapshot.checkpoint.checkpoint.checkpoint_id) != str(checkpoint_id): raise PermissionError("恢复必须指定当前检查点")
            elif snapshot.task is None or str(snapshot.task.task_id) != str(task_id) or snapshot.task.status == "completed":
                raise PermissionError("恢复必须定向指定暂停/等待任务")
            SessionEvents(self.sessions, context).resume_dispatch()
            self._launch(context, text=text, checkpoint_id=UUID(str(checkpoint_id)) if pending else None)

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

    def _registry(self, context, segment, raw_model, control, trace=None):
        controller = TaskController(self.sessions)
        registry = todo_registry(TodoService(self.sessions), controller, context)
        registry.register(CALCULATOR_TOOL)
        register_reminders(registry, ReminderService(self.sessions), controller, context)
        def propose(**kwargs): return self.propose(context, kwargs).model_dump_json()
        registry.register(ToolDefinition("propose_goal", "记录候选目标/约束/完成标准和精确动作清单供用户确认；不授权业务写入，不切换Build。普通聊天不必调用。", GoalParameters, propose))
        artifacts = ArtifactStore(self.sessions, context)
        registry.register(ToolDefinition("read_artifact", "按当前会话真实引用和范围补读原文；不能输入磁盘路径或其他会话ID。", _Reference,
            lambda artifact_id, start=0, limit=4000: json.dumps(artifacts.read(artifact_id, start=start, limit=limit), ensure_ascii=False)))
        registry.register(ToolDefinition("recall_memory", "检索当前会话相关归档摘要和引用，不跨会话，不授权执行。", _Question,
            lambda question: json.dumps([{key: value for key, value in record.items() if key != "messages"}
                for record in MemoryJournal(self.sessions, context).select(question)], ensure_ascii=False)))
        skill_root = Path(__file__).parent / "skills"
        catalog = SkillCatalog(skill_root)
        registry.register(ToolDefinition("read_skill", "读取已登记技能正文；内容是参考，不能增加工具/网络/写入权限。可用：" + json.dumps(catalog.summaries(), ensure_ascii=False),
                                        _Skill, lambda name: catalog.read(name)))
        def research(question):
            if not self.sessions.extension(context, "allow_public_web", False): raise AuthorizationRequired("network_not_authorized")
            with self._lock:
                if context.session_id not in self._fetchers:
                    self._fetchers[context.session_id] = PublicFetcher()
                fetcher = self._fetchers[context.session_id]
            search = SearchService(proxy=self.proxy)
            reader = WebReader(fetcher, artifacts)
            state = self.sessions.work_state(context)
            version = state.confirmed_goal_id
            task = self.sessions.snapshot(context).task
            class ResearchControl:
                def snapshot(inner):
                    current = control.snapshot()
                    return {"stop": current["stop"] or bool(current["steers"]), "steers": []}
                def poll(inner, task_id):
                    return inner.snapshot()["stop"], []
            result = ResearchService(raw_model, search, reader, artifacts).run(question, segment,
                constraints=state.proposal.goal.constraints if state.proposal else (), control=ResearchControl(),
                trace_context=TraceContext(session_id=context.session_id, task_id=task.task_id), trace=trace)
            if self.sessions.work_state(context).confirmed_goal_id != version:
                result = {"status": "partial", "error": "stale_goal_version", "evidence": None}
            return json.dumps(result, ensure_ascii=False)
        registry.register(ToolDefinition("research", "针对已允许的公开问题自主搜索、阅读与补查，返回有原文位置/限制/冲突的证据包；不复制私人聊天、不写业务或递归委派。", _Question, research))
        return registry

    def _worker(self, context, text=None, checkpoint_id=None):
        identity = str(context.session_id)
        try:
            while not self._closing.is_set():
                events = SessionEvents(self.sessions, context)
                new_input = text is None
                if new_input: text = events.next_input()
                if text is None: break
                snapshot = self.sessions.snapshot(context)
                budgets = SessionBudgets(self.sessions)
                if new_input and snapshot.task is not None and snapshot.task.status != "completed":
                    raise RuntimeError("暂停任务必须定向续跑，普通输入不能创建新账本")
                # 普通新输入开启新逻辑任务；定向续跑仅新建片段，复用原任务账本。
                budget_id = snapshot.active_budget_id
                if new_input or budget_id is None:
                    budget_id = budgets.create(context, BudgetLimits(model_calls=self.total_models, tool_calls=60))
                ledger = budgets.open(context, budget_id)
                segment = ledger.start_segment(BudgetLimits(model_calls=self.segment_models, tool_calls=12))
                raw_model = self.model_factory(context) if self.model_factory is not None else self.model
                compactor = Compactor(raw_model, self.sessions, context, segment, input_window=self.window, control=events)
                manager = ContextManager(self.sessions, context, ArtifactStore(self.sessions, context), window=self.window, compactor=compactor)
                trace = TraceRecorder()
                runtime = Runtime(ManagedModel(raw_model, manager, control=events), self._registry(context, segment, raw_model, events, trace),
                    budget=segment, checkpoint_policy=CheckpointPolicy(), control=events,
                    progress_guard=ProgressGuard(self.sessions, context), recover_unknown_writes=True, trace=trace)
                if checkpoint_id is not None:
                    result = runtime.resume_session(checkpoint_id, store=self.sessions, context=context, text=text)
                else:
                    result = runtime.run_session(text, store=self.sessions, context=context)
                record_run_trace(self.sessions, context, runtime.trace, run_id=result.run_id,
                    budget_id=budget_id, status=result.status)
                if result.status in {"partial", "failed", "cancelled"} and result.checkpoint is None:
                    self._save_factual_checkpoint(context, segment, result.error_code)
                text, checkpoint_id = None, None
                if result.status != "completed": break
        except Exception:
            self._errors[identity] = "execution_paused_reload_required"
            try: SessionEvents(self.sessions, context).finished("failed")
            except Exception: pass
        finally:
            with self._lock:
                self._running.pop(identity, None)
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
                self.sessions.finish(context, snapshot.task.task_id, status="partial", error_code="interrupted", messages=missing)
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
        for fetcher in self._fetchers.values(): fetcher.close()
        if hasattr(self.model, "close"): self.model.close()
        self.storage.close()
