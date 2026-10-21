from uuid import uuid4

import pytest

from min_agent.budget import BudgetLimits
from min_agent.budget_persistence import SessionBudgets
from min_agent.checkpoints import CheckpointPolicy
from min_agent.models import ModelResponse
from min_agent.runtime import Runtime
from min_agent.sessions import SessionStore
from min_agent.storage import SQLiteStorage
from tests.fakes.model import ScriptedModel
from tests.unit.test_checkpoint_resume import note_response


def test_restart_requires_directed_checkpoint_and_fresh_segment(tmp_path):
    path = tmp_path / "checkpoint.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        store = SessionStore(storage=storage)
        context = store.create(uuid4())
        budgets = SessionBudgets(store)
        budget_id = budgets.create(context, BudgetLimits(model_calls=4, tool_calls=0))
        segment = budgets.open(context, budget_id).start_segment(BudgetLimits(model_calls=1, tool_calls=0))
        result = Runtime(ScriptedModel([note_response()]), budget=segment,
                         checkpoint_policy=CheckpointPolicy()).run_session("长任务", store=store, context=context)
        checkpoint_id = result.checkpoint.checkpoint_id
        assert store.snapshot(context).checkpoint.checkpoint.checkpoint_id == checkpoint_id
    with SQLiteStorage.open(path) as storage:
        store = SessionStore(storage=storage)
        ledger = SessionBudgets(store).open(context, budget_id)
        model = ScriptedModel([ModelResponse(finish_reason="stop", content="依据原结果继续完成")])
        segment = ledger.start_segment(BudgetLimits(model_calls=2, tool_calls=0))
        runtime = Runtime(model, budget=segment)
        with pytest.raises(PermissionError):
            runtime.run_session("继续", store=store, context=context)
        with pytest.raises(PermissionError):
            runtime.resume_session(uuid4(), store=store, context=context)
        assert model.calls == []
        assert runtime.resume_session(checkpoint_id, store=store, context=context).status == "completed"
        assert ledger.usage.model_calls == 2
        with pytest.raises(PermissionError):
            runtime.resume_session(checkpoint_id, store=store, context=context)
        assert len(model.calls) == 1


def test_cannot_resume_using_original_segment(tmp_path):
    with SQLiteStorage.create_new(tmp_path / "old.sqlite3") as storage:
        store = SessionStore(storage=storage)
        context = store.create(uuid4())
        budgets = SessionBudgets(store)
        budget_id = budgets.create(context, BudgetLimits(model_calls=5, tool_calls=0))
        segment = budgets.open(context, budget_id).start_segment(BudgetLimits(model_calls=2, tool_calls=0))
        segment.consume("model_calls")
        runtime = Runtime(ScriptedModel([note_response()]), budget=segment, checkpoint_policy=CheckpointPolicy())
        cp = runtime.run_session("任务", store=store, context=context).checkpoint
        with pytest.raises(PermissionError):
            runtime.resume_session(cp.checkpoint_id, store=store, context=context)
