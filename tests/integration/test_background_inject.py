"""GOV-001-S9：后台任务结果在后续轮次以 task_notification 注入主循环。"""

import pytest

from min_agent.application import HarnessApplication
from min_agent.contracts.models import ModelResponse, ToolCall
from tests.fakes.model import ScriptedModel


def test_background_result_injected_in_later_round(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # 轮1：后台 echo（很快完成）；轮2：同步 sleep 给后台收尾时间；轮3：stop。
    model = ScriptedModel([
        ModelResponse(finish_reason="tool_calls", tool_calls=[
            ToolCall(id="b1", name="bash",
                     arguments={"command": "echo done", "run_in_background": True})]),
        ModelResponse(finish_reason="tool_calls", tool_calls=[
            ToolCall(id="s1", name="bash", arguments={"command": "sleep 0.4"})]),
        ModelResponse(finish_reason="stop", content="收到后台结果。"),
    ])
    app = HarnessApplication(tmp_path / "bg.sqlite3", workspace_root=workspace,
                             generic_tools=True, model=model)
    try:
        session_id = app.create_session("bg")["session_id"]
        app.submit(session_id, "后台跑 echo 再做别的")
        app.wait(session_id, timeout=30)
        snapshot = app.sessions.snapshot(app.context(session_id))
    finally:
        app.close()
    assert snapshot.task.status == "completed"
    # 占位结果（bg_id）+ 后续轮次的 task_notification 都在历史里。
    contents = [m.content or "" for m in snapshot.messages]
    assert any("bg_id" in c and "background" in c for c in contents)
    assert any("<task_notification" in c and 'status="completed"' in c for c in contents)