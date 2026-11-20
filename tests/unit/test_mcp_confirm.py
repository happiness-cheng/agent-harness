"""MCP confirm 排队协调器：任务绑定、TTL、一次性消费、跨任务隔离与容量。"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from min_agent.mcp.confirm import (
    MAX_PENDING, MAX_RECORDS, ConfirmationBacklogError, MCPConfirmations,
)
from min_agent.session.store import SessionStore

BASE = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def make_pair(*, ttl_seconds=60):
    store = SessionStore()
    context = store.create(uuid4())
    clock = [BASE]
    confirmations = MCPConfirmations(
        store, context, ttl_seconds=ttl_seconds, now=lambda: clock[0])
    return store, context, confirmations, clock


def request_echo(confirmations, message="hi", task="A"):
    return confirmations.request(server="fake", tool="echo",
                                 prefixed="mcp__fake__echo",
                                 arguments={"message": message},
                                 logical_task_id=task)


def test_request_registers_pending_and_dedupes_within_one_task():
    _, _, confirmations, _ = make_pair()
    first = request_echo(confirmations)
    assert first["status"] == "pending"
    assert first["logical_task_id"] == "A"
    assert confirmations.pending() == [first]
    again = request_echo(confirmations)
    assert again["pending_id"] == first["pending_id"]
    different = request_echo(confirmations, message="other")
    assert different["pending_id"] != first["pending_id"]
    assert len(confirmations.pending()) == 2


def test_same_signature_in_another_task_gets_a_new_pending():
    _, _, confirmations, _ = make_pair()
    first = request_echo(confirmations, task="A")
    second = request_echo(confirmations, task="B")
    assert second["pending_id"] != first["pending_id"]
    assert len(confirmations.pending()) == 2


def test_decide_only_pending_and_only_once():
    _, _, confirmations, _ = make_pair()
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


def test_refusal_only_blocks_the_same_task():
    _, _, confirmations, _ = make_pair()
    record = request_echo(confirmations, task="A")
    confirmations.decide(UUID(record["pending_id"]), False)
    assert confirmations.check_and_consume(
        "mcp__fake__echo", {"message": "hi"}, logical_task_id="A") == "refused"
    # 新任务同签名不是重放：没有当前任务的记录，返回 none 并重新排队。
    assert confirmations.check_and_consume(
        "mcp__fake__echo", {"message": "hi"}, logical_task_id="B") == "none"
    fresh = request_echo(confirmations, task="B")
    assert fresh["status"] == "pending"


def test_approval_is_consumed_once_and_not_reused_by_another_task():
    _, _, confirmations, _ = make_pair()
    record = request_echo(confirmations, task="A")
    confirmations.decide(UUID(record["pending_id"]), True)
    assert confirmations.check_and_consume(
        "mcp__fake__echo", {"message": "hi"}, logical_task_id="A") == "approved"
    assert confirmations.check_and_consume(
        "mcp__fake__echo", {"message": "hi"}, logical_task_id="A") == "consumed"
    # M1：A 的批准不能被 B 的相同签名挪用。
    assert confirmations.check_and_consume(
        "mcp__fake__echo", {"message": "hi"}, logical_task_id="B") == "none"
    assert request_echo(confirmations, task="B")["status"] == "pending"
    # 参数不同即不同签名：旧批准不能挪用。
    assert confirmations.check_and_consume(
        "mcp__fake__echo", {"message": "other"}, logical_task_id="A") == "none"


def test_pending_and_unknown_signatures_are_not_executable():
    _, _, confirmations, _ = make_pair()
    request_echo(confirmations)
    assert confirmations.check_and_consume(
        "mcp__fake__echo", {"message": "hi"}, logical_task_id="A") == "pending"
    assert confirmations.check_and_consume(
        "mcp__fake__add", {"a": 1, "b": 2}, logical_task_id="A") == "none"


def test_records_survive_a_new_coordinator_instance():
    store, context, confirmations, _ = make_pair()
    record = request_echo(confirmations)
    confirmations.decide(UUID(record["pending_id"]), True)
    reopened = MCPConfirmations(store, context, ttl_seconds=60,
                                now=lambda: BASE)
    assert reopened.check_and_consume(
        "mcp__fake__echo", {"message": "hi"}, logical_task_id="A") == "approved"


def test_other_session_cannot_decide_or_consume():
    store, _, confirmations, _ = make_pair()
    record = request_echo(confirmations)
    other_context = store.create(uuid4())
    other = MCPConfirmations(store, other_context, ttl_seconds=60,
                             now=lambda: BASE)
    with pytest.raises(PermissionError):
        other.decide(UUID(record["pending_id"]), True)
    assert other.check_and_consume(
        "mcp__fake__echo", {"message": "hi"}, logical_task_id="A") == "none"
    assert other.pending() == []


def test_approved_record_expires_and_requires_new_confirmation():
    _, _, confirmations, clock = make_pair(ttl_seconds=60)
    record = request_echo(confirmations)
    confirmations.decide(UUID(record["pending_id"]), True)
    clock[0] = BASE + timedelta(seconds=61)
    # 过期批准不执行，也不堵塞：返回 none，调用方需要重新排队确认。
    assert confirmations.check_and_consume(
        "mcp__fake__echo", {"message": "hi"}, logical_task_id="A") == "none"
    fresh = request_echo(confirmations)
    assert fresh["status"] == "pending"
    assert fresh["pending_id"] != record["pending_id"]


def test_expired_pending_cannot_be_decided_and_is_replaced():
    _, _, confirmations, clock = make_pair(ttl_seconds=60)
    record = request_echo(confirmations)
    clock[0] = BASE + timedelta(seconds=61)
    assert confirmations.pending() == []
    with pytest.raises(PermissionError):
        confirmations.decide(UUID(record["pending_id"]), True)
    fresh = request_echo(confirmations)
    assert fresh["pending_id"] != record["pending_id"]


def test_pending_count_is_capped():
    _, _, confirmations, _ = make_pair()
    for index in range(MAX_PENDING):
        request_echo(confirmations, message=f"m{index}")
    with pytest.raises(ConfirmationBacklogError):
        request_echo(confirmations, message="overflow")


def test_finished_records_are_pruned_beyond_capacity():
    _, _, confirmations, _ = make_pair()
    for index in range(MAX_RECORDS):
        record = request_echo(confirmations, message=f"m{index}")
        confirmations.decide(UUID(record["pending_id"]), True)
        assert confirmations.check_and_consume(
            "mcp__fake__echo", {"message": f"m{index}"},
            logical_task_id="A") == "approved"
    request_echo(confirmations, message="overflow")
    records = confirmations._records()
    assert len(records) == MAX_RECORDS
    statuses = [record["status"] for record in records.values()]
    assert statuses.count("pending") == 1
    assert statuses.count("consumed") == MAX_RECORDS - 1