from min_agent.application import HarnessApplication
from min_agent.models import ModelResponse, ToolCall
from tests.fakes.model import ScriptedModel
from tests.unit.test_checkpoint_resume import note_response


def reply(text="已回答"):
    return ModelResponse(finish_reason="stop", content=text)


def test_new_completed_inputs_get_independent_budgets_without_losing_session(tmp_path):
    harness = HarnessApplication(tmp_path / "chat.sqlite3",
        model=ScriptedModel([reply(str(index)) for index in range(12)]), total_models=3)
    try:
        identity = harness.create_session()["session_id"]
        budgets = []
        for index in range(12):
            harness.submit(identity, f"独立问题{index}"); harness.wait(identity)
            state = harness.snapshot(identity)
            assert state["task"]["status"] == "completed"
            assert state["budget"]["usage"]["model_calls"] == 1
            budgets.append(state["budget"]["id"])
        assert len(set(budgets)) == 12
        assert len(state["messages"]) == 24
        assert len(harness.sessions.snapshot(harness.context(identity)).budgets) == 12
    finally: harness.close()


def test_new_task_after_restart_keeps_history_and_old_budget_audit(tmp_path):
    path = tmp_path / "restart.sqlite3"
    harness = HarnessApplication(path, model=ScriptedModel([reply("首轮结果")]), total_models=3)
    identity = harness.create_session()["session_id"]
    harness.submit(identity, "首轮问题"); harness.wait(identity)
    first = harness.snapshot(identity); harness.close()
    model = ScriptedModel([reply("第二轮结果")])
    harness = HarnessApplication(path, model=model, total_models=3)
    try:
        harness.submit(identity, "独立后续问题"); harness.wait(identity)
        second = harness.snapshot(identity)
        assert second["task"]["status"] == "completed"
        assert first["budget"]["id"] != second["budget"]["id"]
        records = harness.sessions.snapshot(harness.context(identity)).budgets
        assert all(record.state.usage.model_calls == 1 for record in records.values())
        assert any(message.content == "首轮结果" for message in model.calls[0])
    finally: harness.close()


def test_paused_task_queue_and_resume_cannot_replace_exhausted_original_budget(tmp_path):
    model = ScriptedModel([
        ModelResponse(finish_reason="tool_calls", tool_calls=[
            ToolCall(id="calculate", name="calculator", arguments={"expression":"0.1+0.2"})]),
        note_response(), note_response(),
    ])
    harness = HarnessApplication(tmp_path / "paused.sqlite3", model=model, total_models=3, segment_models=2)
    try:
        identity = harness.create_session()["session_id"]
        harness.submit(identity, "核对任务"); harness.wait(identity)
        first = harness.snapshot(identity)
        assert first["task"]["status"] == "partial"
        harness.submit(identity, "普通消息不能清空累计"); harness.wait(identity)
        assert len(model.calls) == 2
        harness.resume(identity, checkpoint_id=first["checkpoint"]["checkpoint"]["checkpoint_id"])
        harness.wait(identity)
        final = harness.snapshot(identity)
        assert final["budget"]["id"] == first["budget"]["id"]
        assert final["budget"]["usage"]["model_calls"] == 3
        assert len(harness.sessions.snapshot(harness.context(identity)).budgets) == 1
        assert final["events"]["queue"] == ["普通消息不能清空累计"]
    finally: harness.close()
