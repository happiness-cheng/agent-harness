"""原始材料与冻结模型投影分离；窗口计算明确采用保守字节上界。"""

from hashlib import sha256
import json
from copy import deepcopy

from min_agent.models import Message
from min_agent.skills import latest_skill_snapshots, project_skill_results, restored_skill_snapshots
from min_agent.skill_retention import managed_skill_context, normalize_selection, skill_history_projection
from min_agent.state_bar import RuntimeContextMessage


class ContextLimit(RuntimeError):
    pass


def request_bound(messages, tools=None, *, model=None):
    # 真实Provider只统计实际请求体；不同时计算内部可见消息和它对应的原生续传状态。
    # UTF-8字节仍是保守估计，不冒充Tokenizer或服务端精确Token计数。
    builder = getattr(model, '_build_body', None)
    if callable(builder):
        payload = builder(messages, tools)
    else:
        payload = {"messages": [m.model_dump(mode="json") for m in messages], "tools": tools or []}
    return len(json.dumps(payload, ensure_ascii=False).encode()) + 1024


class ContextManager:
    def __init__(self, sessions, context, artifacts, *, window=None, output_reserve=None,
                 safety_margin=1024, compactor=None, selector=None, state_reader=None, skill_selector=None, model=None):
        from min_agent.model_context import context_window, output_reserve as configured_output
        window = context_window(model, override=window)
        if output_reserve is None:
            output_reserve = configured_output(model)
        if any(type(value) is not int or value < 0 for value in (window, output_reserve, safety_margin)):
            raise ValueError("窗口预算必须为非负整数")
        self.sessions, self.context, self.artifacts = sessions, sessions.check_access(context), artifacts
        self.window, self.output_reserve, self.safety_margin = window, output_reserve, safety_margin
        self.model = model
        self.compactor = compactor
        self.selector, self.state_reader = selector, state_reader
        self.skill_selector = skill_selector

    def prepare(self, messages, tools=None):
        from min_agent.compression_text import tool_summary
        from min_agent.structured_context import structured_history
        # 仅可信类型的临时投影脱离历史；普通用户伪造同名JSON仍是原历史。
        dynamic = messages[-1:] if messages and isinstance(messages[-1], RuntimeContextMessage) else []
        if dynamic:
            messages = messages[:-1]
        objective = self._objective(messages)
        with self.sessions.control(self.context):
            state = self.sessions.extension(self.context, "context", {"version": 1, "projections": {}, "summary": None, "covered": 0, "covered_hashes": []})
        projected = []
        requests = {}
        try:
            for message in messages:
                requests.update((call.id, call.name) for call in message.tool_calls)
                # Skill是执行规程，不交给普通工具摘要；仍接受整体窗口预算检查。
                if (message.role == "tool" and len(message.content.encode()) > 2000
                        and requests.get(message.tool_call_id) != "read_skill"):
                    key = sha256(message.model_dump_json().encode()).hexdigest()
                    if key not in state["projections"]:
                        if requests.get(message.tool_call_id) in {
                            "recall_memory", "read_memory_source", "read_session_state", "read_artifact",
                        }:
                            # 结构化记忆/显式分页不能再截成不完整JSON；总窗口仍受_fits约束。
                            state["projections"][key] = message.content
                        else:
                            attempt = key + ":" + sha256(objective.encode()).hexdigest()
                            failures = self.sessions.extension(self.context, "tool_summary_failures", {})
                            if failures.get(attempt, 0) >= 2:
                                raise ContextLimit("当前工具来源摘要已熔断")
                            try:
                                state["projections"][key] = tool_summary(
                                    message.content, objective, self.artifacts, self.selector)
                            except Exception:
                                control = getattr(self.selector, "control", None)
                                stopped = control is not None and control.snapshot()["stop"]
                                if not stopped:
                                    failures[attempt] = failures.get(attempt, 0) + 1
                                    self.sessions.set_extension(self.context, "tool_summary_failures", failures)
                                raise
                        self.sessions.set_extension(self.context, "context", state)
                    projected.append(message.model_copy(update={"content": state["projections"][key]}, deep=True))
                else:
                    projected.append(message.model_copy(deep=True))
        except Exception as error:
            raise ContextLimit("工具摘要失败，原文保留且不发布截断结果") from error
        self.sessions.set_extension(self.context, "context", state)
        prefix = 0
        if projected and projected[0].role == "system":
            prefix = 1
        core, stable = projected[prefix:], projected[:prefix]
        hashes = [sha256(message.model_dump_json().encode()).hexdigest() for message in core]
        if state["covered"]:
            if hashes[:state["covered"]] != state["covered_hashes"]:
                raise ContextLimit("上下文版本不匹配，停止而不重新摘要旧投影")
        if state["covered"] or state.get("skill_retention"):
            visible = self._visible(stable, core, state, dynamic)
        else:
            visible = projected + dynamic
        if self._fits(visible, tools): return visible
        # 先选择整份Skill，再整理普通历史；仅窗口有压力时触发。
        state = self._prepare_skill_retention(core, state)
        visible = self._visible(stable, core, state, dynamic)
        if self._fits(visible, tools):
            self.sessions.set_extension(self.context, "context", state)
            return visible
        boundaries = [index for index, message in enumerate(core) if message.role == "user" and index > state["covered"]]
        end = boundaries[-2] if len(boundaries) >= 2 else None
        # 第二层先于LLM兜底压缩，且从原始消息构建，不能从预览恢复原文。
        structured_end = end if end is not None else state["covered"]
        if structured_end:
            try:
                candidate = deepcopy(state)
                candidate.update(summary=structured_history(self.sessions, self.context,
                    messages[prefix:], structured_end, objective), covered=structured_end, covered_hashes=hashes[:structured_end],
                    version=state["version"] + 1, stage="structured")
                structured = self._visible(stable, core, candidate, dynamic)
                if self._fits(structured, tools):
                    self.sessions.set_extension(self.context, "context", candidate)
                    return structured
            except ValueError:
                pass
        if self.compactor is None: raise ContextLimit("结构化层仍放不下，需要LLM兜底或分段")
        self.compactor.objective = objective
        window_key = self.compactor.window_attempt(state, core, tools,
            (self.window, self.output_reserve, self.safety_margin))
        # 先整理现有记忆，再考虑较旧完整历史；原始消息始终保留。
        if state["summary"]:
            state = self.compactor.compact_memory(state)
            visible = self._visible(stable, core, state, dynamic)
            if self._fits(visible, tools):
                self.sessions.set_extension(self.context, "context", state)
                return visible
        if end is None:
            self.compactor.reject_window(window_key)
            raise ContextLimit("保留近期完整交互后仍无可压缩历史")
        state = self.compactor.compact_history(state, messages[prefix:][state["covered"]:end], hashes[:end], end)
        state["stage"] = "llm"
        visible = self._visible(stable, core, state, dynamic)
        if not self._fits(visible, tools):
            self.compactor.reject_window(window_key)
            raise ContextLimit("压缩后窗口仍不足")
        self.sessions.set_extension(self.context, "context", state)
        return visible

    def _objective(self, messages):
        work = self.sessions.work_state(self.context)
        goal = work.proposal.goal.model_dump(mode="json") if work.proposal else None
        latest = next((message.content for message in reversed(messages) if message.role == "user"), "")
        return json.dumps({"goal": goal, "current_request": latest}, ensure_ascii=False)

    def _prepare_skill_retention(self, core, state):
        candidates = [snapshot for _, snapshot in latest_skill_snapshots(core)]
        if not candidates or self.skill_selector is None:
            return state
        # 旧摘要只覆盖 covered 以前的记录，其后的任务依据必须完整提供。
        start = state["covered"]
        task_context = {"previous_memory": state["summary"], "uncompressed_messages": [
            message.model_dump(mode="json", exclude={"provider_state"})
            for message in skill_history_projection(core[start:])]}
        try:
            selection = self.skill_selector.select(candidates, task_context)
        except ContextLimit:
            raise
        except Exception:
            selection = None
        candidate = deepcopy(state)
        candidate["skill_retention"] = normalize_selection(selection, candidates)
        return candidate

    def _visible(self, stable, core, state, dynamic=()):
        visible = stable + [Message(role="user", content=json.dumps(
            {"untrusted_session_memory": state["summary"]}, ensure_ascii=False))]
        if state.get("skill_retention"):
            visible += managed_skill_context(core, state["covered"], state["skill_retention"])
        else:
            visible += restored_skill_snapshots(core, state["covered"])
            visible += core[state["covered"]:]
        if self.state_reader is not None:
            visible.append(Message(role="user", content=json.dumps(
                {"current_runtime_state": self.state_reader()}, ensure_ascii=False)))
        return visible + list(dynamic)

    def _fits(self, messages, tools):
        return request_bound(project_skill_results(messages), tools, model=self.model) + self.output_reserve + self.safety_margin <= self.window


class ManagedModel:
    def __init__(self, model, manager, *, control=None):
        self.model, self.manager = model, manager
        self.control = control

    def generate(self, messages, *, tools=None):
        if self.control is not None and self.control.snapshot()["stop"]:
            raise ContextLimit("用户已停止，不启动上下文或模型请求")
        visible = self.manager.prepare(messages, tools)
        if self.control is not None and self.control.snapshot()["stop"]:
            raise ContextLimit("上下文准备期间用户已停止，不刷新状态或启动模型请求")
        # 压缩可能改变版本和预算；派发前刷新一次，再检查完整请求预算。
        refreshed = False
        for message in visible:
            if isinstance(message, RuntimeContextMessage):
                message.refresh()
                refreshed = True
        if refreshed and not self.manager._fits(visible, tools):
            raise ContextLimit("最新状态投影加入后窗口不足")
        projected = project_skill_results(visible)
        if self.control is not None and self.control.snapshot()["stop"]:
            raise ContextLimit("上下文准备期间用户已停止，不启动后续模型请求")
        return self.model.generate(projected, tools=tools)
