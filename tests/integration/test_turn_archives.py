from uuid import uuid4
import pytest

from min_agent.application import HarnessApplication
from min_agent.memory import MemoryJournal
from min_agent.models import Message, ModelResponse, ToolCall
from min_agent.sessions import SessionStore
from tests.fakes.model import ScriptedModel


def test_two_completed_interactions_create_independent_archives_and_survive_restart(tmp_path):
    path=tmp_path/"rounds.sqlite3"
    harness=HarnessApplication(path,model=ScriptedModel([
        ModelResponse(finish_reason="stop",content="尚未核实的日期答复"),
        ModelResponse(finish_reason="stop",content="后续答复"),
    ]))
    identity=harness.create_session()["session_id"];context=harness.context(identity)
    for text in ["核对日期","后续问题"]:
        harness.submit(identity,text);harness.wait(identity)
    records=harness.sessions.extension(context,"memory_journal",{})
    assert len(records)==2
    first,second=records.values()
    assert first["start"]==0 and first["end"]==second["start"]
    assert first["kind"]==second["kind"]=="interaction"
    assert first["task_id"]!=second["task_id"]
    assert first["logical_task_id"]!=second["logical_task_id"]
    assert "尚未核实" in first["summary"]
    harness.close()
    harness=HarnessApplication(path)
    try:
        restored=harness.sessions.extension(harness.context(identity),"memory_journal",{})
        assert restored==records
        messages=[Message(role="user",content="压缩覆盖区间"),Message(role="assistant",content="统一摘要")]
        MemoryJournal(harness.sessions,harness.context(identity)).archive(messages,"压缩附加记录",start=0)
        assert all(harness.sessions.extension(harness.context(identity),"memory_journal",{})[key]==value
                   for key,value in records.items())
    finally:harness.close()


def test_unclosed_calls_are_not_archived_and_completed_resume_covers_original_interaction():
    store=SessionStore();context=store.create(uuid4())
    task=store.begin(context)
    store.append(context,task.task_id,[Message(role="user",content="记录"),
        Message(role="assistant",content="",tool_calls=[ToolCall(id="write",name="todo_add",arguments={})])])
    store.finish(context,task.task_id,status="failed")
    assert store.extension(context,"memory_journal",{})=={}
    with pytest.raises(RuntimeError):
        store.begin(context)
    store=SessionStore();context=store.create(uuid4())
    task=store.begin(context);store.append(context,task.task_id,[Message(role="user",content="待澄清")])
    store.finish(context,task.task_id,status="waiting_user")
    resumed=store.begin(context);store.append(context,resumed.task_id,[
        Message(role="user",content="补充信息"),Message(role="assistant",content="已回答")])
    store.finish(context,resumed.task_id,status="completed")
    record=next(iter(store.extension(context,"memory_journal",{}).values()))
    assert record["start"]==0 and record["end"]==3
    assert len(record["messages"])==3
