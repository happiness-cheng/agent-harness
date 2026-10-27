import json
from uuid import uuid4

import pytest

from min_agent.memory import MemoryJournal
from min_agent.models import Message, ToolCall
from min_agent.sessions import SessionStore
from min_agent.storage import SQLiteStorage


def completed_turn(store, context, text="周报原文：不要删除原始材料"):
    task = store.begin(context)
    store.append(context, task.task_id, [
        Message(role="user", content=text),
        Message(role="assistant", content="", tool_calls=[
            ToolCall(id=str(uuid4()), name="calculator", arguments={"expression": "1+1"}),
        ]),
    ])
    call_id = store.snapshot(context).messages[-1].tool_calls[0].id
    store.append(context, task.task_id, [
        Message(role="tool", tool_call_id=call_id, content='{"ok":true,"result":"2"}'),
        Message(role="assistant", content="周报已整理；此文字不是业务提交证明"),
    ])
    store.finish(context, task.task_id, status="completed")
    return MemoryJournal(store, context).select("周报")[-1]


def test_turn_has_ordered_events_with_readable_original_sources():
    store = SessionStore(); context = store.create(uuid4())
    record = completed_turn(store, context)
    assert [event["kind"] for event in record["events"]] == [
        "user_request", "tool_requested", "tool_result", "assistant_statement",
    ]
    assert len({event["id"] for event in record["events"]}) == 4
    assert record["events"][-1]["verified"] is False
    result_event = record["events"][2]
    assert result_event["tool_name"] == "calculator" and result_event["ok"] is True
    journal = MemoryJournal(store, context)
    original = journal.read_source(record["events"][0]["source"])
    assert original["text"] == "周报原文：不要删除原始材料"
    requested = journal.read_source(record["events"][1]["source"])
    assert json.loads(requested["text"])["tool_calls"][0]["arguments"] == {"expression": "1+1"}
    assert record["runtime_state"]["task"]["status"] == "completed"


@pytest.mark.parametrize("change", [
    {"session_id": str(uuid4())}, {"archive_id": str(uuid4())},
    {"message_index": 1000}, {"message_hash": "0" * 64},
    {"end": 100000}, {"start": 5, "end": 2}, {"start": -1},
    {"extra_permission": True},
])
def test_source_rejects_forgery_or_invalid_range(change):
    store = SessionStore(); context = store.create(uuid4())
    source = completed_turn(store, context)["events"][0]["source"]
    with pytest.raises((ValueError, PermissionError)):
        MemoryJournal(store, context).read_source({**source, **change})


def test_same_owner_other_session_cannot_read_source():
    store = SessionStore(); context = store.create(uuid4()); other = store.create(context.owner_id)
    source = completed_turn(store, context)["events"][0]["source"]
    with pytest.raises(PermissionError):
        MemoryJournal(store, other).read_source(source)


def test_source_is_paginated_without_changing_original_and_rejects_invalid_limits():
    store = SessionStore(); context = store.create(uuid4())
    record = completed_turn(store, context, "周报" + "原始材料" * 3000)
    journal = MemoryJournal(store, context)
    source = record["events"][0]["source"]
    page = journal.read_source(source, offset=2, limit=4)
    assert page["text"] == "原始材料" and page["truncated"]
    assert len(journal.read(record["id"])["messages"][0]["content"]) > 10000
    for options in ({"offset": -1}, {"offset": True}, {"limit": 8001}, {"limit": 0}):
        with pytest.raises(ValueError):
            journal.read_source(source, **options)


def test_original_reference_survives_later_turn_and_restart(tmp_path):
    path = tmp_path / "memory.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        store = SessionStore(storage=storage); context = store.create(uuid4())
        first = completed_turn(store, context)
        completed_turn(store, context, "后续周报修改")
        source = first["events"][0]["source"]
    with SQLiteStorage.open(path) as storage:
        journal = MemoryJournal(SessionStore(storage=storage), context)
        assert journal.read_source(source)["text"] == "周报原文：不要删除原始材料"
        assert journal.read(first["id"])["events"] == first["events"]


def test_constructed_model_cannot_skip_source_range_validation():
    from min_agent.memory_records import SourceReference
    store = SessionStore(); context = store.create(uuid4())
    source = completed_turn(store, context)["events"][0]["source"]
    valid = SourceReference.model_validate(source).model_dump()
    forged = SourceReference.model_construct(**{**valid, "start": -1})
    with pytest.raises(ValueError):
        MemoryJournal(store, context).read_source(forged)


def test_source_read_rejects_modified_archived_original():
    store = SessionStore(); context = store.create(uuid4())
    record = completed_turn(store, context)
    records = store.extension(context, "memory_journal", {})
    records[record["id"]]["messages"][0]["content"] = "篡改后的原文"
    store.set_extension(context, "memory_journal", records)
    with pytest.raises(ValueError, match="完整性"):
        MemoryJournal(store, context).read_source(record["events"][0]["source"])


def test_legacy_interaction_gets_readable_sources_without_rewriting_saved_history():
    store = SessionStore(); context = store.create(uuid4())
    record = completed_turn(store, context)
    records = store.extension(context, "memory_journal", {})
    for key in ("events", "runtime_state", "schema_version"):
        records[record["id"]].pop(key)
    store.set_extension(context, "memory_journal", records)
    journal = MemoryJournal(store, context)
    recalled = journal.select("周报")[0]
    assert journal.read_source(recalled["events"][0]["source"])["text"].startswith("周报原文")
    assert "runtime_state" not in recalled
    assert store.extension(context, "memory_journal", {}) == records
