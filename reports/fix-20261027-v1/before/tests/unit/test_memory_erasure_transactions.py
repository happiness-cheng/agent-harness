"""删除的失败回退及SQLite批量事务，不使用真实私人数据。"""
import pytest
from uuid import uuid4

from min_agent.sessions import SessionStore,ExecutionContext
from min_agent.storage import SQLiteStorage


def test_batch_save_rolls_back_first_snapshot_if_second_owner_is_invalid(tmp_path):
    with SQLiteStorage.create_new(tmp_path/'atomic.sqlite3') as storage:
        store=SessionStore(storage=storage)
        first=store.create(uuid4());second=store.create(uuid4())
        changed=store.snapshot(first);changed.extensions['private-test']='must-rollback'
        wrong=ExecutionContext(owner_id=uuid4(),session_id=second.session_id)
        with pytest.raises(PermissionError):
            storage.save_many([(first,changed,[]),(wrong,store.snapshot(second),[])])
        assert 'private-test' not in storage.load(first).snapshot.extensions


def test_nested_transaction_failure_preserves_outer_atomicity(tmp_path):
    with SQLiteStorage.create_new(tmp_path/'nested.sqlite3') as storage:
        store=SessionStore(storage=storage);context=store.create(uuid4())
        candidate=store.snapshot(context);candidate.extensions['flag']='not-committed'
        with pytest.raises(RuntimeError):
            with storage._transaction(write=True):
                storage.save(context,candidate,[])
                raise RuntimeError('synthetic abort')
        assert 'flag' not in storage.load(context).snapshot.extensions
