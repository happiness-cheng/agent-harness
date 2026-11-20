"""FS-WRITE-001：写入/编辑的授权门、版本校验、原子写与拒绝分支（离线）。"""

import json
import hashlib
from types import SimpleNamespace

import pytest

from min_agent.tools import filesystem
from min_agent.tools.filesystem import FileSandbox, build_write_gate, filesystem_tools


def payload(text: str) -> dict:
    return json.loads(text)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture()
def workspace(tmp_path):
    (tmp_path / "notes.md").write_bytes("第一行\n第二行\n".encode())
    (tmp_path / "dup.txt").write_bytes("重复 重复".encode())
    (tmp_path / ".env").write_bytes(b"SECRET=1")
    return tmp_path


def allow():
    return True, "allowed", ""


def deny(code, message="拒绝"):
    return False, code, message


# ---------- 授权门 ----------

def test_gate_requires_build_confirmed_and_not_waiting():
    def controller(mode, confirmed, status):
        state = SimpleNamespace(mode=mode, confirmed_goal_id=confirmed, status=status)
        return SimpleNamespace(snapshot=lambda context: state)

    assert build_write_gate(controller("plan", "g", "ready"), object)() == (
        False, "plan_read_only", "Plan 模式只读；写入需要切换到 Build。")
    assert build_write_gate(controller("build", None, "ready"), object)()[1] == "goal_unconfirmed"
    assert build_write_gate(controller("build", "g", "waiting_user"), object)()[1] == "waiting_user"
    assert build_write_gate(controller("build", "g", "ready"), object)()[0] is True


def test_tools_without_gate_have_no_write_capability(workspace):
    names = [tool.name for tool in filesystem_tools(workspace)]

    assert names == ["fs_list", "fs_read"]


def test_tools_with_gate_include_write_edit_and_mutation_tools(workspace):
    names = [tool.name for tool in filesystem_tools(workspace, gate=allow)]

    assert names == ["fs_list", "fs_read", "fs_write", "fs_edit",
                     "fs_mkdir", "fs_move", "fs_delete"]


def test_denied_gate_blocks_every_write_before_touching_disk(workspace):
    tools = {tool.name: tool for tool in filesystem_tools(workspace, gate=lambda: deny("plan_read_only"))}

    created = payload(tools["fs_write"].handler(path="new.txt", content="x"))
    edited = payload(tools["fs_edit"].handler(path="notes.md", old_text="第一行",
                                              new_text="改", expected_sha256=sha("第一行\n第二行\n")))

    assert created["error"]["code"] == "plan_read_only"
    assert edited["error"]["code"] == "plan_read_only"
    assert not (workspace / "new.txt").exists()
    assert (workspace / "notes.md").read_bytes() == "第一行\n第二行\n".encode()


# ---------- 写入 ----------

def test_create_new_file(workspace):
    result = payload(FileSandbox(workspace).write_text("new.txt", "你好"))

    assert result["ok"] is True and result["created"] is True
    assert (workspace / "new.txt").read_bytes() == "你好".encode()
    assert result["sha256"] == sha("你好")


def test_creating_existing_file_requires_expected_version(workspace):
    result = payload(FileSandbox(workspace).write_text("notes.md", "新内容"))

    assert result["ok"] is False
    assert result["error"]["code"] == "missing_expected_version"
    assert result["error"]["current_sha256"] == sha("第一行\n第二行\n")
    assert (workspace / "notes.md").read_bytes() == "第一行\n第二行\n".encode()


def test_overwrite_with_matching_version(workspace):
    result = payload(FileSandbox(workspace).write_text(
        "notes.md", "全新内容", expected_sha256=sha("第一行\n第二行\n")))

    assert result["ok"] is True and result["created"] is False
    assert (workspace / "notes.md").read_bytes() == "全新内容".encode()


def test_overwrite_with_stale_version_is_refused(workspace):
    result = payload(FileSandbox(workspace).write_text("notes.md", "覆盖", expected_sha256="0" * 64))

    assert result["error"]["code"] == "stale_version"
    assert (workspace / "notes.md").read_bytes() == "第一行\n第二行\n".encode()


def test_version_for_absent_file_is_refused(workspace):
    result = payload(FileSandbox(workspace).write_text("ghost.txt", "x", expected_sha256="0" * 64))

    assert result["error"]["code"] == "unexpected_version"
    assert not (workspace / "ghost.txt").exists()


def test_sensitive_parent_missing_and_oversized_are_refused(workspace):
    sandbox = FileSandbox(workspace)

    assert payload(sandbox.write_text(".env", "TOKEN=2"))["error"]["code"] == "sensitive_path"
    assert payload(sandbox.write_text("nope/file.txt", "x"))["error"]["code"] == "parent_missing"
    assert payload(sandbox.write_text("big.txt", "x" * (filesystem.MAX_WRITE_BYTES + 1)))[
        "error"]["code"] == "file_too_large"
    assert not (workspace / "big.txt").exists()


def test_write_failure_keeps_original_file(workspace, monkeypatch):
    def boom(target, data):
        raise OSError("disk full")

    monkeypatch.setattr(filesystem, "_atomic_write", boom)
    result = payload(FileSandbox(workspace).write_text(
        "notes.md", "不该落盘", expected_sha256=sha("第一行\n第二行\n")))

    assert result["error"]["code"] == "write_failed"
    assert (workspace / "notes.md").read_bytes() == "第一行\n第二行\n".encode()


# ---------- 编辑 ----------

def test_edit_replaces_unique_text(workspace):
    result = payload(FileSandbox(workspace).edit_text(
        "notes.md", "第二行", "第二行（已改）", expected_sha256=sha("第一行\n第二行\n")))

    assert result["ok"] is True and result["replacements"] == 1
    assert (workspace / "notes.md").read_bytes() == "第一行\n第二行（已改）\n".encode()


def test_edit_requires_matching_version(workspace):
    result = payload(FileSandbox(workspace).edit_text(
        "notes.md", "第一行", "改", expected_sha256="0" * 64))

    assert result["error"]["code"] == "stale_version"


def test_edit_rejects_absent_or_ambiguous_text(workspace):
    sandbox = FileSandbox(workspace)
    note_hash = sha("第一行\n第二行\n")

    assert payload(sandbox.edit_text("notes.md", "没有这句", "x", expected_sha256=note_hash))[
        "error"]["code"] == "no_match"
    ambiguous = payload(sandbox.edit_text("dup.txt", "重复", "x", expected_sha256=sha("重复 重复")))
    assert ambiguous["error"]["code"] == "ambiguous_match"
    assert ambiguous["error"]["occurrences"] == 2
    assert (workspace / "dup.txt").read_bytes() == "重复 重复".encode()


def test_edit_refuses_sensitive_and_missing(workspace):
    sandbox = FileSandbox(workspace)

    assert payload(sandbox.edit_text(".env", "SECRET", "x", expected_sha256="0" * 64))[
        "error"]["code"] == "sensitive_path"
    assert payload(sandbox.edit_text("none.txt", "a", "b", expected_sha256="0" * 64))[
        "error"]["code"] == "not_found"