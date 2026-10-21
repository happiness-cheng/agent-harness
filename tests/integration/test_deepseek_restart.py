import json
from uuid import uuid4

import httpx

from min_agent.llm.deepseek import DeepSeekResponsesModel, DeepSeekSettings
from min_agent.runtime import Runtime
from min_agent.sessions import SessionStore
from min_agent.storage import SQLiteStorage
from min_agent.tools.calculator import CALCULATOR_TOOL
from min_agent.tools.registry import ToolRegistry
from tests.unit.test_deepseek_adapter import ds_response, ds_text


def test_deepseek_native_items_survive_sqlite_restart_without_adapter_cache(tmp_path):
    path = tmp_path / "deepseek.sqlite3"
    reasoning = {"type": "reasoning", "content": [{"type": "reasoning_text", "text": "synthetic-protocol-state"}]}
    call = {"type": "function_call", "call_id": "math", "name": "calculator", "arguments": '{"expression":"1+1"}'}
    captured = []

    def handle(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=ds_response(reasoning, call) if len(captured) == 1 else ds_response(ds_text("2")))

    tools = ToolRegistry()
    tools.register(CALCULATOR_TOOL)
    with SQLiteStorage.create_new(path) as storage:
        sessions = SessionStore(storage=storage)
        context = sessions.create(uuid4())
        with DeepSeekResponsesModel(DeepSeekSettings(api_key="unit-deepseek-token"), transport=httpx.MockTransport(handle)) as model:
            assert Runtime(model, tools=tools).run_session("计算1+1", store=sessions, context=context).status == "completed"
        before = sessions.snapshot(context)
    captured.clear()
    with SQLiteStorage.open(path) as storage:
        sessions = SessionStore(storage=storage)
        assert sessions.snapshot(context) == before
        with DeepSeekResponsesModel(DeepSeekSettings(api_key="unit-deepseek-token"), transport=httpx.MockTransport(lambda request: captured.append(json.loads(request.content)) or httpx.Response(200, json=ds_response(ds_text("继续"))))) as model:
            assert Runtime(model, tools=tools).run_session("继续", store=sessions, context=context).status == "completed"
        assert captured[0]["input"][1:3] == [reasoning, call]
        assert captured[0]["input"][3]["type"] == "function_call_output"
