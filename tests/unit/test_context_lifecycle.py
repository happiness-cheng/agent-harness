from uuid import uuid4
import json
import pytest

from min_agent.artifacts import ArtifactStore
from min_agent.context import ContextManager, ContextLimit
from min_agent.memory import MemoryJournal
from min_agent.skills import SkillCatalog
from min_agent.models import Message, ToolCall
from min_agent.sessions import SessionStore


def setup():
    store = SessionStore(); context = store.create(uuid4())
    return store, context, ArtifactStore(store, context)


def test_large_tool_projection_freezes_and_raw_stays_accessible():
    store, context, artifacts = setup()
    manager = ContextManager(store, context, artifacts, window=10000, output_reserve=1000)
    messages = [Message(role="user", content="question"), Message(role="assistant", content="", tool_calls=[
        ToolCall(id="large", name="read_webpage", arguments={"url": "https://example.com"}),
    ]), Message(role="tool", tool_call_id="large", content="original condition NOT allowed " * 400)]
    first = manager.prepare(messages)
    assert first[-1].content != messages[-1].content
    assert manager.prepare(messages)[-1] == first[-1]
    reopened = ContextManager(store, context, artifacts, window=10000, output_reserve=1000)
    assert reopened.prepare(messages)[-1] == first[-1]
    reference = json.loads(first[-1].content)["artifact_id"]
    assert artifacts.get(reference)["content"] == messages[-1].content
    assert len(messages[-1].content) > 5000


def test_oversized_current_input_pauses_without_trimming_user_or_system():
    store, context, artifacts = setup()
    manager = ContextManager(store, context, artifacts, window=2000, output_reserve=1000)
    with pytest.raises(ContextLimit): manager.prepare([Message(role="user", content="private " * 3000)])


def test_memory_retrieval_is_current_session_and_archives_complete_segments():
    store, context, _ = setup(); other = store.create(context.owner_id)
    journal = MemoryJournal(store, context)
    messages = [Message(role="user", content="2026-10-22 不要删除"), Message(role="assistant", content="保留否定")]
    identity = journal.archive(messages, "日期2026-10-22；不要删除", start=0)
    assert journal.read(identity)["summary"].startswith("日期2026")
    assert journal.select("删除")[0]["id"] == identity
    with pytest.raises(PermissionError): MemoryJournal(store, other).read(identity)
    with pytest.raises(ValueError): journal.archive([Message(role="assistant", content="", tool_calls=[
        ToolCall(id="pending", name="tool", arguments={}),
    ])], "不能拆调用配对", start=0)


def test_skill_paths_and_missing_names_cannot_escape_catalog(tmp_path):
    root = tmp_path / "skills"; root.mkdir()
    (root / "example.md").write_text("# 示例\n仅总结公开材料，不增加任何权限。", encoding="utf-8")
    catalog = SkillCatalog(root)
    assert catalog.summaries()[0]["name"] == "example"
    assert "不增加" in catalog.read("example")
    for name in ("../secret", "C:\\secret", "unknown"):
        with pytest.raises(PermissionError): catalog.read(name)


def test_memory_recall_does_not_return_unrelated_archives():
    store, context, _ = setup()
    journal = MemoryJournal(store, context)
    journal.archive([Message(role="user", content="单独的日期")], "截止日期2026-10-22", start=0)
    assert journal.select("交通规则") == []
