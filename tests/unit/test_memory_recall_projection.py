from min_agent.memory import MemoryJournal
from min_agent.models import Message
from min_agent.sessions import SessionStore


def test_recall_is_explicit_preview_with_full_hash_checked_source_still_readable():
    from uuid import uuid4
    sessions = SessionStore()
    context = sessions.create(uuid4())
    task = sessions.begin(context)
    original = '周报：' + 'background ' * 220 + '不要删除原文'
    sessions.append(context, task.task_id, [Message(role='user', content=original)])
    sessions.finish(context, task.task_id, status='completed')
    journal = MemoryJournal(sessions, context)
    before = journal.select('周报')
    result = journal.recall('周报')
    assert len(result[0]['events'][0]['text']) <= 96
    assert result[0]['events'][0]['truncated'] is True
    assert 'summary' not in result[0] and 'messages' not in result[0]
    assert result[0]['events'][0]['verified'] is False
    assert journal.read_source(result[0]['events'][0]['source'])['text'] == original
    assert before == journal.select('周报')
