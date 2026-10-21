import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from min_agent.models import ModelResponse, ToolCall
from min_agent.runtime import Runtime
from min_agent.sessions import SessionStore
from min_agent.storage import SQLiteStorage
from min_agent.task_state import ActionSpec, AuthorizationRequired, GoalSpec, TaskController
from min_agent.tools.todo import TodoService
from min_agent.tools.todo_tools import todo_registry
from tests.fakes.model import ScriptedModel


ACTION = ActionSpec(operation="todo.add", parameters={"title":"恰好一条", "due_date":None})


def confirm(controller, context, count=1):
    proposal = controller.propose(context, GoalSpec(objective="按确认次数记录",
        completion_criteria=("记录数量符合确认",), actions=(ACTION,) * count))
    controller.select_mode(context, "build")
    controller.confirm(context, proposal.proposal_id)
    return proposal


def call(identity):
    return ToolCall(id=identity, name="todo_add", arguments={"title":"恰好一条"})


def run_calls(store, context, todos, controller, count=2):
    model = ScriptedModel([ModelResponse(finish_reason="tool_calls",
        tool_calls=[call(str(index)) for index in range(count)]),
        ModelResponse(finish_reason="stop", content="按实际结果核对")])
    return Runtime(model, todo_registry(todos, controller, context)).run_session(
        "执行已确认范围", store=store, context=context)


def test_one_confirmed_instance_cannot_be_reused_by_a_different_call_id():
    store=SessionStore();context=store.create(uuid4());todos=TodoService(store);controller=TaskController(store)
    confirm(controller,context)
    result=run_calls(store,context,todos,controller)
    assert len(todos.list_items(context))==1
    assert result.status=="waiting_user" and result.error_code=="action_already_consumed"
    assert not store._pending(store.snapshot(context).messages)


def test_two_explicit_same_content_instances_create_two_distinct_records():
    store=SessionStore();context=store.create(uuid4());todos=TodoService(store);controller=TaskController(store)
    confirm(controller,context,2)
    assert run_calls(store,context,todos,controller).status=="completed"
    assert len({item.id for item in todos.list_items(context)})==2


def test_reconfirming_same_proposal_does_not_refill_consumed_instances():
    store=SessionStore();context=store.create(uuid4());todos=TodoService(store);controller=TaskController(store)
    proposal=confirm(controller,context);registry=todo_registry(todos,controller,context)
    registry.execute(call("first"))
    controller.confirm(context,proposal.proposal_id)
    with pytest.raises(AuthorizationRequired):registry.execute(call("second"))
    assert len(todos.list_items(context))==1
    confirm(controller,context)
    registry.execute(call("independent-confirmation"))
    assert len(todos.list_items(context))==2


def test_consumption_survives_restart_and_changed_call_id(tmp_path):
    path=tmp_path/"actions.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        store=SessionStore(storage=storage);context=store.create(uuid4());controller=TaskController(store)
        confirm(controller,context)
        todo_registry(TodoService(store),controller,context).execute(call("before"),operation_id="model-before")
    with SQLiteStorage.open(path) as storage:
        store=SessionStore(storage=storage);controller=TaskController(store);todos=TodoService(store)
        with pytest.raises(AuthorizationRequired):
            todo_registry(todos,controller,context).execute(call("after"),operation_id="model-after")
        assert len(todos.list_items(context))==1


def test_sqlite_failure_rolls_back_business_and_authorization_together(tmp_path):
    with SQLiteStorage.create_new(tmp_path/"rollback.sqlite3") as storage:
        store=SessionStore(storage=storage);context=store.create(uuid4());controller=TaskController(store)
        confirm(controller,context);todos=TodoService(store)
        storage._connection.execute("CREATE TRIGGER reject_new_todo BEFORE INSERT ON todos "
                                    "BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
        with pytest.raises(sqlite3.IntegrityError):
            todo_registry(todos,controller,context).execute(call("failed"),operation_id="failed-model-call")
        assert todos.list_items(context)==[]
        assert controller.authorize(context,ACTION).allowed
        saved=storage.load(context).snapshot
        assert saved.extensions.get("action_receipts",{})=={}
        assert store.extension(context,"action_receipts",{})=={}


def test_lost_response_after_commit_cannot_reexecute_after_restart(tmp_path):
    path=tmp_path/"unknown.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        store=SessionStore(storage=storage);context=store.create(uuid4());controller=TaskController(store)
        confirm(controller,context);todos=TodoService(store);original=todos.add
        def lost(*args,**kwargs):
            original(*args,**kwargs)
            raise TimeoutError("synthetic lost response")
        todos.add=lost
        with pytest.raises(TimeoutError):
            todo_registry(todos,controller,context).execute(call("before"),operation_id="unknown-before")
    with SQLiteStorage.open(path) as storage:
        store=SessionStore(storage=storage);controller=TaskController(store);todos=TodoService(store)
        with pytest.raises(AuthorizationRequired):
            todo_registry(todos,controller,context).execute(call("new-id"),operation_id="unknown-after")
        assert len(todos.list_items(context))==1


def test_simultaneous_different_ids_cannot_consume_one_instance_twice():
    store=SessionStore();context=store.create(uuid4());todos=TodoService(store);controller=TaskController(store)
    confirm(controller,context);registry=todo_registry(todos,controller,context)
    def execute(index):
        try:return registry.execute(call(str(index)))
        except AuthorizationRequired:return None
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(execute,range(2)))
    assert sum(value is not None for value in results)==1
    assert len(todos.list_items(context))==1


def test_reminder_add_also_consumes_the_action_instance():
    from min_agent.tools.reminder import ReminderService
    from min_agent.tools.reminder_tools import register_reminders
    store=SessionStore();context=store.create(uuid4());controller=TaskController(store)
    service=ReminderService(store)
    parameters={"title":"提醒","scheduled_at":"2026-10-22T09:00:00+08:00",
                "time_zone":"Asia/Shanghai","todo_id":None}
    proposal=controller.propose(context,GoalSpec(objective="一条提醒",completion_criteria=("一条",),
        actions=(ActionSpec(operation="reminder.add",parameters=parameters),)))
    controller.select_mode(context,"build");controller.confirm(context,proposal.proposal_id)
    registry=todo_registry(TodoService(store),controller,context)
    register_reminders(registry,service,controller,context)
    registry.execute(ToolCall(id="first",name="reminder_add",arguments=parameters),operation_id="first")
    with pytest.raises(AuthorizationRequired):
        registry.execute(ToolCall(id="second",name="reminder_add",arguments=parameters),operation_id="second")
    assert len(service.list_items(context))==1


def test_unknown_same_request_recovers_receipt_instead_of_taking_second_instance(tmp_path):
    path=tmp_path/"receipt.sqlite3"
    with SQLiteStorage.create_new(path) as storage:
        store=SessionStore(storage=storage);context=store.create(uuid4());controller=TaskController(store)
        confirm(controller,context,2);todos=TodoService(store);original=todos.add
        def lost(*args,**kwargs):
            original(*args,**kwargs)
            raise TimeoutError("synthetic lost response")
        todos.add=lost
        with pytest.raises(TimeoutError):
            todo_registry(todos,controller,context).execute(call("same-request"),operation_id="same-request")
    with SQLiteStorage.open(path) as storage:
        store=SessionStore(storage=storage);todos=TodoService(store);controller=TaskController(store)
        registry=todo_registry(todos,controller,context)
        recovered=registry.execute(call("same-request"),operation_id="same-request")
        assert json.loads(recovered["result"])["id"]==str(todos.list_items(context)[0].id)
        assert len(todos.list_items(context))==1
        registry.execute(call("second-instance"),operation_id="second-instance")
        assert len(todos.list_items(context))==2


def test_legacy_confirmation_stays_readable_but_requires_new_proposal_for_writes():
    from min_agent.task_state import WorkState
    store=SessionStore();context=store.create(uuid4());controller=TaskController(store)
    proposal=confirm(controller,context)
    legacy=controller.snapshot(context).model_dump(mode="json")
    legacy["proposal"].pop("authorization_version")
    store.set_work_state(context,WorkState.model_validate_json(json.dumps(legacy)))
    controller.confirm(context,proposal.proposal_id)
    assert controller.authorize(context,ActionSpec(operation="todo.list")).allowed
    assert controller.authorize(context,ACTION).code=="legacy_confirmation_requires_new_proposal"
    confirm(controller,context)
    assert controller.authorize(context,ACTION).allowed
