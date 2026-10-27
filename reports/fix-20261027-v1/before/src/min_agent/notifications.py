"""一条持久事件、两个渠道状态；发送不自动标记用户已读。"""


class Notifications:
    def __init__(self, sessions): self.sessions = sessions

    def list_items(self, context):
        return list(self.sessions.extension(context, "notifications", {}).values())

    def mark_read(self, context, event_id):
        with self.sessions.control(context):
            events = self.sessions.extension(context, "notifications", {})
            if str(event_id) not in events: raise PermissionError("通知不属于当前会话")
            events[str(event_id)]["unread"] = False
            self.sessions.set_extension(context, "notifications", events)

    def retry_desktop(self, context, event_id):
        with self.sessions.control(context):
            events = self.sessions.extension(context, "notifications", {})
            if str(event_id) not in events: raise PermissionError("通知不属于当前会话")
            event = events[str(event_id)]
            if event["desktop"]["status"] not in {"failed", "unknown", "disabled"}: raise ValueError("该通知不需要人工重发")
            event["desktop"]["status"] = "pending"
            self.sessions.set_extension(context, "notifications", events)
