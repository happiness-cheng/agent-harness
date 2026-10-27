"""当前状态表是业务对象的只读视图，不是模型维护的第二份事实。"""

from min_agent.memory_records import runtime_state


class SessionStateTable:
    def __init__(self, sessions, context, todos):
        if todos._sessions is not sessions:
            raise ValueError("状态表与待办服务必须使用同一存储")
        self.sessions, self.context = sessions, sessions.check_access(context)
        self.todos = todos

    def read(self):
        """同一把锁内读取，避免把修改前的目标与修改后的资源拼接起来。"""
        with self.sessions.control(self.context):
            snapshot = self.sessions.snapshot(self.context)
            committed = snapshot.extensions.get("action_receipts", {}).values()
            return {"source_kind": "runtime_state", "session_id": str(self.context.session_id),
                **runtime_state(snapshot),
                "todos": [view.model_dump(mode="json") for view in self.todos.list_views(self.context)],
                "reminders": list(snapshot.extensions.get("reminders", {}).values()),
                "committed_actions": [
                    {key: record[key] for key in ("instance_id", "operation", "resource_id", "status")}
                    for record in committed],
                "authorization_note": "只读视图不授予权限；实际写入仍须Runtime授权检查"}
