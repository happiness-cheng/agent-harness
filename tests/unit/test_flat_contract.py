"""GOV-001-S4：单层契约 {ok,error_code,message,data} 与 expected_sha256。"""

import hashlib
import json

import pytest

from min_agent.tools.generic import GenericWorkspace
from min_agent.tools.registry import ToolRegistry, flatten_outcome


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_flatten_nested_success():
    outcome = {"ok": True, "result": json.dumps({"ok": True, "path": "a", "content": "x"})}
    flat = flatten_outcome(outcome)
    assert flat == {"ok": True, "error_code": None, "message": "",
                    "data": {"path": "a", "content": "x"}}


def test_flatten_business_failure_uses_inner_ok():
    # 外层 ok=True（工具执行了），内层 ok=False（业务失败）→ 单层 ok=False。
    outcome = {"ok": True, "result": json.dumps(
        {"ok": False, "error": {"code": "stale_version", "message": "被改了"}})}
    flat = flatten_outcome(outcome)
    assert flat["ok"] is False
    assert flat["error_code"] == "stale_version"
    assert flat["message"] == "被改了"


def test_flatten_registry_error():
    outcome = {"ok": False, "error": {"code": "unknown_tool", "message": "未注册",
                                      "execution_state": "not_started"}}
    flat = flatten_outcome(outcome)
    assert flat["ok"] is False and flat["error_code"] == "unknown_tool"
    assert flat["data"]["execution_state"] == "not_started"


def test_flatten_plain_string_result():
    flat = flatten_outcome({"ok": True, "result": "纯文本"})
    assert flat["ok"] is True and flat["data"] == {"result": "纯文本"}


def test_registry_serialize_nested_vs_flat():
    outcome = {"ok": True, "result": json.dumps({"ok": True, "x": 1})}
    nested = ToolRegistry()
    flat = ToolRegistry(envelope="flat")
    assert json.loads(nested.serialize(outcome)) == outcome
    assert json.loads(flat.serialize(outcome))["data"] == {"x": 1}


def test_registry_rejects_bad_envelope():
    with pytest.raises(ValueError):
        ToolRegistry(envelope="bogus")


@pytest.fixture()
def workspace(tmp_path):
    return GenericWorkspace(tmp_path)


def test_write_with_matching_expected_sha_succeeds(workspace):
    workspace.write("a.txt", "hello")
    result = json.loads(workspace.write("a.txt", "world", expected_sha256=sha("hello")))
    assert result["ok"] is True


def test_write_with_stale_expected_sha_rejected(workspace):
    workspace.write("a.txt", "hello")
    result = json.loads(workspace.write("a.txt", "world", expected_sha256=sha("OTHER")))
    assert result["ok"] is False
    assert result["error"]["code"] == "stale_version"


def test_write_new_with_expected_sha_rejected(workspace):
    result = json.loads(workspace.write("new.txt", "x", expected_sha256=sha("anything")))
    assert result["ok"] is False
    assert result["error"]["code"] == "unexpected_version"


def test_edit_with_matching_expected_sha_succeeds(workspace):
    workspace.write("a.txt", "hello world")
    result = json.loads(workspace.edit("a.txt", "world", "there", expected_sha256=sha("hello world")))
    assert result["ok"] is True


def test_edit_with_stale_expected_sha_rejected(workspace):
    workspace.write("a.txt", "hello world")
    result = json.loads(workspace.edit("a.txt", "world", "there", expected_sha256=sha("OTHER")))
    assert result["ok"] is False
    assert result["error"]["code"] == "stale_version"


def test_eval_verifier_accepts_flat_shape():
    from eval.verifiers import _inner
    flat_ok = {"ok": True, "error_code": None, "message": "", "data": {"content": "l2\nl3"}}
    inner = _inner(flat_ok)
    assert inner["ok"] is True and inner["content"] == "l2\nl3"
    flat_fail = {"ok": False, "error_code": "blocked_command", "message": "拦", "data": {}}
    inner = _inner(flat_fail)
    assert inner["ok"] is False and inner["error"]["code"] == "blocked_command"