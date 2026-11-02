"""基础设施操作身份；不按标题合并用户的独立新增请求。"""

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import json

operation_id = ContextVar("agent_harness_operation_id", default=None)
authorized_action = ContextVar("agent_harness_authorized_action", default=None)
operation_phase = ContextVar("agent_harness_operation_phase", default=None)


def mark_business_started(identity):
    """可信 guard 在让出到真实业务前标记阶段；此后不再具备业务前证据。"""
    phase = operation_phase.get()
    if phase is None or phase.get("identity") != identity:
        return
    phase["business_started"] = True


def mark_business_not_started(identity, code):
    """只有受审计适配器本次调用持有的资格才可建立业务前证据。"""
    phase = operation_phase.get()
    if phase is None or phase.get("identity") != identity:
        return
    if phase.get("business_started"):
        return
    if phase.get("cleanup_authority") is None:
        return
    phase["denial"] = code


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
    if str(snapshot.work_state.confirmed_goal_id) != record["proposal_id"]:
        raise PermissionError("动作确认已变化")
    grant = snapshot.work_state.grant
    if (grant is None or str(grant.grant_id) != record.get("grant_id")
            or grant.status != "bound"):
        raise PermissionError("动作授权已变化")
    if str(grant.logical_task_id) != record.get("logical_task_id"):
        raise PermissionError("动作逻辑任务已变化")
    budget_id = str(snapshot.active_budget_id) if snapshot.active_budget_id is not None else None
    if grant.budget_id != snapshot.active_budget_id or record.get("budget_id") != budget_id:
        raise PermissionError("动作预算已变化")
    if "task_id" in record:
        task_id = str(snapshot.task.task_id) if snapshot.task else None
        attempt = task_id if snapshot.task and snapshot.task.status == "running" else None
        if (record["task_id"], record["attempt_id"], record["authorization_revision"]) != (task_id, attempt, snapshot.revision):
            raise PermissionError("动作执行代次或授权版本已变化")
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


def _cleanup_fresh_unknown(sessions, context, identity, record):
    """只清理本次新建、仍保持原样且权威状态无 receipt 的 UNKNOWN 标记。

    任何读取/CAS/写入异常都保留记录并 fail closed；此函数不重试业务。
    """
    try:
        sessions.assert_current(context)
        journal = sessions.extension(context, "operations", {})
        if journal.get(identity) != record:
            return
        receipts = sessions.extension(context, "action_receipts", {})
        if any(isinstance(item, dict) and item.get("request_id") == identity
               for item in receipts.values()):
            return
        journal.pop(identity, None)
        sessions.set_extension(context, "operations", journal)
    except Exception:
        # 清理失败或无法核实：保留 UNKNOWN，不把异常当成“无 receipt”。
        return


def execute_once(sessions, context, call, identity, handler, *, cleanup_authority=None):
    signature = json.dumps([call.name, call.arguments], sort_keys=True, ensure_ascii=False)
    with sessions.control(context):
        journal = sessions.extension(context, "operations", {})
        previous = journal.get(identity)
        if previous is not None:
            if previous["signature"] != signature:
                raise ValueError("同一操作ID不能修改参数")
            current = sessions.snapshot(context)
            current_proposal = (str(current.work_state.confirmed_goal_id)
                                if current.work_state.confirmed_goal_id is not None else None)
            if previous.get("proposal_id") is not None and previous.get("proposal_id") != current_proposal:
                raise ValueError("同一操作ID不能跨确认实例重放")
            if previous["status"] == "returned":
                return previous["outcome"]
            receipts = sessions.extension(context, "action_receipts", {})
            committed = next((item for item in receipts.values()
                              if isinstance(item, dict) and item.get("request_id") == identity), None)
            if committed is not None:
                if committed.get("result") is None:
                    raise RuntimeError("动作已提交，需核对结果；不得消费另一个实例")
                return {"ok": True, "result": committed["result"]}
            raise RuntimeError("写入结果未知，必须核对而非重试")
        snapshot = sessions.snapshot(context)
        task = snapshot.task
        grant = snapshot.work_state.grant
        if task is None or task.status != "running" or grant is None:
            # 不创建 UNKNOWN；也不得继承外层的清理资格。
            phase = {
                "identity": identity, "record": None,
                "business_started": False, "denial": None,
                "cleanup_authority": None,
            }
            phase_token = operation_phase.set(phase)
            try:
                with operation_scope(identity):
                    return handler(call)
            finally:
                operation_phase.reset(phase_token)
        record = {
            "signature": signature, "status": "unknown",
            "proposal_id": (str(snapshot.work_state.confirmed_goal_id)
                            if snapshot.work_state.confirmed_goal_id is not None else None),
            "grant_id": str(grant.grant_id),
            "logical_task_id": str(task.logical_task_id) if task.logical_task_id is not None else None,
            "attempt_id": str(task.task_id),
            "budget_id": str(snapshot.active_budget_id) if snapshot.active_budget_id is not None else None,
        }
        journal[identity] = record
        sessions.set_extension(context, "operations", journal)
        from min_agent.task_state import AuthorizationRequired
        phase = {
            "identity": identity, "record": record,
            "business_started": False, "denial": None,
            "cleanup_authority": cleanup_authority,
        }
        phase_token = operation_phase.set(phase)
        try:
            with operation_scope(identity):
                outcome = handler(call)
        except AuthorizationRequired:
            # 仅当本次受审计调用持有资格、guard 业务前拒绝仍有效、阶段未前进，
            # 才尝试清理本次新建的 UNKNOWN；自定义 handler 不被清理。
            if (cleanup_authority is not None
                    and phase.get("identity") == identity
                    and phase.get("record") is record
                    and phase.get("cleanup_authority") is cleanup_authority
                    and phase.get("denial") is not None
                    and not phase.get("business_started")):
                _cleanup_fresh_unknown(sessions, context, identity, record)
            raise
        finally:
            operation_phase.reset(phase_token)
        journal = sessions.extension(context, "operations", {})
        journal[identity] = {**record, "status": "returned", "outcome": outcome}
        sessions.set_extension(context, "operations", journal)
        return outcome
