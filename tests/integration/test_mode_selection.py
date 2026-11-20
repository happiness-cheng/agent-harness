"""SHELL-007：欢迎页选模式——创建会话时绑定 Plan/Build 的契约（离线）。"""

from fastapi.testclient import TestClient

from min_agent.application import HarnessApplication
from min_agent.contracts.models import ModelResponse
from min_agent.web.app import create_app
from tests.fakes.model import ScriptedModel


def client_for(tmp_path):
    harness = HarnessApplication(
        tmp_path / "mode.sqlite3",
        model=ScriptedModel([ModelResponse(finish_reason="stop", content="ok")]),
    )
    client = TestClient(create_app(harness), base_url="http://127.0.0.1")
    headers = {"X-Harness-CSRF": client.get("/api/bootstrap").json()["csrf"]}
    return harness, client, headers


def test_create_session_with_build_mode_sets_waiting_for_goal(tmp_path):
    _, client, headers = client_for(tmp_path)

    created = client.post("/api/sessions", json={"label": "Build 会话", "mode": "build"}, headers=headers)

    assert created.status_code == 201
    assert created.json()["mode"] == "build"
    session_id = created.json()["session_id"]
    state = client.get(f"/api/sessions/{session_id}").json()["work_state"]
    assert state["mode"] == "build"
    assert state["status"] == "waiting_user"
    assert state["waiting_reason"] == "goal_unconfirmed"


def test_create_session_with_plan_mode_is_ready(tmp_path):
    _, client, headers = client_for(tmp_path)

    created = client.post("/api/sessions", json={"label": "Plan 会话", "mode": "plan"}, headers=headers)

    session_id = created.json()["session_id"]
    state = client.get(f"/api/sessions/{session_id}").json()["work_state"]
    assert state["mode"] == "plan"
    assert state["status"] == "ready"


def test_create_session_defaults_to_plan(tmp_path):
    _, client, headers = client_for(tmp_path)

    created = client.post("/api/sessions", json={"label": "默认模式"}, headers=headers)

    assert created.json()["mode"] == "plan"
    session_id = created.json()["session_id"]
    assert client.get(f"/api/sessions/{session_id}").json()["work_state"]["mode"] == "plan"


def test_invalid_mode_is_rejected_without_creating_session(tmp_path):
    harness, client, headers = client_for(tmp_path)
    before = len(harness.list_sessions())

    refused = client.post("/api/sessions", json={"label": "非法模式", "mode": "auto"}, headers=headers)

    assert refused.status_code == 400
    assert len(harness.list_sessions()) == before
