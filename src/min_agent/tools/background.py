"""后台任务（GOV-001-S9）：慢 bash 操作放后台线程，主循环继续，后续轮次收集结果。

参考 learn-claude-code s11_background_tasks：模型用 bash 的 ``run_in_background=true``
显式请求后台；当前调用先返回带 ``bg_id`` 的占位结果，主循环不阻塞；后续每轮模型
调用前 ``collect()`` 把已完成结果以 ``<task_notification>`` 注入对话（不复用原
tool_use_id，一个 tool_use 仍只对应一个 tool_result）。

明确不保证：后台线程是生命周期清理，不是沙箱（隔离仍靠 S7 的 Docker 后端）；
结果不主动唤醒模型，只在下一轮进入主循环时收集；进程退出时 daemon 线程随之结束。
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime

MAX_NOTIFICATION_BYTES = 16 * 1024


def _format_result(result: dict) -> str:
    if not result.get("ok"):
        error = result.get("error", {})
        return f"执行失败：{error.get('code', 'unknown')} {error.get('message', '')}"
    parts = [f"exit_code={result.get('exit_code')}"]
    if result.get("stdout"):
        parts.append("stdout:\n" + result["stdout"])
    if result.get("stderr"):
        parts.append("stderr:\n" + result["stderr"])
    text = "\n".join(parts)
    if len(text) > MAX_NOTIFICATION_BYTES:
        text = text[:MAX_NOTIFICATION_BYTES] + "\n...[truncated]"
    return text


class BackgroundManager:
    """每会话一个；登记任务、daemon 线程执行、完成队列收集。线程安全。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[str, dict] = {}
        self._results: dict[str, str] = {}
        self._ready: list[str] = []

    def start(self, command: str, runner, timeout_seconds: float) -> str:
        """登记任务并起 daemon 线程跑 runner(command, timeout)；立即返回 bg_id。"""
        bg_id = "bg_" + uuid.uuid4().hex[:8]
        with self._lock:
            self._tasks[bg_id] = {"command": command, "status": "running",
                                  "started_at": datetime.now(UTC).isoformat()}
        thread = threading.Thread(target=self._run, args=(bg_id, command, runner, timeout_seconds),
                                  daemon=True)
        thread.start()
        return bg_id

    def _run(self, bg_id: str, command: str, runner, timeout_seconds: float) -> None:
        try:
            result = runner(command, timeout_seconds)
            status = "completed" if result.get("ok") and result.get("exit_code") == 0 else "failed"
            formatted = _format_result(result)
        except Exception as error:  # worker 异常 → failed，如实记录
            status = "failed"
            formatted = f"后台任务异常：{type(error).__name__}: {error}"
        with self._lock:
            self._tasks[bg_id]["status"] = status
            self._tasks[bg_id]["finished_at"] = datetime.now(UTC).isoformat()
            self._results[bg_id] = formatted
            self._ready.append(bg_id)

    def collect(self) -> list[str]:
        """取出已完成结果，格式化为 <task_notification>； drain 完成队列。"""
        with self._lock:
            ready, self._ready = self._ready, []
            notifications = []
            for bg_id in ready:
                task = self._tasks[bg_id]
                body = self._results.get(bg_id, "")
                notifications.append(
                    f'<task_notification bg_id="{bg_id}" status="{task["status"]}">\n'
                    f"命令：{task['command']}\n{body}\n</task_notification>")
            return notifications

    def tasks(self) -> dict[str, dict]:
        with self._lock:
            return {bg_id: dict(task) for bg_id, task in self._tasks.items()}