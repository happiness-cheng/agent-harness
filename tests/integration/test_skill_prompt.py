"""WIRING-001 应用层：默认路径 system 含技能清单段；read_skill 真读全文与子文档；trace 留元数据。"""
import json
from hashlib import sha256
from pathlib import Path

from min_agent.application import HarnessApplication
from min_agent.compression.skills import SkillCatalog
from min_agent.contracts.models import ModelResponse, ToolCall
from tests.fakes.model import ScriptedModel

SKILL_ROOT = Path(__file__).resolve().parents[2] / "src" / "min_agent" / "skills"


def test_default_path_prompt_lists_skills_and_read_skill_loads_full_body(tmp_path):
    model = ScriptedModel([
        ModelResponse(finish_reason="tool_calls", tool_calls=[
            ToolCall(id="s1", name="read_skill", arguments={"name": "criteria-before-tests"}),
        ]),
        ModelResponse(finish_reason="tool_calls", tool_calls=[
            ToolCall(id="s2", name="read_skill", arguments={
                "name": "evidence-before-compaction", "path": "references/evidence-checklist.md"}),
        ]),
        ModelResponse(finish_reason="stop", content="已读取技能"),
    ])
    app = HarnessApplication(tmp_path / "skills.sqlite3", model=model)
    try:
        identity = app.create_session()["session_id"]
        app.submit(identity, "我要给这个仓库补测试，先读相关技能再回答")
        app.wait(identity, timeout=30)
        saved = app.snapshot(identity)
    finally:
        app.close()

    assert saved["task"]["status"] == "completed", saved["task"].get("error_code")

    system = model.calls[0][0].content
    catalog = SkillCatalog(SKILL_ROOT)
    section = catalog.prompt_section()
    assert section and section in system
    assert "criteria-before-tests" in system
    body = catalog.read("criteria-before-tests")
    assert body.split("---", 2)[2].strip()[:20] not in system

    read_skill_schema = next(s for s in model.tool_schemas[0] if s["name"] == "read_skill")
    assert "可用：" not in read_skill_schema["description"]
    assert "path" in read_skill_schema["parameters"]["properties"]

    tool_messages = [m for m in saved["messages"] if m["role"] == "tool"]
    assert any("写测试先定判据" in m["content"] and "硬门不折价" in m["content"] for m in tool_messages)
    assert any("判据原文" in m["content"] and "交接自足" in m["content"] for m in tool_messages)

    record = saved["trace_runs"][-1]
    assert record["prompt_sections"]["skills"] == {
        "sha256": sha256(section.encode("utf-8")).hexdigest(),
        "skill_count": len(catalog.entries),
    }
    read_skill_ref = sha256(b"read_skill").hexdigest()
    tool_events = [e for e in saved["trace"] if e["operation"] == "tool"]
    assert sum(1 for e in tool_events if e["tool_ref"] == read_skill_ref) == 2
    assert all(e["status"] == "completed" for e in tool_events if e["tool_ref"] == read_skill_ref)


def test_unregistered_subdoc_is_rejected_at_runtime(tmp_path):
    model = ScriptedModel([
        ModelResponse(finish_reason="tool_calls", tool_calls=[
            ToolCall(id="s1", name="read_skill", arguments={
                "name": "criteria-before-tests", "path": "../../prompts/system.md"}),
        ]),
        ModelResponse(finish_reason="stop", content="收到拒绝"),
    ])
    app = HarnessApplication(tmp_path / "skills-reject.sqlite3", model=model)
    try:
        identity = app.create_session()["session_id"]
        app.submit(identity, "读取技能子文档")
        app.wait(identity, timeout=30)
        saved = app.snapshot(identity)
    finally:
        app.close()

    tool_messages = [m for m in saved["messages"] if m["role"] == "tool"]
    assert tool_messages, "越界子文档读取必须产生失败的工具结果"
    payload = json.loads(tool_messages[0]["content"])
    assert payload["ok"] is False
    assert payload["error"]["code"] == "tool_execution_failed"
    assert "基础系统提示词" not in tool_messages[0]["content"]
    assert "system.md" not in tool_messages[0]["content"]