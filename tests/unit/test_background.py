"""GOV-001-S9：BackgroundManager 与 bash run_in_background 占位结果。"""

import json
import threading

import pytest

from min_agent.tools.background import BackgroundManager
from min_agent.tools.generic import GenericWorkspace


def test_start_collect_completed():
    manager = BackgroundManager()
    done = threading.Event()

    def runner(command, timeout):
        done.wait(2)
        return {"ok": True, "exit_code": 0, "stdout": "installed", "stderr": ""}

    bg_id = manager.start("pip install x", runner, 30)
    assert bg_id.startswith("bg_")
    assert manager.tasks()[bg_id]["status"] == "running"
    assert manager.collect() == []  # 还没完成
    done.set()
    # 等线程收尾。
    for _ in range(100):
        if manager.tasks()[bg_id]["status"] != "running":
            break
        threading.Event().wait(0.02)
    notifications = manager.collect()
    assert len(notifications) == 1
    assert f'bg_id="{bg_id}"' in notifications[0]
    assert 'status="completed"' in notifications[0]
    assert "installed" in notifications[0]
    assert manager.collect() == []  # drain 后为空


def test_failed_command_marked_failed():
    manager = BackgroundManager()

    def runner(command, timeout):
        return {"ok": True, "exit_code": 1, "stdout": "", "stderr": "boom"}

    bg_id = manager.start("false", runner, 30)
    for _ in range(100):
        if manager.tasks()[bg_id]["status"] != "running":
            break
        threading.Event().wait(0.02)
    notifications = manager.collect()
    assert 'status="failed"' in notifications[0]


def test_runner_exception_marked_failed():
    manager = BackgroundManager()

    def runner(command, timeout):
        raise RuntimeError("worker died")

    bg_id = manager.start("x", runner, 30)
    for _ in range(100):
        if manager.tasks()[bg_id]["status"] != "running":
            break
        threading.Event().wait(0.02)
    assert 'status="failed"' in manager.collect()[0]


def test_bash_background_returns_placeholder(tmp_path):
    manager = BackgroundManager()
    workspace = GenericWorkspace(tmp_path, background=manager)
    result = json.loads(workspace.bash("echo hi", run_in_background=True))
    assert result["ok"] is True and result["background"] is True
    assert result["bg_id"].startswith("bg_")
    assert result["bg_id"] in manager.tasks()


def test_bash_background_without_manager_rejected(tmp_path):
    workspace = GenericWorkspace(tmp_path)
    result = json.loads(workspace.bash("echo hi", run_in_background=True))
    assert result["ok"] is False
    assert result["error"]["code"] == "background_unavailable"