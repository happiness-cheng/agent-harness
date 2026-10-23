"""原始材料与冻结模型投影分离；窗口计算明确采用保守字节上界。"""

from hashlib import sha256
import json

from min_agent.models import Message
from min_agent.skills import project_skill_results


class ContextLimit(RuntimeError):
    pass


def request_bound(messages, tools=None):
    # 文本/JSON的UTF-8字节数作为保守预算，不冒充provider精确token计量。
    return len(json.dumps({"messages": [m.model_dump(mode="json") for m in messages], "tools": tools or []},
                          ensure_ascii=False).encode()) + 1024


class ContextManager:
    def __init__(self, sessions, context, artifacts, *, window=32768, output_reserve=4096,
                 safety_margin=1024, compactor=None):
        if any(type(value) is not int or value < 0 for value in (window, output_reserve, safety_margin)):
            raise ValueError("窗口预算必须为非负整数")
        self.sessions, self.context, self.artifacts = sessions, sessions.check_access(context), artifacts
        self.window, self.output_reserve, self.safety_margin = window, output_reserve, safety_margin
        self.compactor = compactor

    def prepare(self, messages, tools=None):
        with self.sessions.control(self.context):
            state = self.sessions.extension(self.context, "context", {"version": 1, "projections": {}, "summary": None, "covered": 0, "covered_hashes": []})
            projected = []
            for message in messages:
                if message.role == "tool" and len(message.content.encode()) > 2000:
                    key = sha256(message.model_dump_json().encode()).hexdigest()
                    if key not in state["projections"]:
                        identity = self.artifacts.save(message.content, kind="tool_result")
                        preview = {"artifact_id": identity, "preview": message.content[:1200],
                                   "truncated": True, "original_bytes": len(message.content.encode()),
                                   "read_more": "read_artifact按ID和范围读取当前会话原文"}
                        state["projections"][key] = json.dumps(preview, ensure_ascii=False)
                    projected.append(message.model_copy(update={"content": state["projections"][key]}, deep=True))
                else:
                    projected.append(message.model_copy(deep=True))
            self.sessions.set_extension(self.context, "context", state)
        prefix = 0
        if projected and projected[0].role == "system":
            prefix = 1
            if len(projected) > 1 and projected[1].role == "user" and '"application_state"' in projected[1].content:
                prefix = 2
        core, stable = projected[prefix:], projected[:prefix]
        hashes = [sha256(message.model_dump_json().encode()).hexdigest() for message in core]
        if state["covered"]:
            if hashes[:state["covered"]] != state["covered_hashes"]:
                raise ContextLimit("上下文版本不匹配，停止而不重新摘要旧投影")
            visible = stable + [Message(role="user", content=json.dumps({"untrusted_session_memory": state["summary"]}, ensure_ascii=False))] + core[state["covered"]:]
        else:
            visible = projected
        if self._fits(visible, tools): return visible
        if self.compactor is None: raise ContextLimit("上下文不足，需要压缩或分段")
        window_key = self.compactor.window_attempt(state, core, tools,
            (self.window, self.output_reserve, self.safety_margin))
        # 先整理现有记忆，再考虑较旧完整历史；原始消息始终保留。
        if state["summary"]:
            state = self.compactor.compact_memory(state)
            visible = stable + [Message(role="user", content=json.dumps({"untrusted_session_memory": state["summary"]}, ensure_ascii=False))] + core[state["covered"]:]
            if self._fits(visible, tools):
                self.sessions.set_extension(self.context, "context", state)
                return visible
        boundaries = [index for index, message in enumerate(core) if message.role == "user" and index > state["covered"]]
        if len(boundaries) < 2:
            self.compactor.reject_window(window_key)
            raise ContextLimit("保留近期完整交互后仍无可压缩历史")
        end = boundaries[-2]
        state = self.compactor.compact_history(state, core[state["covered"]:end], hashes[:end], end)
        visible = stable + [Message(role="user", content=json.dumps({"untrusted_session_memory": state["summary"]}, ensure_ascii=False))] + core[end:]
        if not self._fits(visible, tools):
            self.compactor.reject_window(window_key)
            raise ContextLimit("压缩后窗口仍不足")
        self.sessions.set_extension(self.context, "context", state)
        return visible

    def _fits(self, messages, tools):
        return request_bound(project_skill_results(messages), tools) + self.output_reserve + self.safety_margin <= self.window


class ManagedModel:
    def __init__(self, model, manager, *, control=None):
        self.model, self.manager = model, manager
        self.control = control

    def generate(self, messages, *, tools=None):
        if self.control is not None and self.control.snapshot()["stop"]:
            raise ContextLimit("用户已停止，不启动上下文或模型请求")
        projected = project_skill_results(self.manager.prepare(messages, tools))
        if self.control is not None and self.control.snapshot()["stop"]:
            raise ContextLimit("上下文准备期间用户已停止，不启动后续模型请求")
        return self.model.generate(projected, tools=tools)
