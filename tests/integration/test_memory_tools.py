import json

from min_agent.application import HarnessApplication
from min_agent.memory import MemoryJournal
from min_agent.models import Message, ModelResponse, ToolCall
from tests.fakes.model import ScriptedModel


def test_main_runtime_reads_current_state_and_verifiable_archived_source(tmp_path):
    model = None
    harness = HarnessApplication(tmp_path / "tools.sqlite3", model_factory=lambda context: model)
    try:
        identity = harness.create_session()["session_id"]; context = harness.context(identity)
        task = harness.sessions.begin(context)
        harness.sessions.append(context, task.task_id, [
            Message(role="user", content="周报：不要删除原文"), Message(role="assistant", content="已了解"),
        ])
        harness.sessions.finish(context, task.task_id, status="completed")
        source = MemoryJournal(harness.sessions, context).select("周报")[0]["events"][0]["source"]
        model = ScriptedModel([
            ModelResponse(finish_reason="tool_calls", tool_calls=[
                ToolCall(id="current-state", name="read_session_state", arguments={}),
                ToolCall(id="original", name="read_memory_source", arguments={"reference": source}),
            ]),
            ModelResponse(finish_reason="stop", content="原要求是不删除；尚未创建待办"),
        ])
        harness.submit(identity, "核对当前状态与周报原要求"); harness.wait(identity)
        assert harness.snapshot(identity)["task"]["status"] == "completed"
        results = {message.tool_call_id: json.loads(json.loads(message.content)["result"])
                   for message in model.calls[-1] if message.role == "tool"}
        assert results["current-state"]["source_kind"] == "runtime_state"
        assert results["current-state"]["todos"] == []
        assert results["original"]["text"] == "周报：不要删除原文"
        assert {"read_session_state", "read_memory_source"}.issubset(
            {schema["name"] for schema in model.tool_schemas[0]})
    finally:
        harness.close()


def test_explicit_source_read_and_structured_recall_are_not_silently_head_truncated(tmp_path):
    model = None
    harness = HarnessApplication(tmp_path / "long-source.sqlite3", model_factory=lambda context: model)
    try:
        identity = harness.create_session()["session_id"]; context = harness.context(identity)
        text = "周报：" + "background " * 220 + "不要删除原文"
        task = harness.sessions.begin(context)
        harness.sessions.append(context, task.task_id, [
            Message(role="user", content=text), Message(role="assistant", content="已了解"),
        ])
        harness.sessions.finish(context, task.task_id, status="completed")
        source = MemoryJournal(harness.sessions, context).select("周报")[0]["events"][0]["source"]
        model = ScriptedModel([
            ModelResponse(finish_reason="tool_calls", tool_calls=[
                ToolCall(id="source-long", name="read_memory_source", arguments={"reference": source}),
                ToolCall(id="past", name="recall_memory", arguments={"question": "周报"}),
            ]),
            ModelResponse(finish_reason="stop", content="已核对原文中的否定要求"),
        ])
        harness.submit(identity, "请补读完整原要求"); harness.wait(identity)
        assert harness.snapshot(identity)["task"]["status"] == "completed"
        outcomes = {message.tool_call_id: json.loads(message.content)
                    for message in model.calls[-1] if message.role == "tool"}
        assert json.loads(outcomes["source-long"]["result"])["text"] == text
        records = json.loads(outcomes["past"]["result"])
        assert records[0]["events"][0]["truncated"] is True
        assert records[0]["events"][0]["source"] == source
    finally:
        harness.close()
