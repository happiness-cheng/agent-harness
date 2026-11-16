"""Windows通知API接受不等于用户实际看到；点击只打开本地会话URL。"""

from uuid import UUID


class DesktopDisabled(RuntimeError):
    """系统或应用通知关闭，保留会话事件供用户查看。"""


class DesktopChannel:
    def __init__(self, *, port=8765):
        if type(port) is not int or not 1024 <= port <= 65535: raise ValueError("本地端口无效")
        from windows_toasts import WindowsToaster
        self.toaster, self.port = WindowsToaster("Agent Harness"), port

    def __call__(self, event):
        from windows_toasts import Toast
        from winrt.windows.ui.notifications import NotificationSetting
        if self.toaster.toastNotifier.setting != NotificationSetting.ENABLED:
            raise DesktopDisabled("Windows通知未启用")
        session_id = UUID(event["session_id"])
        toast = Toast(text_fields=["Agent Harness 提醒", event["title"]],
                      launch_action=f"http://127.0.0.1:{self.port}/?session={session_id}")
        self.toaster.show_toast(toast)
