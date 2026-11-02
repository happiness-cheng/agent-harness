"""系统检查新增反例；只用合成SQLite和本地验收文件。"""
from datetime import datetime, timezone
from hashlib import sha256
import json
import sqlite3

import pytest

from min_agent.application import HarnessApplication
from min_agent.evaluation.contracts import CheckSpec, Observation, TaskSpec, digest
from min_agent.evaluation.verifier import verify
from min_agent.long_memory.delivery import EvidenceSession
from min_agent.long_memory.models import Query
from min_agent.long_memory.store import LongMemory
from min_agent.models import Message
from min_agent.sessions import SessionStore
from min_agent.storage import SQLiteStorage
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


@pytest.mark.parametrize('mismatch', ['protocol', 'task', 'missing_protocol', 'safety'])
def test_recovery_refuses_pass_result_from_wrong_contract(tmp_path, mismatch):
    from min_agent.evaluation.night import NightJob, recover
    task = TaskSpec(id='night-case', category='runtime', prompt='合成任务',
                    checks=[CheckSpec(id='done', kind='runtime_status', expected='completed')])
    observation = Observation(run_id='r', task_id=task.id, version_id='fixture', execution_status='completed',
                              budget={'usage': {'model_calls': 0, 'tool_calls': 0}})
    result = verify(task, observation)
    changes = {'protocol': {'evaluation_protocol_sha256': 'f' * 64},
               'task': {'task_id': 'other'}, 'missing_protocol': {'evaluation_protocol_sha256': None},
               'safety': {'safety': 'FAIL'}}[mismatch]
    file = tmp_path / 'result.json'
    file.write_text(result.model_copy(update=changes).model_dump_json(), encoding='utf-8')
    job = NightJob(id='job', goal='测试恢复', task=task)
    data = {'jobs': {'job': {'spec': job.model_dump(mode='json'), 'status': 'VERIFYING', 'attempts': [
        {'outcome': {'evidence_refs': [str(file)]}, 'evidence_sha256': sha256(file.read_bytes()).hexdigest(),
         'effective_spec_sha256': digest(task)}]}}}
    recover(data)
    assert data['jobs']['job']['status'] == 'WAITING_FOR_USER'


def test_recovery_keeps_valid_current_evidence(tmp_path):
    from min_agent.evaluation.night import NightJob, recover
    task = TaskSpec(id='valid-night', category='runtime', prompt='合成任务',
                    checks=[CheckSpec(id='done', kind='runtime_status', expected='completed')])
    result = verify(task, Observation(run_id='r', task_id=task.id, version_id='fixture', execution_status='completed',
                                      budget={'usage': {'model_calls': 0, 'tool_calls': 0}}))
    file = tmp_path / 'valid.json';file.write_text(result.model_dump_json(), encoding='utf-8')
    job = NightJob(id='job', goal='合法恢复', task=task)
    data = {'jobs': {'job': {'spec': job.model_dump(mode='json'), 'status': 'VERIFYING', 'attempts': [
        {'outcome': {'evidence_refs': [str(file)]}, 'evidence_sha256': sha256(file.read_bytes()).hexdigest(),
         'effective_spec_sha256': digest(task)}]}}}
    recover(data)
    assert data['jobs']['job']['status'] == 'COMPLETED'


@pytest.mark.parametrize('valid', [False, True])
def test_production_completion_uses_same_evidence_gate_as_recovery(tmp_path, monkeypatch, valid):
    from min_agent.evaluation.night import NightJob, NightQueue, NightPolicy, NightOutcome, run_night
    import min_agent.evaluation.night_executor as module
    task = TaskSpec(id='production-case', category='runtime', prompt='合成任务',
                    checks=[CheckSpec(id='done', kind='runtime_status', expected='completed')])
    queue = NightQueue(tmp_path / 'queue.json');queue.initialize(NightPolicy())
    queue.add(NightJob(id='job', goal='核验生产验收文件', task=task))
    def adapter(job, route, out, **kwargs):
        out.mkdir(parents=True)
        result = verify(job.task, Observation(run_id='r', task_id=task.id, version_id='fixture', execution_status='completed',
                                             budget={'usage': {'model_calls': 0, 'tool_calls': 0}}))
        if not valid:
            result = result.model_copy(update={'evaluation_protocol_sha256': 'f' * 64})
        file = out / 'result.json';file.write_text(result.model_dump_json(), encoding='utf-8')
        return NightOutcome(verdict='PASS', evidence_refs=[str(file)])
    monkeypatch.setattr(module, 'execute_job', adapter)
    result = run_night(queue)
    assert result['jobs']['job']['status'] == ('COMPLETED' if valid else 'WAITING_FOR_USER')


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


def test_recovery_recomputes_verdict_instead_of_trusting_pass_labels(tmp_path):
    from min_agent.evaluation.night import NightJob, recover
    task = TaskSpec(id='false-label', category='runtime', prompt='预算违规不能通过', max_model_calls=1,
                    checks=[CheckSpec(id='done', kind='runtime_status', expected='completed')])
    result = verify(task, Observation(run_id='r', task_id=task.id, version_id='fixture', execution_status='completed',
                                      budget={'usage': {'model_calls': 2, 'tool_calls': 0}}))
    assert result.safety == 'FAIL'
    file = tmp_path / 'forged.json'
    file.write_text(result.model_copy(update={'verdict': 'PASS', 'safety': 'PASS'}).model_dump_json(), encoding='utf-8')
    job = NightJob(id='job', goal='防止只改标签', task=task)
    data = {'jobs': {'job': {'spec': job.model_dump(mode='json'), 'status': 'VERIFYING', 'attempts': [
        {'outcome': {'evidence_refs': [str(file)]}, 'evidence_sha256': sha256(file.read_bytes()).hexdigest(),
         'effective_spec_sha256': digest(task)}]}}}
    recover(data)
    assert data['jobs']['job']['status'] == 'WAITING_FOR_USER'


def test_recovery_does_not_override_a_recorded_regression_failure(tmp_path):
    from min_agent.evaluation.night import NightJob, recover
    task = TaskSpec(id='regression-failed', category='runtime', prompt='主任务通过但保留回归失败',
                    checks=[CheckSpec(id='done', kind='runtime_status', expected='completed')])
    result = verify(task, Observation(run_id='r', task_id=task.id, version_id='fixture', execution_status='completed',
                                      budget={'usage': {'model_calls': 0, 'tool_calls': 0}}))
    file = tmp_path / 'task-pass.json';file.write_text(result.model_dump_json(), encoding='utf-8')
    job = NightJob(id='job', goal='禁止恢复覆盖失败', task=task)
    data = {'jobs': {'job': {'spec': job.model_dump(mode='json'), 'status': 'VERIFYING', 'attempts': [
        {'outcome': {'verdict': 'FAIL', 'non_retryable': True, 'evidence_refs': [str(file)]},
         'evidence_sha256': sha256(file.read_bytes()).hexdigest(), 'effective_spec_sha256': digest(task)}]}}}
    recover(data)
    assert data['jobs']['job']['status'] == 'WAITING_FOR_USER'


def test_corrupt_observation_in_recovery_is_withheld_not_a_scheduler_crash(tmp_path):
    from min_agent.evaluation.night import NightJob, recover
    task = TaskSpec(id='corrupt-observation', category='runtime', prompt='损坏的费用观察不能放行',
                    checks=[CheckSpec(id='done', kind='runtime_status', expected='completed')])
    result = verify(task, Observation(run_id='r', task_id=task.id, version_id='fixture', execution_status='completed',
                                      budget={'usage': {'model_calls': 0, 'tool_calls': 0}}))
    malformed = result.observation.model_copy(update={'budget': {'usage': []}})
    file = tmp_path / 'corrupt.json'
    file.write_text(result.model_copy(update={'observation': malformed}).model_dump_json(), encoding='utf-8')
    job = NightJob(id='job', goal='损坏数据失败关闭', task=task)
    data = {'jobs': {'job': {'spec': job.model_dump(mode='json'), 'status': 'VERIFYING', 'attempts': [
        {'outcome': {'evidence_refs': [str(file)]}, 'evidence_sha256': sha256(file.read_bytes()).hexdigest(),
         'effective_spec_sha256': digest(task)}]}}}
    recover(data)
    assert data['jobs']['job']['status'] == 'WAITING_FOR_USER'
