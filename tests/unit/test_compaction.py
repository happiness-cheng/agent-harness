import json
from uuid import uuid4
import pytest

from min_agent.compaction import Compactor, CompactionBlocked
from min_agent.budget import BudgetLedger, BudgetLimits
from min_agent.models import Message, ModelResponse
from min_agent.sessions import SessionStore
from tests.fakes.model import ScriptedModel


def fixture(model):
    store = SessionStore(); context = store.create(uuid4())
    ledger = BudgetLedger(BudgetLimits(model_calls=5, tool_calls=0))
    return Compactor(model, store, context, ledger.start_segment(BudgetLimits(model_calls=5, tool_calls=0))), store, context, ledger


def state():
    return {"version": 1, "projections": {}, "summary": None, "covered": 0, "covered_hashes": []}


def test_valid_compaction_preserves_dates_negation_and_spends_shared_budget():
    summary = {"selections": [{"unit_id": 0, "category": "facts"}, {"unit_id": 1, "category": "constraints"}]}
    compactor, store, context, ledger = fixture(ScriptedModel([ModelResponse(finish_reason="stop", content=json.dumps(summary, ensure_ascii=False))]))
    messages = [Message(role="user", content="2026-10-22 不删除原文"), Message(role="assistant", content="只读；仍需查反例")]
    original = state(); new = compactor.compact_history(original, messages, ["first", "second"], 2)
    assert new["covered"] == 2 and new["version"] == 2 and original == state()
    assert "不删除" in new["summary"]["facts"][0]
    assert ledger.usage.by_purpose["compaction"].model_calls == 1
    assert store.extension(context, "memory_journal", {})


def test_two_failed_compactions_open_persistent_circuit_without_more_calls():
    model = ScriptedModel([ModelResponse(finish_reason="stop", content="invalid")] * 2)
    compactor, store, context, ledger = fixture(model)
    messages = [Message(role="user", content="不能丢失日期2026-10-22")]
    for _ in range(3):
        with pytest.raises(CompactionBlocked): compactor.compact_history(state(), messages, ["hash"], 1)
    assert len(model.calls) == 2 and ledger.usage.model_calls == 2
    assert state()["summary"] is None


def test_valid_summary_that_cannot_fit_window_also_opens_circuit():
    from min_agent.artifacts import ArtifactStore
    from min_agent.context import ContextManager, ContextLimit
    summary = {"selections": [{"unit_id": index, "category": "facts"} for index in range(6)]}
    model = ScriptedModel([ModelResponse(finish_reason="stop", content=json.dumps(summary))] * 3)
    compactor, store, context, ledger = fixture(model)
    manager = ContextManager(store, context, ArtifactStore(store, context),
        window=7000, compactor=compactor)
    messages = [Message(role="user", content="".join(f"第{index}条旧事实需要保留。" for index in range(6))),
        Message(role="assistant", content="背景资料。\n" * 1000),
        Message(role="user", content="中间问题"), Message(role="assistant", content="中间回答"),
        Message(role="user", content="当前问题" * 10)]
    for _ in range(3):
        with pytest.raises(ContextLimit): manager.prepare(messages)
    assert len(model.calls) == 2
    assert store.extension(context, "context", {})["version"] == 1


def test_stopped_compaction_does_not_consume_budget_or_count_as_failed_summary():
    from min_agent.context import ContextLimit
    from min_agent.events import SessionEvents
    model=ScriptedModel([])
    compactor,store,context,ledger=fixture(model)
    events=SessionEvents(store,context);events.stop()
    compactor=Compactor(model,store,context,compactor.budget,control=events)
    with pytest.raises(ContextLimit):
        compactor.compact_history(state(),[Message(role="user",content="旧历史")],["hash"],1)
    assert ledger.usage.model_calls==0 and model.calls==[]
    assert store.extension(context,"compaction_failures",{})=={}
