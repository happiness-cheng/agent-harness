"""针对真实应用及 SQLite 的并发和准入回归；全部使用隔离合成数据。"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest

from min_agent.application import HarnessApplication
from min_agent.models import ToolCall
from min_agent.sessions import ExecutionContext
from min_agent.storage import VersionConflict
from min_agent.task_state import ActionSpec, GoalSpec, TaskController
from min_agent.tools.todo import TodoService
from min_agent.tools.todo_tools import todo_registry


def goal():
    action = ActionSpec(operation='todo.add', parameters={'title':'synthetic', 'due_date':None})
    return GoalSpec(objective='synthetic', completion_criteria=('synthetic',), actions=(action,))


def approve(app, sid):
    context=app.context(sid); controller=TaskController(app.sessions)
    proposal=controller.propose(context,goal()); controller.confirm(context,proposal.proposal_id)
    controller.select_mode(context,'build')
    return proposal


def test_two_writers_same_revision_have_exactly_one_commit(tmp_path):
    path=tmp_path/'synthetic.sqlite3'; a=HarnessApplication(path,memory_mode='off')
    sid=a.create_session()['session_id']; b=HarnessApplication(path,owner_id=a.owner_id,memory_mode='off')
    start=Barrier(2)
    def write(app, key):
        ctx=app.context(sid);start.wait(timeout=5)
        try: app.sessions.set_extension(ctx,key,True);return 'committed'
        except VersionConflict: return 'conflict'
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(write,a,'a'),pool.submit(write,b,'b')]
            assert sorted(f.result(timeout=10) for f in futures)==['committed','conflict']
        stored=a.storage.load(ExecutionContext(owner_id=a.owner_id,session_id=UUID(sid))).snapshot.extensions
        assert ('a' in stored) != ('b' in stored)
    finally:b.close();a.close()


def test_stale_authorize_and_failed_reads_never_fall_back_to_cache(tmp_path):
    path=tmp_path/'synthetic.sqlite3'; a=HarnessApplication(path,memory_mode='off')
    sid=a.create_session()['session_id'];approve(a,sid)
    b=HarnessApplication(path,owner_id=a.owner_id,memory_mode='off')
    c=HarnessApplication(path,owner_id=a.owner_id,memory_mode='off')
    try:
        ca,cb,cc=a.context(sid),b.context(sid),c.context(sid)
        TaskController(a.sessions).propose(ca,goal())
        with pytest.raises(VersionConflict):TaskController(b.sessions).authorize(cb,goal().actions[0])
        c.storage.close()
        with pytest.raises(RuntimeError):TaskController(c.sessions).authorize(cc,goal().actions[0])
        with pytest.raises(VersionConflict):TaskController(c.sessions).authorize(cc,goal().actions[0])
    finally:c.close();b.close();a.close()


def test_committed_tool_first_remains_after_later_proposal(tmp_path):
    path=tmp_path/'synthetic.sqlite3'; a=HarnessApplication(path,memory_mode='off')
    sid=a.create_session()['session_id'];approve(a,sid)
    b=HarnessApplication(path,owner_id=a.owner_id,memory_mode='off')
    try:
        ca,cb=a.context(sid),b.context(sid)
        a.sessions.begin(ca)
        result=todo_registry(TodoService(a.sessions),TaskController(a.sessions),ca).execute(
            ToolCall(id=str(uuid4()),name='todo_add',arguments={'title':'synthetic','due_date':None}))
        assert result['ok'] is True
        stored=a.storage.load(ca); receipt=next(iter(stored.snapshot.extensions['action_receipts'].values()))
        assert receipt['task_id']==str(stored.snapshot.task.task_id)==receipt['attempt_id']
        assert len(stored.todos)==1
        with pytest.raises(VersionConflict):TaskController(b.sessions).propose(cb,goal())
        fresh=HarnessApplication(path,owner_id=a.owner_id,memory_mode='off')
        try:
            current=fresh.context(sid);TaskController(fresh.sessions).propose(current,goal())
            final=fresh.storage.load(current)
            assert len(final.todos)==1 and final.snapshot.work_state.confirmed_goal_id is None
        finally:fresh.close()
    finally:b.close();a.close()


def test_direct_save_cannot_replace_authorization_even_with_relabelled_revision(tmp_path):
    app=HarnessApplication(tmp_path/'synthetic.sqlite3',memory_mode='off');sid=app.create_session()['session_id']
    try:
        approve(app,sid);ctx=app.context(sid);old=app.storage.load(ctx)
        TaskController(app.sessions).propose(ctx,goal())
        old.snapshot.revision=app.storage.load(ctx).snapshot.revision
        with pytest.raises((PermissionError,VersionConflict)):app.storage.save(ctx,old.snapshot,old.todos)
        assert app.storage.load(ctx).snapshot.work_state.confirmed_goal_id is None
    finally:app.close()
