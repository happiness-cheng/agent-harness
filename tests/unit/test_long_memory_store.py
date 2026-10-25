"""P1固定契约：真实会话身份、用户确认、同一SQLite事务和证据。"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from min_agent.long_memory import LongMemory
from min_agent.models import Message
from min_agent.sessions import SessionStore
from min_agent.storage import SQLiteStorage
from min_agent.task_state import TaskController, GoalSpec


class TestClock:
    __test__ = False
    def __init__(self): self.now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    def __call__(self): return self.now


def setup_memory(storage=None):
    sessions = SessionStore(storage=storage)
    context = sessions.create(uuid4())
    controller = TaskController(sessions)
    proposal = controller.propose(context, GoalSpec(objective='确认长期记忆', completion_criteria=('审核指定候选',)))
    controller.confirm(context, proposal.proposal_id)
    controller.select_mode(context, 'build')
    task = sessions.begin(context)
    sessions.append(context, task.task_id, [Message(role='user', content='我希望解释代码时先给Python短例子。')])
    sessions.finish(context, task.task_id, status='completed')
    clock = TestClock()
    return sessions, context, clock, LongMemory(sessions, clock=clock)


def candidate(memory, context, **kwargs):
    return memory.propose(context, '先给Python短例子', source_indices=[0], **kwargs)


def commit(memory, context, item):
    review = memory.review(context, item.id)
    return memory.confirm(context, item.id, review_token=review['review_token'])


def test_pending_is_not_cross_session_memory_and_bad_confirmation_fails():
    sessions, context, clock, memory = setup_memory()
    item = candidate(memory, context)
    other = sessions.create(context.owner_id)
    assert memory.records(other, as_of=clock.now) == []
    with pytest.raises(PermissionError): memory.confirm(context, item.id, review_token='yes')
    confirmed = commit(memory, context, item)
    assert [m.id for m in memory.records(other, as_of=clock.now)] == [confirmed.id]
    assert confirmed.evidence_type == 'DIRECT'


def test_commit_is_idempotent_and_shared_quote_is_one_evidence():
    sessions, context, clock, memory = setup_memory()
    first = candidate(memory, context)
    confirmed = commit(memory, context, first)
    assert commit(memory, context, first) == confirmed
    second = memory.propose(context, '解释代码用Python', source_indices=[0])
    result = commit(memory, context, second)
    assert result.evidence_refs == confirmed.evidence_refs
    assert len(memory.document(context).evidence) == 1


def test_rejected_candidate_does_not_reappear_with_new_request_id():
    sessions, context, clock, memory = setup_memory()
    item = candidate(memory, context)
    memory.reject(context, item.id)
    assert candidate(memory, context).id == item.id
    assert memory.list_pending(context) == []


def test_owner_plan_role_and_time_boundaries_fail_closed():
    sessions, context, clock, memory = setup_memory()
    item = candidate(memory, context)
    foreign = sessions.create(uuid4())
    with pytest.raises(PermissionError): memory.review(foreign, item.id)
    with pytest.raises(PermissionError): memory.propose(foreign, 'foreign', source_indices=[0])
    sessions.set_work_state(context, sessions.work_state(context).model_copy(update={'mode': 'plan'}))
    with pytest.raises(PermissionError): commit(memory, context, item)
    assert memory.records(context, as_of=clock.now) == []


def test_confirmation_time_is_not_backdated_to_observation():
    sessions, context, clock, memory = setup_memory()
    before = clock.now
    item = candidate(memory, context)
    clock.now += timedelta(days=1)
    confirmed = commit(memory, context, item)
    assert memory.records(context, as_of=before) == []
    assert memory.records(context, as_of=clock.now)[0].id == confirmed.id


def test_expiration_rejects_confirm_and_filters_committed_memory():
    sessions, context, clock, memory = setup_memory()
    deadline = clock.now + timedelta(hours=1)
    confirmed = commit(memory, context, candidate(memory, context, valid_until=deadline))
    clock.now = deadline
    assert memory.records(context, as_of=clock.now) == []
    pending = memory.propose(context, '另一个临时要求', source_indices=[0], expires_at=clock.now+timedelta(hours=1))
    clock.now += timedelta(hours=2)
    with pytest.raises(PermissionError): commit(memory, context, pending)


def test_correction_needs_own_confirmation_and_past_query_has_old_version():
    sessions, context, clock, memory = setup_memory()
    first = commit(memory, context, candidate(memory, context))
    before = clock.now
    clock.now += timedelta(days=1)
    pending = memory.correct(context, first.id, '先给C++短例子', source_indices=[0])
    assert memory.records(context, as_of=clock.now)[0].id == first.id
    new = commit(memory, context, pending)
    assert [m.id for m in memory.records(context, as_of=clock.now)] == [new.id]
    assert [m.id for m in memory.records(context, as_of=before)] == [first.id]


def test_delete_erases_module_owned_copies_and_cannot_resurrect():
    sessions, context, clock, memory = setup_memory()
    pending = candidate(memory, context)
    item = commit(memory, context, pending)
    memory.delete(context, item.id)
    doc = memory.document(context).model_dump_json()
    assert '先给Python短例子' not in doc
    assert not memory.document(context).evidence
    assert not memory.records(context, as_of=clock.now)
    with pytest.raises(PermissionError): commit(memory, context, pending)


def test_existing_sqlite_schema_unchanged_and_internal_snapshot_hidden(tmp_path):
    path = tmp_path / 'memory.sqlite3'
    with SQLiteStorage.create_new(path) as storage:
        sessions, context, clock, memory = setup_memory(storage)
        item = commit(memory, context, candidate(memory, context))
        assert sessions.contexts(context.owner_id) == [context]
    with SQLiteStorage.open(path) as storage:
        restored = SessionStore(storage=storage)
        other = restored.create(context.owner_id)
        assert LongMemory(restored, clock=clock).records(other, as_of=clock.now)[0].id == item.id
    import sqlite3
    with sqlite3.connect(path) as con:
        assert {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")} == {'sessions','todos'}
