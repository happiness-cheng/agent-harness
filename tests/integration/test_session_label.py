"""SHELL-004：会话重命名接口的归属、校验与持久化（离线）。"""

from uuid import uuid4

from fastapi.testclient import TestClient

from min_agent.application import HarnessApplication
from min_agent.contracts.models import ModelResponse
from min_agent.web.app import create_app
from tests.fakes.model import ScriptedModel


def client_for(tmp_path):
    harness = HarnessApplication(tmp_path / "label.sqlite3",
                                  model=ScriptedModel([ModelResponse(finish_reason="stop", content="ok")]))
    client = TestClient(create_app(harness), base_url="http://127.0.0.1")
    headers = {"X-Harness-CSRF": client.get("/api/bootstrap").json()["csrf"]}
    return harness, client, headers


def test_rename_updates_list_and_detail(tmp_path):
    _, client, headers = client_for(tmp_path)
    session = client.post("/api/sessions", json={"label": "旧名字"}, headers=headers).json()["session_id"]

    renamed = client.post(f"/api/sessions/{session}/label", json={"label": "新名字"}, headers=headers)

    assert renamed.status_code == 200
    assert renamed.json()["label"] == "新名字"
    listed = client.get("/api/sessions").json()
    assert [item["label"] for item in listed if item["session_id"] == session] == ["新名字"]
    assert client.get(f"/api/sessions/{session}").json()["label"] == "新名字"


def test_rename_rejects_blank_and_overlong_labels(tmp_path):
    _, client, headers = client_for(tmp_path)
    session = client.post("/api/sessions", json={"label": "旧名字"}, headers=headers).json()["session_id"]

    blank = client.post(f"/api/sessions/{session}/label", json={"label": "   "}, headers=headers)
    overlong = client.post(f"/api/sessions/{session}/label", json={"label": "x" * 61}, headers=headers)

    assert blank.status_code == 400
    assert overlong.status_code == 422
    assert client.get(f"/api/sessions/{session}").json()["label"] == "旧名字"


def test_rename_requires_csrf_and_known_session(tmp_path):
    _, client, headers = client_for(tmp_path)
    session = client.post("/api/sessions", json={"label": "旧名字"}, headers=headers).json()["session_id"]

    no_csrf = client.post(f"/api/sessions/{session}/label", json={"label": "新名字"})
    unknown = client.post(f"/api/sessions/{uuid4()}/label", json={"label": "新名字"}, headers=headers)

    assert no_csrf.status_code == 403
    assert unknown.status_code == 403
    assert client.get(f"/api/sessions/{session}").json()["label"] == "旧名字"


def test_rename_persists_across_application_restart(tmp_path):
    database = tmp_path / "restart.sqlite3"
    first = HarnessApplication(database, model=ScriptedModel([ModelResponse(finish_reason="stop", content="ok")]))
    client = TestClient(create_app(first), base_url="http://127.0.0.1")
    headers = {"X-Harness-CSRF": client.get("/api/bootstrap").json()["csrf"]}
    session = client.post("/api/sessions", json={"label": "旧名字"}, headers=headers).json()["session_id"]
    assert client.post(f"/api/sessions/{session}/label", json={"label": "重启后仍是这个名字"},
                       headers=headers).status_code == 200
    first.close()

    second = HarnessApplication(database)
    try:
        assert second.snapshot(session)["label"] == "重启后仍是这个名字"
        assert [item["label"] for item in second.list_sessions()] == ["重启后仍是这个名字"]
    finally:
        second.close()
