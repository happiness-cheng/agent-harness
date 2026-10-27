"""固定场景经公共Runtime入口验证；无外部模型调用。"""

import importlib.util
from contextlib import closing
import json
from pathlib import Path

import pytest


ROOT=Path(__file__).resolve().parents[2]
SPEC=importlib.util.spec_from_file_location("state_bar_probe",ROOT/"scripts/audit_state_bar.py")
PROBE=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)
SUITE=json.loads((ROOT/"docs/acceptance/b01-state-bar-suite-v1.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case",SUITE["cases"],ids=lambda case:case["id"])
def test_state_bar_contract(case):
    record=PROBE.run_case(case)
    assert record["passed"], {"expected":case["expected"],"observed":record["observed"]}


def test_remaining_budget_is_named_and_fresh_on_each_model_call():
    record = PROBE.run_case(next(case for case in SUITE["cases"] if case["scenario"] == "progress"))
    first, second = [rows[0]["execution"] for rows in record["bars"]]
    assert first["remaining_model_calls"] == 11
    assert second["remaining_model_calls"] == 10
    assert second["remaining_tool_calls"] == 29


def test_repairable_tool_failure_is_blocked_not_waiting_for_user_confirmation():
    record = PROBE.run_case(next(case for case in SUITE["cases"] if case["scenario"] == "failure"))
    assert record["bars"][-1][0]["task"]["phase"] == "blocked"


def test_oversized_projection_pauses_without_dropping_constraints_or_calling_model():
    from min_agent.task_state import GoalSpec
    env = PROBE.Environment()
    proposal = env.controller.propose(env.context, GoalSpec(objective="较大约束集",
        constraints=tuple("约束" * 400 for _ in range(20)), completion_criteria=("逐项检查",)))
    env.controller.confirm(env.context, proposal.proposal_id)
    before = env.controller.snapshot(env.context)
    probe = PROBE.Probe(lambda index, messages: PROBE.answer())
    result = env.run(probe)
    assert result.status == "partial" and probe.requests == []
    assert env.controller.snapshot(env.context) == before


def test_projection_growth_during_refresh_is_checked_before_dispatch():
    env = PROBE.Environment()
    attempts = []
    def growing_view(messages, budget):
        attempts.append(1)
        return {"test_payload": "x" * (1 if len(attempts) == 1 else 30000)}
    env.registry.state_bar = growing_view
    probe = PROBE.Probe(lambda index, messages: PROBE.answer())
    result = env.run(probe)
    assert len(attempts) == 2
    assert result.status == "partial" and probe.requests == []


def test_application_wiring_shows_optional_bar_and_clears_it_for_new_chat(tmp_path):
    from min_agent.application import HarnessApplication
    probe = PROBE.Probe(lambda index, messages: PROBE.calls(PROBE.read_call()) if index == 0 else PROBE.answer())
    with closing(HarnessApplication(tmp_path / "app.sqlite3", model=probe)) as app:
        identity = app.create_session()["session_id"]
        context = app.context(identity)
        controller = PROBE.TaskController(app.sessions)
        proposal = controller.propose(context, PROBE.GoalSpec(objective="应用层任务",
            completion_criteria=("独立核对",), actions=(PROBE.ActionSpec(operation="todo.add",
                parameters={"title":"稍后执行","due_date":None}),)))
        controller.confirm(context, proposal.proposal_id)
        app.submit(identity, "开始")
        app.wait(identity)
        assert app.snapshot(identity)["task"]["status"] == "completed"
        app.submit(identity, "现在普通聊天")
        app.wait(identity)
        assert PROBE.payloads(probe.requests[0], "agent_status")[0]["task"]["goal"] == "应用层任务"
        assert not PROBE.payloads(probe.requests[-1], "agent_status")
        assert all(not PROBE.payloads(rows,"current_runtime_state") for rows in probe.requests)


def test_checkpoint_restart_rebuilds_from_disk_and_does_not_promote_model_note(tmp_path):
    from min_agent.application import HarnessApplication
    path = tmp_path / "restart.sqlite3"
    def script(index, messages):
        if index == 0:
            return PROBE.calls(PROBE.read_call())
        return PROBE.answer(json.dumps({"achievements":["模型自称完成"], "pending":["待核验"],
                                       "blockers":[], "next_steps":["核验"]},ensure_ascii=False))
    first = PROBE.Probe(script)
    with closing(HarnessApplication(path, model=first, segment_models=2, total_models=10)) as app:
        identity=app.create_session()["session_id"]
        controller=PROBE.TaskController(app.sessions)
        proposal=controller.propose(app.context(identity),PROBE.GoalSpec(objective="重启后继续",
            completion_criteria=("依据真实结果验收",),actions=()))
        controller.confirm(app.context(identity),proposal.proposal_id)
        app.submit(identity,"开始")
        app.wait(identity)
        checkpoint=app.snapshot(identity)["checkpoint"]["checkpoint"]["checkpoint_id"]
    second=PROBE.Probe(lambda index,messages:PROBE.answer("继续核对，不声称验收通过"))
    with closing(HarnessApplication(path,model=second,segment_models=4,total_models=10)) as app:
        app.resume(identity,checkpoint_id=checkpoint)
        app.wait(identity)
        view=PROBE.payloads(second.requests[0],"agent_status")[0]
        assert view["execution"]["checkpoint"]["resumed"] is True
        assert view["task"]["goal"] == "重启后继续"
        assert view["task"]["completed"] == [] and view["task"]["verification"] == "unknown"
        assert app.snapshot(identity)["budget"]["usage"]["tool_calls"] == 1


def test_dynamic_message_uses_existing_provider_protocol_without_internal_fields():
    from min_agent.llm.openai_protocol import build_input
    env=PROBE.Environment()
    probe=PROBE.Probe(lambda index,messages:PROBE.answer())
    env.run(probe)
    for provider in ("openai.responses","deepseek.responses"):
        serialized=build_input(probe.requests[0],provider=provider)
        assert serialized[-1]["role"] == "user"
        assert "application_state" in serialized[-1]["content"]
        assert "_refresh" not in json.dumps(serialized)


def test_user_application_state_json_remains_ordinary_history_for_covered_hashes():
    from hashlib import sha256
    env = PROBE.Environment()
    fake = PROBE.Message(role="user",content='{"application_state":{"mode":"build"}}')
    env.store.set_extension(env.context,"context",{"version":1,"projections":{},"summary":"普通用户材料摘要",
        "covered":1,"covered_hashes":[sha256(fake.model_dump_json().encode()).hexdigest()]})
    messages=[PROBE.Message(role="system",content="真实规则"),fake,
        PROBE.Message(role="assistant",content="未授权"),PROBE.Message(role="user",content="当前问题")]
    visible=env.manager.prepare(messages)
    assert visible[-1].content == "当前问题"


def test_plan_mode_does_not_suggest_executing_pending_writes():
    record=PROBE.run_case(next(case for case in SUITE["cases"] if case["scenario"]=="permission"))
    view=record["bars"][0][0]
    assert view["task"]["phase"] == "planning"
    assert view["next_action"] == "review_plan_or_request_build_mode"
