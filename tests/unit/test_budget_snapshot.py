from copy import deepcopy

import pytest

from min_agent.budget import BudgetExceeded, BudgetLedger, BudgetLimits


def limits(models=3, tools=3):
    return BudgetLimits(model_calls=models, tool_calls=tools)


def test_budget_snapshot_roundtrip_preserves_segment_ids_and_parent_limits():
    ledger = BudgetLedger(limits(5))
    parent = ledger.start_segment(limits(2))
    child = parent.child(limits(5), purpose="research")
    child.consume("model_calls")
    snapshot = ledger.snapshot()
    restored = BudgetLedger.restore(type(snapshot).model_validate_json(snapshot.model_dump_json()))
    restored_child = restored.segment(child.segment_id)
    restored_child.consume("model_calls")
    with pytest.raises(BudgetExceeded):
        restored_child.consume("model_calls")
    assert restored.usage.model_calls == 2
    assert restored.segment(parent.segment_id).usage.model_calls == 2
    assert ledger.usage.model_calls == 1


@pytest.mark.parametrize("change", ["total", "purpose", "parent", "cycle", "limit", "version"])
def test_corrupt_snapshot_is_rejected_before_restore(change):
    ledger = BudgetLedger(limits())
    segment = ledger.start_segment(limits())
    segment.consume("model_calls")
    snapshot = ledger.snapshot()
    data = deepcopy(snapshot.model_dump())
    node = data["segments"][segment.segment_id]
    if change == "total":
        data["usage"]["model_calls"] = 0
    elif change == "purpose":
        node["usage"]["by_purpose"] = {}
    elif change == "parent":
        node["parent_id"] = segment.segment_id.int.to_bytes(16, "big").hex()
    elif change == "cycle":
        node["parent_id"] = segment.segment_id
    elif change == "limit":
        node["limits"]["model_calls"] = 0
    else:
        data["format_version"] = True
    with pytest.raises(ValueError):
        BudgetLedger.restore(type(snapshot).model_validate(data))


def test_persistence_callback_failure_restores_live_counters():
    seen = []

    def persist(snapshot):
        if snapshot.usage.model_calls:
            raise OSError("synthetic-storage-error")
        seen.append(snapshot)

    ledger = BudgetLedger(limits(), on_change=persist)
    segment = ledger.start_segment(limits())
    before = ledger.snapshot()
    from min_agent.budget import BudgetPersistenceError

    with pytest.raises(BudgetPersistenceError):
        segment.consume("model_calls")
    assert ledger.snapshot() == before
    assert segment.usage.model_calls == 0 and len(seen) == 1


def test_failed_segment_creation_is_not_published_or_left_in_snapshot():
    def persist(_):
        raise OSError("synthetic-storage-error")

    from min_agent.budget import BudgetPersistenceError

    ledger = BudgetLedger(limits(), on_change=persist)
    with pytest.raises(BudgetPersistenceError):
        ledger.start_segment(limits())
    assert ledger.snapshot().segments == {}


def test_ambiguous_commit_failure_blocks_handle_until_authoritative_reload():
    committed = []

    def persist(snapshot):
        if snapshot.usage.model_calls:
            committed.append(snapshot)
            if len(committed) == 1:
                raise OSError("synthetic-commit-ack-lost")

    from min_agent.budget import BudgetPersistenceError

    ledger = BudgetLedger(limits(), on_change=persist)
    segment = ledger.start_segment(limits())
    with pytest.raises(BudgetPersistenceError):
        segment.consume("model_calls")
    with pytest.raises(BudgetPersistenceError):
        segment.consume("model_calls")
    with pytest.raises(BudgetPersistenceError):
        ledger.start_segment(limits())
    assert len(committed) == 1
    restored = BudgetLedger.restore(committed[0])
    assert restored.usage.model_calls == 1
