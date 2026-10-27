import json
from uuid import uuid4

import httpx

from min_agent.llm.client import OpenAIResponsesModel
from min_agent.llm.config import OpenAISettings
from min_agent.models import Message
from min_agent.runtime import Runtime
from min_agent.sessions import SessionStore
from min_agent.storage import SQLiteStorage
from min_agent.tools.calculator import CALCULATOR_TOOL
from min_agent.tools.registry import ToolRegistry
from tests.unit.test_llm_adapter import text_output, wire_response


def test_native_reasoning_tool_and_message_items_survive_sqlite_restart_without_cache(tmp_path):
    path = tmp_path / "native.sqlite3"
    reasoning = {"id": "rs", "type": "reasoning", "summary": [{"type": "summary_text", "text": "可见摘要"}], "encrypted_content": "opaque-test-state"}
    call = {"id": "fc", "type": "function_call", "call_id": "call", "name": "calculator", "arguments": '{"expression":"1+1"}', "status": "completed"}
    captured = []
    def handle(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=wire_response(reasoning, call) if len(captured) == 1 else wire_response(text_output("2")))
    tools = ToolRegistry()
    tools.register(CALCULATOR_TOOL)
    with SQLiteStorage.create_new(path) as storage:
        sessions = SessionStore(storage=storage)
        context = sessions.create(uuid4())
        with OpenAIResponsesModel(OpenAISettings(api_key="unit-test-token"), transport=httpx.MockTransport(handle)) as model:
            result = Runtime(model, tools=tools).run_session("计算1+1", store=sessions, context=context)
        assert result.status == "completed"
        before = sessions.snapshot(context)
    captured.clear()
    with SQLiteStorage.open(path) as storage:
        sessions = SessionStore(storage=storage)
        assert sessions.snapshot(context) == before
        # 新适配器没有旧响应缓存；仅从已恢复的消息回放完整原生条目。
        with OpenAIResponsesModel(OpenAISettings(api_key="unit-test-token"), transport=httpx.MockTransport(lambda request: captured.append(json.loads(request.content)) or httpx.Response(200, json=wire_response(text_output("继续"))))) as model:
            assert Runtime(model, tools=tools).run_session("继续", store=sessions, context=context).status == "completed"
        assert captured[0]["input"][1:3] == [reasoning, call]
        assert captured[0]["input"][3]["type"] == "function_call_output"
        assert captured[0]["input"][4] == text_output("2")
