from uuid import uuid4

import pytest

from min_agent.sessions import SessionStore
from min_agent.storage import SQLiteStorage, _SESSION_COMMIT
from min_agent.tools.todo import TodoService


def test_outer_rollback_does_not_publish_inner_revision(tmp_path):
    with SQLiteStorage.create_new(tmp_path/'synthetic.sqlite3') as storage:
        store=SessionStore(storage=storage); context=store.create(uuid4())
        candidate=store.snapshot(context); before=candidate.revision
        candidate.extensions['synthetic']='not-committed'
        with pytest.raises(RuntimeError):
            with storage._transaction(write=True):
                storage.save(context,candidate,[],_session_commit=_SESSION_COMMIT)
                raise RuntimeError('synthetic abort')
        assert storage.load(context).snapshot.revision==before
        assert candidate.revision==before


def test_outer_rollback_does_not_publish_batch_revisions(tmp_path):
    with SQLiteStorage.create_new(tmp_path/'synthetic.sqlite3') as storage:
        store=SessionStore(storage=storage); a=store.create(uuid4()); b=store.create(uuid4())
        first=store.snapshot(a);second=store.snapshot(b)
        before=(first.revision,second.revision)
        with pytest.raises(RuntimeError):
            with storage._transaction(write=True):
                storage.save_many([(a,first,[]),(b,second,[])],_session_commit=_SESSION_COMMIT)
                raise RuntimeError('synthetic abort')
        assert (first.revision,second.revision)==before
        assert (storage.load(a).snapshot.revision,storage.load(b).snapshot.revision)==before


def test_external_transaction_cannot_publish_uncommitted_session_cache(tmp_path):
    with SQLiteStorage.create_new(tmp_path/'synthetic.sqlite3') as storage:
        store=SessionStore(storage=storage); context=store.create(uuid4())
        with storage._transaction(write=True):
            with pytest.raises(RuntimeError):
                store.set_extension(context,'synthetic','must-not-publish')
        assert 'synthetic' not in store.snapshot(context).extensions
        assert 'synthetic' not in storage.load(context).snapshot.extensions


def test_external_transaction_cannot_publish_uncommitted_todo_cache(tmp_path):
    with SQLiteStorage.create_new(tmp_path/'synthetic.sqlite3') as storage:
        store=SessionStore(storage=storage); context=store.create(uuid4()); todos=TodoService(store)
        with storage._transaction(write=True):
            with pytest.raises(RuntimeError):
                todos.add(context,'synthetic')
        assert todos.list_items(context)==[]
        assert storage.load(context).todos==[]
