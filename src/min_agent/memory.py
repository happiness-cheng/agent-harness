"""按完整交互段归档；只读当前会话引用，不做跨会话召回。"""

import json
from uuid import uuid4, uuid5
from min_agent.checkpoints import history_hash
from min_agent.memory_records import SourceReference, interaction_events, runtime_state, source_text
from min_agent.models import Message
from min_agent.sessions import SessionStore


def record_completed_interaction(snapshot):
    """无需模型调用，按完整交互覆盖范围记录原文、工具状态与待核实文字。"""
    task = snapshot.task
    if task is None or task.status != "completed":
        return
    start = task.history_start
    messages = snapshot.messages[start:]
    if not messages:
        return
    if SessionStore._pending(messages):
        raise ValueError("未配对工具请求不能归档为完整交互")
    tool_results = []
    for index, message in enumerate(messages, start):
        if message.role != "tool":
            continue
        try:
            value = json.loads(message.content)
            ok = value.get("ok") if type(value.get("ok")) is bool else None
            execution = "returned" if ok is True else value.get("error", {}).get("execution_state", "unknown")
        except (ValueError, TypeError, AttributeError):
            ok, execution = None, "unknown"
        tool_results.append({"message_index": index, "call_id": message.tool_call_id,
                             "ok": ok, "execution_state": execution})
    summary = json.dumps({
        "user_requests": [m.content[:1000] for m in messages if m.role == "user"],
        "assistant_text_unverified": [m.content[:1000] for m in messages if m.role == "assistant" and m.content],
        "tool_results": tool_results, "mode": snapshot.work_state.mode,
        "confirmed_goal_id": str(snapshot.work_state.confirmed_goal_id) if snapshot.work_state.confirmed_goal_id else None,
        "completion_meaning": "模型正常结束，不代表业务目标已独立验证",
    }, ensure_ascii=False)
    identity = str(uuid5(snapshot.session_id, "interaction:" + str(task.task_id)))
    snapshot.extensions.setdefault("memory_journal", {})[identity] = {
        "id": identity, "kind": "interaction", "task_id": str(task.task_id),
        "logical_task_id": str(snapshot.active_budget_id or task.task_id),
        "start": start, "end": len(snapshot.messages), "history_hash": history_hash(messages),
        "summary": summary, "tool_results": tool_results,
        "messages": [message.model_dump(mode="json") for message in messages],
        "schema_version": 2,
        "events": interaction_events(snapshot, identity, messages, start),
        "runtime_state": runtime_state(snapshot),
    }


class MemoryJournal:
    def __init__(self, sessions, context):
        self.sessions, self.context = sessions, sessions.check_access(context)

    def archive(self, messages, summary, *, start):
        if not messages or SessionStore._pending(messages):
            raise ValueError("归档必须包含完整调用与结果")
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 20000:
            raise ValueError("归档摘要长度无效")
        with self.sessions.control(self.context):
            records = self.sessions.extension(self.context, "memory_journal", {})
            identity = str(uuid4())
            records[identity] = {"id": identity, "kind": "compaction", "start": start, "end": start + len(messages),
                "history_hash": history_hash(messages), "summary": summary,
                "messages": [message.model_dump(mode="json") for message in messages]}
            self.sessions.set_extension(self.context, "memory_journal", records)
            return identity

    def read(self, identity):
        record = self.sessions.extension(self.context, "memory_journal", {}).get(str(identity))
        if record is None: raise PermissionError("归档引用不属于当前会话")
        return record

    def read_source(self, reference, *, offset=0, limit=4000):
        """读取已归档原文；校验的是来源完整性，不是模型结论的语义。"""
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 8000:
            raise ValueError("原文分页范围无效")
        payload = reference.model_dump(warnings="error") if isinstance(reference, SourceReference) else reference
        source = SourceReference.model_validate(payload)
        if source.session_id != self.context.session_id:
            raise PermissionError("原文引用不属于当前会话")
        record = self.read(str(source.archive_id))
        messages = [Message.model_validate(value) for value in record["messages"]]
        if history_hash(messages) != record["history_hash"]:
            raise ValueError("归档原文完整性校验失败")
        position = source.message_index - record["start"]
        if not 0 <= position < len(messages):
            raise ValueError("引用消息不在归档范围内")
        message = messages[position]
        if history_hash([message]) != source.message_hash:
            raise ValueError("引用消息版本不匹配")
        text = source_text(message)
        if source.end > len(text) or source.start + offset > source.end:
            raise ValueError("引用字符范围超出原文")
        start = source.start + offset
        end = min(start + limit, source.end)
        return {"source": source.model_dump(mode="json"), "role": message.role,
                "start": start, "end": end, "text": text[start:end],
                "truncated": end < source.end}

    def select(self, question, limit=3):
        if type(limit) is not int or not 1 <= limit <= 10: raise ValueError("归档数量无效")
        if not isinstance(question, str) or not question.strip(): raise ValueError("回查问题不能为空")
        records = list(self.sessions.extension(self.context, "memory_journal", {}).values())
        tokens = {question.casefold(), *question.casefold().split()}
        scored = [(sum(token in item["summary"].casefold() for token in tokens), item) for item in records]
        selected = [item for score, item in sorted(scored, key=lambda pair: pair[0], reverse=True) if score > 0][:limit]
        for record in selected:
            if record.get("kind") == "interaction" and "events" not in record:
                # 旧归档只在读取副本上补引用；不重写历史，也不猜测当时控制状态。
                messages = [Message.model_validate(value) for value in record["messages"]]
                record["events"] = interaction_events(self.context, record["id"], messages, record["start"])
        return selected
