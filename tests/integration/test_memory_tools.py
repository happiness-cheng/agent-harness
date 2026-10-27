import json

import pytest

from min_agent.application import HarnessApplication
from min_agent.context import request_bound
from min_agent.memory import MemoryJournal
from min_agent.model_context import ModelContextLimits
from min_agent.models import Message, ModelResponse, ToolCall
from tests.fakes.model import ScriptedModel


class SourceReadModel(ScriptedModel):
    """合成容量只用于区分来源完整性与超窗暂停，不代表真实Provider。"""

    def __init__(self, script, *, window):
        super().__init__(script)
        self.window = window

    def context_limits(self):
        return ModelContextLimits(model="synthetic-source-read", context_window=self.window,
                                  max_output_tokens=4096, source="test-fixture:source-read")


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


@pytest.mark.parametrize(("window", "expected_status"), [
    pytest.param(65536, "completed", id="sufficient-synthetic-capacity"),
    pytest.param(32768, "partial", id="original-small-capacity-stops-safely"),
])
def test_explicit_source_read_and_structured_recall_are_not_silently_head_truncated(
    tmp_path, window, expected_status,
):
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
        model = SourceReadModel([
            ModelResponse(finish_reason="tool_calls", tool_calls=[
                ToolCall(id="source-long", name="read_memory_source", arguments={"reference": source}),
                ToolCall(id="past", name="recall_memory", arguments={"question": "周报"}),
            ]),
            ModelResponse(finish_reason="stop", content="已核对原文中的否定要求"),
        ], window=window)
        harness.submit(identity, "请补读完整原要求"); harness.wait(identity)
        saved = harness.snapshot(identity)
        assert saved["task"]["status"] == expected_status
        # 每次真正派发都满足相同的输出预留与安全余量，不能靠截断绕过容量门。
        for messages, tools in zip(model.calls, model.tool_schemas):
            assert request_bound(messages, tools, model=model) + 4096 + 1024 <= window
        if expected_status == "partial":
            assert saved["task"]["error_code"] == "context_limit"
            assert len(model.calls) == 1
            assert not any(message["role"] == "assistant" and
                           message["content"] == "已核对原文中的否定要求"
                           for message in saved["messages"])
            # 原 32K 配置继续安全暂停，但完整结果已保留，不能把暂停说成截断。
            outcomes = {message["tool_call_id"]: json.loads(message["content"])
                        for message in saved["messages"] if message["role"] == "tool"}
            assert json.loads(outcomes["source-long"]["result"])["text"] == text
            records = json.loads(outcomes["past"]["result"])
            assert records[0]["events"][0]["truncated"] is True
            assert records[0]["events"][0]["source"] == source
            return
        outcomes = {message.tool_call_id: json.loads(message.content)
                    for message in model.calls[-1] if message.role == "tool"}
        assert json.loads(outcomes["source-long"]["result"])["text"] == text
        records = json.loads(outcomes["past"]["result"])
        assert records[0]["events"][0]["truncated"] is True
        assert records[0]["events"][0]["source"] == source
    finally:
        harness.close()
