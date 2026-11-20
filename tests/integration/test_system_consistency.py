"""系统检查新增反例；只用合成SQLite和本地验收文件。"""
from datetime import datetime, timezone
import json
import sqlite3

import pytest

from min_agent.application import HarnessApplication
from min_agent.long_memory.delivery import EvidenceSession
from min_agent.long_memory.models import Query
from min_agent.long_memory.store import LongMemory
from min_agent.contracts.models import Message
from min_agent.session.store import SessionStore
from min_agent.session.storage import SQLiteStorage
from tests.integration.test_long_memory_runtime import seed


@pytest.fixture
def memory_pair(tmp_path):
    app = HarnessApplication(tmp_path / 'case.sqlite3', memory_mode='off')
    writer = app.context(app.create_session()['session_id'])
    seed(app, writer)
    item = app.memory.remember(writer, source_index=0)
    reader = app.context(app.create_session()['session_id'])
    storage = SQLiteStorage.open(tmp_path / 'case.sqlite3')
    memory = LongMemory(SessionStore(storage=storage))
    try:
        yield app, writer, reader, item, memory, storage
    finally:
        storage.close()
        app.close()


def test_cross_store_revoked_memory_is_not_returned_from_cache(memory_pair):
    app, writer, reader, item, memory, storage = memory_pair
    assert item.id in memory.document(reader).memories
    app.memory.delete(writer, item.id)
    assert item.id not in memory.document(reader).memories


def test_prepared_memory_view_rechecks_durable_revocation_before_send(memory_pair):
    app, writer, reader, item, memory, storage = memory_pair
    evidence = EvidenceSession(memory, reader, Query(text='Python', as_of=datetime.now(timezone.utc)), 4096)
    evidence.discovered_ids.add(item.id)
    evidence.read([item.id])
    view = evidence.build([])
    assert any(v['source'] == item.id for v in view['evidence'])
    app.memory.delete(writer, item.id)
    with pytest.raises((PermissionError, RuntimeError)):
        evidence.before_send([Message(role='user', content=json.dumps({'untrusted_user_memory': view}))])


def test_memory_authority_unavailable_does_not_fall_back_to_warm_cache(memory_pair):
    app, writer, reader, item, memory, storage = memory_pair
    assert item.id in memory.document(reader).memories
    storage.close()
    with pytest.raises((OSError, RuntimeError, sqlite3.Error)):
        memory.document(reader)


def test_cross_store_memory_updates_merge_authoritative_document(memory_pair):
    app, writer, reader, item, memory, storage = memory_pair
    first = app.memory.register_topic(writer, 'writer-topic')
    second = memory.register_topic(reader, 'reader-topic')
    assert {first.id, second.id} <= app.memory.document(writer).topics.keys()
    assert {first.id, second.id} <= memory.document(reader).topics.keys()


def test_empty_authoritative_owner_document_is_distinct_from_storage_failure(tmp_path):
    app = HarnessApplication(tmp_path / 'empty.sqlite3', memory_mode='off')
    try:
        context = app.context(app.create_session()['session_id'])
        assert not app.memory.document(context).memories
        app.storage.close()
        with pytest.raises((OSError, RuntimeError, sqlite3.Error)):
            app.memory.document(context)
    finally:
        app.close()


def test_memory_owner_cannot_be_forged_by_changing_context(memory_pair):
    from uuid import uuid4
    app, writer, reader, item, memory, storage = memory_pair
    other = reader.model_copy(update={'owner_id': uuid4()})
    with pytest.raises(PermissionError):
        memory.document(other)
