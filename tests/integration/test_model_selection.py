"""SHELL-006：欢迎页选模型——目录接口与「创建会话时绑定模型」的契约（离线）。"""

from fastapi.testclient import TestClient

from min_agent.application import HarnessApplication
from min_agent.llm.qianwen import QianwenCatalog
from min_agent.contracts.models import ModelResponse
from min_agent.web.app import create_app
from tests.fakes.model import ScriptedModel

MODELS = ("qwen3.8-flash", "qwen3.8-max", "qwen3.7-plus", "deepseek-v4.1-flash")


def catalog():
    # settings_for 只在真正实例化模型时使用；本测试不发起模型调用。
    return QianwenCatalog(lambda name=None: None)


def client_for(tmp_path):
    harness = HarnessApplication(
        tmp_path / "models.sqlite3",
        model=ScriptedModel([ModelResponse(finish_reason="stop", content="ok")]),
        model_catalog=catalog(),
    )
    client = TestClient(create_app(harness), base_url="http://127.0.0.1")
    headers = {"X-Harness-CSRF": client.get("/api/bootstrap").json()["csrf"]}
    return harness, client, headers


def test_models_endpoint_exposes_default_and_options(tmp_path):
    _, client, _ = client_for(tmp_path)

    status = client.get("/api/models").json()

    assert status["selectable"] is True
    assert status["current"] == "qwen3.8-flash"
    assert [option["id"] for option in status["options"]] == list(MODELS)


def test_create_session_with_model_binds_choice_before_first_message(tmp_path):
    _, client, headers = client_for(tmp_path)

    created = client.post("/api/sessions", json={"label": "欢迎页选模型", "model": "qwen3.8-max"},
                          headers=headers)

    assert created.status_code == 201
    assert created.json()["model"] == "qwen3.8-max"
    session_id = created.json()["session_id"]
    snapshot = client.get(f"/api/sessions/{session_id}").json()
    assert snapshot["model"]["current"] == "qwen3.8-max"
    assert snapshot["messages"] == []


def test_create_session_without_model_uses_catalog_default(tmp_path):
    _, client, headers = client_for(tmp_path)

    created = client.post("/api/sessions", json={"label": "默认模型"}, headers=headers)

    assert created.status_code == 201
    session_id = created.json()["session_id"]
    assert client.get(f"/api/sessions/{session_id}").json()["model"]["current"] == "qwen3.8-flash"


def test_invalid_model_is_rejected_without_creating_session(tmp_path):
    harness, client, headers = client_for(tmp_path)
    before = len(harness.list_sessions())

    refused = client.post("/api/sessions", json={"label": "非法模型", "model": "not-a-model"},
                          headers=headers)

    assert refused.status_code == 400
    assert len(harness.list_sessions()) == before


def test_models_endpoint_reports_not_selectable_without_catalog(tmp_path):
    harness = HarnessApplication(tmp_path / "plain.sqlite3",
                                 model=ScriptedModel([ModelResponse(finish_reason="stop", content="ok")]))
    client = TestClient(create_app(harness), base_url="http://127.0.0.1")
    client.get("/api/bootstrap")

    assert client.get("/api/models").json() == {"selectable": False, "current": None, "options": []}
