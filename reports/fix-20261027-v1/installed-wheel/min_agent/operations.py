"""基础设施操作身份；不按标题合并用户的独立新增请求。"""

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import json

operation_id = ContextVar("agent_harness_operation_id", default=None)
authorized_action = ContextVar("agent_harness_authorized_action", default=None)


@contextmanager
def operation_scope(identity):
    token = operation_id.set(identity)
    try:
        yield
    finally:
        operation_id.reset(token)


@contextmanager
def action_scope(record):
    token = authorized_action.set(record)
    try:
        with operation_scope(record["instance_id"]):
            yield
    finally:
        authorized_action.reset(token)


def stamp_action(snapshot, family, *, items=None):
    """由业务提交入口调用，将动作消费和实际资源一起加入同一候选快照。"""
    record = authorized_action.get()
    if record is None or not record["operation"].startswith(family + "."):
        return
    if (record["owner_id"], record["session_id"]) != (str(snapshot.owner_id), str(snapshot.session_id)):
        raise PermissionError("动作实例不属于当前会话")
    receipts = snapshot.extensions.setdefault("action_receipts", {})
    if record["instance_id"] in receipts:
        if receipts[record["instance_id"]]["signature"] != record["signature"]:
            raise ValueError("动作实例签名不能改变")
        return
    receipt = {**deepcopy(record), "status": "committed", "result": None}
    if items is not None:
        item = next((value for identity, value in items.items() if str(identity) == record["resource_id"]), None)
        if item is not None:
            value = item.model_dump(mode="json") if hasattr(item, "model_dump") else deepcopy(item)
            if record["operation"] in {"todo.complete", "todo.delete", "reminder.cancel"}:
                value = {"item": value, "changed": True}
            receipt["result"] = json.dumps(value, ensure_ascii=False, allow_nan=False)
    receipts[record["instance_id"]] = receipt


def execute_once(sessions, context, call, identity, handler):
    signature = json.dumps([call.name, call.arguments], sort_keys=True, ensure_ascii=False)
    with sessions.control(context):
        journal = sessions.extension(context, "operations", {})
        previous = journal.get(identity)
        if previous is not None:
            if previous["signature"] != signature:
                raise ValueError("同一操作ID不能修改参数")
            if previous["status"] == "returned":
                return previous["outcome"]
            receipts = sessions.extension(context, "action_receipts", {})
            committed = next((item for item in receipts.values() if item["request_id"] == identity), None)
            if committed is not None:
                if committed["result"] is None:
                    raise RuntimeError("动作已提交，需核对结果；不得消费另一个实例")
                return {"ok": True, "result": committed["result"]}
            if call.name != "todo_add":
                raise RuntimeError("写入结果未知，必须核对而非重试")
        journal[identity] = {"signature": signature, "status": "unknown"}
        sessions.set_extension(context, "operations", journal)
        with operation_scope(identity):
            outcome = handler(call)
        journal = sessions.extension(context, "operations", {})
        journal[identity] = {"signature": signature, "status": "returned", "outcome": outcome}
        sessions.set_extension(context, "operations", journal)
        return outcome
