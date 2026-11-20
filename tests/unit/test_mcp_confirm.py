"""MCP confirm 排队协调器：登记去重、定向决定、一次性消费、跨会话隔离。"""

from uuid import UUID, uuid4

import pytest

from min_agent.mcp.confirm import MAX_RECORDS, MCPConfirmations
from min_agent.session.store import SessionStore


def make_pair():
    store = SessionStore()
    context = store.create(uuid4())
    return store, context, MCPConfirmations(store, context)


def request_echo(confirmations, message="hi"):
    return confirmations.request(server="fake", tool="echo",
                                 prefixed="mcp__fake__echo",
                                 arguments={"message": message})


def test_request_registers_pending_and_dedupes_same_signature():
    store, context, confirmations = make_pair()
    first = request_echo(confirmations)
    assert first["status"] == "pending"
    assert confirmations.pending() == [first]
    again = request_echo(confirmations)
    assert again["pending_id"] == first["pending_id"]
    different = request_echo(confirmations, message="other")
    assert different["pending_id"] != first["pending_id"]
    assert len(confirmations.pending()) == 2


def test_decide_only_pending_and_only_once():
    store, context, confirmations = make_pair()
    record = request_echo(confirmations)
    pending_id = UUID(record["pending_id"])
    approved = confirmations.decide(pending_id, True)
    assert approved["status"] == "approved"
    with pytest.raises(PermissionError):
        confirmations.decide(pending_id, False)
    with pytest.raises(PermissionError):
        confirmations.decide(uuid4(), True)
    with pytest.raises(TypeError):
        confirmations.decide(record["pending_id"], True)
    with pytest.raises(TypeError):
        confirmations.decide(pending_id, "yes")


def test_refusal_is_visible_to_execution_entry():
    store, context, confirmations = make_pair()
    record = request_echo(confirmations)
    confirmations.decide(UUID(record["pending_id"]), False)
    assert confirmations.check_and_consume("mcp__fake__echo", {"message": "hi"}) == "refused"
    assert confirmations.pending() == []


def test_approval_is_consumed_once_and_replay_is_refused():
    store, context, confirmations = make_pair()
    record = request_echo(confirmations)
    confirmations.decide(UUID(record["pending_id"]), True)
    assert confirmations.check_and_consume("mcp__fake__echo", {"message": "hi"}) == "approved"
    assert confirmations.check_and_consume("mcp__fake__echo", {"message": "hi"}) == "consumed"
    # 参数不同即不同签名：旧批准不能挪用。
    assert confirmations.check_and_consume("mcp__fake__echo", {"message": "other"}) == "none"


def test_pending_and_unknown_signatures_are_not_executable():
    store, context, confirmations = make_pair()
    request_echo(confirmations)
    assert confirmations.check_and_consume("mcp__fake__echo", {"message": "hi"}) == "pending"
    assert confirmations.check_and_consume("mcp__fake__add", {"a": 1, "b": 2}) == "none"


def test_records_survive_a_new_coordinator_instance():
    store, context, confirmations = make_pair()
    record = request_echo(confirmations)
    confirmations.decide(UUID(record["pending_id"]), True)
    reopened = MCPConfirmations(store, context)
    assert reopened.check_and_consume("mcp__fake__echo", {"message": "hi"}) == "approved"


def test_other_session_cannot_decide_or_consume():
    store, context, confirmations = make_pair()
    record = request_echo(confirmations)
    other_context = store.create(uuid4())
    other = MCPConfirmations(store, other_context)
    with pytest.raises(PermissionError):
        other.decide(UUID(record["pending_id"]), True)
    assert other.check_and_consume("mcp__fake__echo", {"message": "hi"}) == "none"
    assert other.pending() == []


def test_finished_records_are_pruned_beyond_capacity():
    store, context, confirmations = make_pair()
    for index in range(MAX_RECORDS):
        record = request_echo(confirmations, message=f"m{index}")
        confirmations.decide(UUID(record["pending_id"]), True)
        confirmations.check_and_consume("mcp__fake__echo", {"message": f"m{index}"})
    request_echo(confirmations, message="overflow")
    records = store.extension(context, "mcp_confirmations", {})
    assert len(records) == MAX_RECORDS
    statuses = [record["status"] for record in records.values()]
    assert statuses.count("pending") == 1
    assert statuses.count("consumed") == MAX_RECORDS - 1