"""冻结的 B0 安全行为；仅使用合成 SQLite，不断言旧缺陷存在。"""
from uuid import uuid4
import pytest

from min_agent.application import HarnessApplication
from min_agent.models import ToolCall
from min_agent.task_state import ActionSpec, GoalSpec, TaskController
from min_agent.tools.todo import TodoService
from min_agent.tools.todo_tools import todo_registry


def goal():
    return GoalSpec(objective='synthetic', completion_criteria=('synthetic',),
                    actions=(ActionSpec(operation='todo.add', parameters={'title':'synthetic', 'due_date':None}),))


def confirmed(app, sid):
    context=app.context(sid)
    controller=TaskController(app.sessions)
    proposal=controller.propose(context, goal())
    controller.confirm(context, proposal.proposal_id)
    controller.select_mode(context, 'build')
    return proposal


def test_stale_extension_never_overwrites_newer_extension(tmp_path):
    path=tmp_path/'test.sqlite3'; a=HarnessApplication(path, memory_mode='off')
    sid=a.create_session()['session_id']; b=HarnessApplication(path, owner_id=a.owner_id, memory_mode='off')
    try:
        ca,cb=a.context(sid),b.context(sid)
        a.sessions.set_extension(ca,'new_value',{'version':1})
        with pytest.raises(Exception): b.sessions.set_extension(cb,'stale',True)
        assert a.storage.load(ca).snapshot.extensions['new_value']=={'version':1}
    finally: b.close();a.close()


def test_stale_write_cannot_erase_new_confirmation(tmp_path):
    path=tmp_path/'test.sqlite3';a=HarnessApplication(path,memory_mode='off')
    sid=a.create_session()['session_id'];b=HarnessApplication(path,owner_id=a.owner_id,memory_mode='off')
    try:
        ca,cb=a.context(sid),b.context(sid);proposal=confirmed(a,sid)
        with pytest.raises(Exception): b.sessions.set_extension(cb,'stale',True)
        assert a.storage.load(ca).snapshot.work_state.confirmed_goal_id==proposal.proposal_id
    finally:b.close();a.close()


def test_superseded_confirmation_and_real_todo_stay_rejected(tmp_path):
    path=tmp_path/'test.sqlite3';a=HarnessApplication(path,memory_mode='off');sid=a.create_session()['session_id']
    original=confirmed(a,sid);b=HarnessApplication(path,owner_id=a.owner_id,memory_mode='off')
    try:
        ca=a.context(sid);TaskController(a.sessions).propose(ca,goal())
        with pytest.raises(Exception):b.sessions.set_extension(b.context(sid),'stale',True)
        reopened=HarnessApplication(path,owner_id=a.owner_id,memory_mode='off')
        try:
            context=reopened.context(sid)
            assert reopened.storage.load(context).snapshot.work_state.confirmed_goal_id is None
            registry=todo_registry(TodoService(reopened.sessions),TaskController(reopened.sessions),context)
            with pytest.raises(Exception):
                registry.execute(ToolCall(id=str(uuid4()),name='todo_add',arguments={'title':'synthetic','due_date':None}))
            assert reopened.storage.load(context).todos==[]
        finally:reopened.close()
    finally:b.close();a.close()


def test_revocation_after_guard_before_side_effect_does_not_commit(tmp_path):
    path=tmp_path/'test.sqlite3';a=HarnessApplication(path,memory_mode='off');sid=a.create_session()['session_id']
    confirmed(a,sid)
    try:
        ca=a.context(sid)
        action=ActionSpec(operation='todo.add',parameters={'title':'synthetic','due_date':None})
        task=a.sessions.begin(ca)
        with pytest.raises(Exception):
            with TaskController(a.sessions).guard(ca,action):
                TaskController(a.sessions).propose(ca,goal())
                TodoService(a.sessions).add(ca,'synthetic')
        a.sessions.finish(ca,task.task_id,status='failed')
        assert a.storage.load(ca).todos==[]
        assert a.storage.load(ca).snapshot.work_state.confirmed_goal_id is None
    finally:a.close()


def test_current_confirmation_allows_normal_real_todo(tmp_path):
    app=HarnessApplication(tmp_path/'test.sqlite3',memory_mode='off');sid=app.create_session()['session_id']
    try:
        confirmed(app,sid);context=app.context(sid)
        task=app.sessions.begin(context)
        registry=todo_registry(TodoService(app.sessions),TaskController(app.sessions),context)
        result=registry.execute(ToolCall(id=str(uuid4()),name='todo_add',arguments={'title':'synthetic','due_date':None}))
        assert result['ok'] is True
        app.sessions.finish(context,task.task_id,status='completed')
        assert len(app.storage.load(context).todos)==1
    finally:app.close()
