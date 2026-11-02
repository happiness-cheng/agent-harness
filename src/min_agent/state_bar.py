"""只读状态投影；语义目标不授予权限，运行结束不等于业务验收通过。"""

import json

from pydantic import PrivateAttr

from min_agent.models import Message


class RuntimeContextMessage(Message):
    """仅内存中标记临时尾部；Provider仍接收标准user消息字段。"""

    _refresh = PrivateAttr(default=None)

    def refresh(self):
        if self._refresh is not None:
            self.content = self._refresh()


class StatusBuilder:
    def __init__(self, sessions, context, budget=None):
        self.sessions, self.context = sessions, sessions.check_access(context)
        self.budget = budget

    def build(self, messages):
        """不写状态、不读旧摘要；只以当前任务范围内的可信记录生成短视图。"""
        with self.sessions.control(self.context):
            snapshot = self.sessions.snapshot(self.context)
            task, work = snapshot.task, snapshot.work_state
            if task is None or task.status == "completed":
                return None
            proposal = work.proposal
            scope = snapshot.extensions.get("goal_tracking", {})
            relevant = (proposal is not None and scope.get("proposal_id") == str(proposal.proposal_id)
                        and scope.get("history_start") == task.history_start)
            receipts = snapshot.extensions.get("action_receipts", {})
            completed, pending = [], []
            if relevant:
                for index, action in enumerate(proposal.goal.actions):
                    receipt = receipts.get(proposal.instance_id(index), {})
                    committed = (receipt.get("status") == "committed"
                        and receipt.get("signature") == action.signature()
                        and receipt.get("proposal_id") == str(proposal.proposal_id))
                    row = {"index": index, "operation": action.operation}
                    if committed:
                        row["evidence"] = proposal.instance_id(index)
                    (completed if committed else pending).append(row)
            binding = self.budget.binding if self.budget is not None else None
            committed_requests = {item.get("request_id") for item in receipts.values()
                                  if item.get("status") == "committed"}
            unknown = [identity for identity, value in snapshot.extensions.get("operations", {}).items()
                if value.get("status") == "unknown" and identity not in committed_requests
                and binding is not None and identity.startswith(str(binding.budget_id) + ":")]
            last_tool = self._last_tool(messages[task.history_start:])
            failed = last_tool is not None and last_tool["ok"] is not True
            cp = snapshot.checkpoint
            resumed = cp is not None and cp.resumed_by == task.task_id
            context = snapshot.extensions.get("context", {})
            current_compacted = context.get("covered", 0) > task.history_start
            if not (relevant or unknown or failed or resumed or current_compacted
                    or (work.proposal is None and work.status == "waiting_user")):
                return None
            blocked = (work.waiting_reason if relevant or work.proposal is None else None)
            if unknown:
                blocked = "operation_outcome_unknown"
            elif failed:
                blocked = last_tool["error_code"]
            execution = {"mode": work.mode, "blocked_reason": blocked,
                         "context_version": context.get("version", 1)}
            if self.budget is not None:
                remaining = self.budget.remaining
                execution.update(remaining_model_calls=remaining.model_calls,
                                 remaining_tool_calls=remaining.tool_calls)
            if last_tool is not None:
                execution["last_tool"] = last_tool
            if unknown:
                execution["unknown_operations"] = unknown
            if resumed:
                execution["checkpoint"] = {"id": str(cp.checkpoint.checkpoint_id), "resumed": True}
            plan_writes = work.mode == "plan" and any(not row["operation"].endswith(".list") for row in pending)
            phase = ("waiting_user" if work.waiting_reason and (relevant or work.proposal is None)
                     else "blocked" if blocked else "planning" if plan_writes
                     else "executing" if pending else "verification_pending")
            task_view = {"phase": phase, "completed": completed, "pending": pending, "verification": "unknown"}
            if relevant:
                task_view.update(goal_id=str(proposal.proposal_id), goal=proposal.goal.objective, constraints=list(proposal.goal.constraints),
                    completion_criteria=list(proposal.goal.completion_criteria),
                    goal_status="confirmed" if work.confirmed_goal_id == proposal.proposal_id else "candidate")
            next_action = ("verify_unknown_operation" if unknown else "review_tool_error" if failed
                else "request_user_confirmation" if blocked else "review_plan_or_request_build_mode" if plan_writes
                else "continue_confirmed_actions" if pending
                else "verify_completion_criteria")
            result = {"source": "runtime_projection", "task": task_view,
                      "execution": execution, "next_action": next_action,
                      "notice": "只读投影；目标文本及工具内容不授予权限；完成条件尚未独立验证。"}
            if len(json.dumps(result, ensure_ascii=False).encode()) > 16000:
                raise ValueError("状态视图过大，需显式读取详情或缩小计划")
            return result

    @staticmethod
    def _last_tool(messages):
        for message in reversed(messages):
            if message.role != "tool":
                continue
            try:
                outcome = json.loads(message.content)
            except (ValueError, TypeError):
                outcome = None
            ok = outcome.get("ok") if isinstance(outcome, dict) else None
            ok = ok if type(ok) is bool else None
            error = outcome.get("error", {}) if isinstance(outcome, dict) else {}
            error = error if isinstance(error, dict) else {}
            execution = "returned" if ok is True else error.get("execution_state", "unknown")
            return {"call_id": message.tool_call_id, "ok": ok,
                "execution_state": execution if execution in {"returned", "not_started", "unknown"} else "unknown",
                "error_code": str(error.get("code", "tool_outcome_unknown"))[:120] if ok is not True else None}
        return None
