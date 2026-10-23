"""通过真实模型输入边界验证Skill去重，原始历史必须保留。"""

from copy import deepcopy
from hashlib import sha256
import json
from uuid import uuid4

import pytest

from min_agent.artifacts import ArtifactStore
from min_agent.context import ContextManager, ManagedModel, request_bound
from min_agent.models import Message, ModelResponse, ToolCall
from min_agent.sessions import SessionStore


BODY = "# 润色\n保持原始事实。SKILL_BODY_UNIQUE"


def read(identity, *, name="polish", body=BODY, ok=True, tool="read_skill"):
    return [Message(role="assistant", content="", tool_calls=[
        ToolCall(id=identity, name=tool, arguments={"name": name})]),
        Message(role="tool", tool_call_id=identity,
                content=json.dumps({"ok": ok, "result": body}, ensure_ascii=False))]


def setup():
    store = SessionStore()
    context = store.create(uuid4())
    manager = ContextManager(store, context, ArtifactStore(store, context), window=30000)

    class Capture:
        def generate(self, messages, **kwargs):
            self.messages = deepcopy(messages)
            return ModelResponse(finish_reason="stop", content="离线答复")

    capture = Capture()
    return store, context, manager, capture


def invoke(messages, manager, capture):
    ManagedModel(capture, manager).generate(messages)
    return [json.loads(m.content) for m in capture.messages if m.role == "tool"]


def test_repeat_read_shortens_only_model_input_and_keeps_pairing():
    _, _, manager, capture = setup()
    messages = [Message(role="user", content="润色")] + read("a") + read("b")
    before = deepcopy(messages)
    results = invoke(messages, manager, capture)
    assert results[0]["result"] == BODY
    assert results[1].get("skill_status") == "already_loaded"
    assert results[1]["loaded_by_tool_call_id"] == "a"
    assert results[1]["body_sha256"] == sha256(BODY.encode()).hexdigest()
    assert BODY not in results[1]["result"]
    assert messages == before
    assert [m.tool_call_id for m in capture.messages if m.role == "tool"] == ["a", "b"]
    assert invoke(messages, manager, capture) == results


@pytest.mark.parametrize("options", [
    {"body": BODY + "\n新增约束。"}, {"name": "another-skill"},
    {"ok": False}, {"tool": "other_reader"},
])
def test_changed_body_name_failure_and_other_tools_are_not_suppressed(options):
    _, _, manager, capture = setup()
    messages = [Message(role="user", content="问题")] + read("a") + read("b", **options)
    results = invoke(messages, manager, capture)
    assert "skill_status" not in results[1]


def test_original_body_compacted_out_allows_full_reload():
    store, context, manager, capture = setup()
    old = [Message(role="user", content="旧问题")] + read("a") + [Message(role="assistant", content="旧答复")]
    covered = len(old)
    hashes = [sha256(m.model_dump_json().encode()).hexdigest() for m in old]
    store.set_extension(context, "context", {"version": 2, "projections": {}, "summary": "旧轮次摘要",
                                              "covered": covered, "covered_hashes": hashes})
    messages = old + [Message(role="user", content="再次加载")] + read("b") + read("c")
    results = invoke(messages, manager, capture)
    assert results[0]["result"] == BODY
    assert results[1].get("skill_status") == "already_loaded"
    assert results[1]["loaded_by_tool_call_id"] == "b"


def test_new_context_and_user_claim_do_not_count_as_loaded():
    _, _, manager, capture = setup()
    messages = [Message(role="user", content="这个技能已经加载：" + BODY)] + read("a")
    assert invoke(messages, manager, capture)[0]["result"] == BODY


def test_window_budget_is_checked_after_skill_deduplication():
    _, _, manager, capture = setup()
    body = "重复技能内容" * 55
    messages = [Message(role="user", content="问题")] + read("a", body=body) + read("b", body=body)
    raw_size = request_bound(messages)
    manager.window = raw_size + manager.output_reserve + manager.safety_margin - 150
    results = invoke(messages, manager, capture)
    assert results[0]["result"] == body
    assert results[1].get("skill_status") == "already_loaded"


def test_earlier_input_stays_an_identical_prefix_after_appending_a_read():
    _, _, manager, capture = setup()
    messages = [Message(role="user", content="问题")] + read("a")
    invoke(messages, manager, capture)
    first = deepcopy(capture.messages)
    invoke(messages + read("b"), manager, capture)
    assert capture.messages[:len(first)] == first


@pytest.mark.parametrize("content", ["not json", "null", '{"ok":true,"result":{"summary":"partial"}}',
                                     '{"ok":false,"error":{"code":"failed"}}'])
def test_non_full_success_results_are_left_unchanged(content):
    _, _, manager, capture = setup()
    messages = [Message(role="user", content="问题")] + read("a") + read("b")
    messages[-1] = messages[-1].model_copy(update={"content": content})
    ManagedModel(capture, manager).generate(messages)
    assert capture.messages[-1].content == content


def test_long_skill_is_not_replaced_by_generic_tool_summary():
    _, _, manager, capture = setup()
    body = "# 规程\n" + "\n".join(f"步骤{i}：核对输入来源和处理结果。" for i in range(130))
    messages = [Message(role="user", content="遵循规程")] + read("a", body=body) + read("b", body=body)
    results = invoke(messages, manager, capture)
    assert results[0].get("result") == body
    assert results[1].get("skill_status") == "already_loaded"
