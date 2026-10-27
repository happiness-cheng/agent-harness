"""把预算提交绑定到现有会话快照；不创建SQL表或更改数据库版本。"""

from uuid import UUID, uuid4

from min_agent.budget import BudgetBinding, BudgetLedger, BudgetLimits, BudgetPersistenceError, BudgetRecord
from min_agent.sessions import SessionStore


class SessionBudgets:
    def __init__(self, sessions: SessionStore) -> None:
        if not isinstance(sessions, SessionStore) or sessions._storage is None:
            raise TypeError("持久预算需要绑定SQLite的SessionStore")
        self._sessions = sessions

    def create(self, context, total_limits: BudgetLimits) -> UUID:
        with self._sessions.control(context):
            state = BudgetLedger(total_limits).snapshot()
            budget_id = uuid4()
            record = BudgetRecord(budget_id=budget_id, owner_id=context.owner_id,
                                  session_id=context.session_id, revision=0, state=state)
            try:
                self._sessions.set_budget_record(context, record, expected_revision=None, activate=True)
            except Exception:
                raise BudgetPersistenceError() from None
            return budget_id

    def open(self, context, budget_id: UUID) -> BudgetLedger:
        with self._sessions.control(context):
            record = self._sessions.snapshot(context).budgets[budget_id]
            key = (context.session_id, budget_id)
            if key in self._sessions._budget_ledgers:
                return self._sessions._budget_ledgers[key]
            previous_state = [record.state]

            def check_context(actual_context, actual_store):
                if actual_store is not self._sessions or actual_context != context:
                    raise PermissionError("持久预算不属于当前存储和会话")
                if self._sessions.snapshot(context).active_budget_id != budget_id:
                    raise PermissionError("预算不是当前会话的活动任务预算")

            def persist(state):
                check_context(context, self._sessions)
                current = self._sessions.snapshot(context).budgets[budget_id]
                if current.state != previous_state[0]:
                    raise ValueError("预算句柄状态已过期")
                candidate = BudgetRecord(**{
                    **current.model_dump(), "revision": current.revision + 1, "state": state,
                })
                self._sessions.set_budget_record(context, candidate, expected_revision=current.revision)
                previous_state[0] = state

            ledger = BudgetLedger.restore(
                record.state, on_change=persist, lock=self._sessions._lock,
                binding=BudgetBinding(budget_id=budget_id, owner_id=context.owner_id, session_id=context.session_id),
                check_context=check_context,
            )
            self._sessions._budget_ledgers[key] = ledger
            return ledger

    def select(self, context, budget_id: UUID) -> None:
        with self._sessions.control(context):
            current = self._sessions.snapshot(context).budgets[budget_id]
            candidate = BudgetRecord(**{**current.model_dump(), "revision": current.revision + 1})
            self._sessions.set_budget_record(context, candidate, expected_revision=current.revision, activate=True)
