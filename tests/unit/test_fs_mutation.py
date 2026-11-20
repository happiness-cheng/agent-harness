"""FS-MUTATE-001：建目录/移动/回收站删除、diff 预览与跨进程锁（离线）。"""

import hashlib
import json
import os
import threading
import time

import pytest

from min_agent.tools import filesystem
from min_agent.tools.filesystem import FileSandbox, filesystem_tools


def payload(text: str) -> dict:
    return json.loads(text)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def allow():
    return True, "allowed", ""


def deny(code="plan_read_only", message="拒绝"):
    return False, code, message


@pytest.fixture()
def workspace(tmp_path):
    (tmp_path / "notes.md").write_bytes("第一行\n第二行\n".encode())
    (tmp_path / "keep.txt").write_bytes(b"keep")
    (tmp_path / "empty").mkdir()
    (tmp_path / "full").mkdir()
    (tmp_path / "full" / "inner.txt").write_bytes(b"inner")
    (tmp_path / ".env").write_bytes(b"SECRET=1")
    return tmp_path


@pytest.fixture()
def sandbox(workspace):
    return FileSandbox(workspace)


# ---------- mkdir ----------

def test_mkdir_creates_single_directory(sandbox, workspace):
    result = payload(sandbox.make_directory("newdir"))

    assert result["ok"] is True
    assert result["created"] is True
    assert (workspace / "newdir").is_dir()


def test_mkdir_parents_and_missing_parent(sandbox, workspace):
    missing = payload(sandbox.make_directory("a/b"))
    nested = payload(sandbox.make_directory("a/b", parents=True))

    assert missing["error"]["code"] == "parent_missing"
    assert nested["ok"] is True
    assert (workspace / "a" / "b").is_dir()


def test_mkdir_refuses_existing_file_and_directory(sandbox):
    file_result = payload(sandbox.make_directory("keep.txt"))
    dir_result = payload(sandbox.make_directory("empty"))

    assert file_result["error"]["code"] == "already_exists"
    assert file_result["error"]["type"] == "file"
    assert dir_result["error"]["code"] == "already_exists"
    assert dir_result["error"]["type"] == "dir"


def test_mkdir_refuses_sensitive_reserved_and_outside(sandbox, workspace):
    sensitive = payload(sandbox.make_directory(".env.d"))
    reserved = payload(sandbox.make_directory(".adrian/trash/x"))
    parent_reference = payload(sandbox.make_directory("../escape"))
    absolute = payload(sandbox.make_directory(str(workspace / "absolute")))

    assert sensitive["error"]["code"] == "sensitive_path"
    assert reserved["error"]["code"] == "reserved_path"
    assert parent_reference["error"]["code"] == "invalid_path"
    assert absolute["error"]["code"] == "outside_workspace"
    assert not (workspace.parent / "escape").exists()
    assert not (workspace / "absolute").exists()


def test_mkdir_refuses_symlinked_path(sandbox, workspace):
    link = workspace / "linked"
    try:
        link.symlink_to(workspace / "empty", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("当前环境不允许创建符号链接")
    result = payload(sandbox.make_directory("linked/sub"))

    assert result["error"]["code"] == "symlink_path"
    assert not (workspace / "empty" / "sub").exists()


def test_mkdir_is_blocked_by_gate(workspace):
    tools = {tool.name: tool for tool in filesystem_tools(workspace, gate=lambda: deny())}
    result = payload(tools["fs_mkdir"].handler(path="blocked"))

    assert result["error"]["code"] == "plan_read_only"
    assert not (workspace / "blocked").exists()


# ---------- move ----------

def test_move_file_renames_and_reports_hash(sandbox, workspace):
    result = payload(sandbox.move("notes.md", "renamed.md", sha("第一行\n第二行\n")))

    assert result["ok"] is True
    assert result["type"] == "file"
    assert result["sha256"] == sha("第一行\n第二行\n")
    assert not (workspace / "notes.md").exists()
    assert (workspace / "renamed.md").read_bytes() == "第一行\n第二行\n".encode()


def test_move_requires_fresh_file_version(sandbox, workspace):
    missing = payload(sandbox.move("notes.md", "renamed.md"))
    stale = payload(sandbox.move("notes.md", "renamed.md", "0" * 64))

    assert missing["error"]["code"] == "missing_expected_version"
    assert stale["error"]["code"] == "stale_version"
    assert (workspace / "notes.md").exists()
    assert not (workspace / "renamed.md").exists()


def test_move_refuses_existing_destination_and_missing_parent(sandbox, workspace):
    exists = payload(sandbox.move("notes.md", "keep.txt", sha("第一行\n第二行\n")))
    parent = payload(sandbox.move("notes.md", "nope/renamed.md", sha("第一行\n第二行\n")))

    assert exists["error"]["code"] == "destination_exists"
    assert parent["error"]["code"] == "parent_missing"
    assert (workspace / "keep.txt").read_bytes() == b"keep"


def test_move_directory_and_reject_move_into_itself(sandbox, workspace):
    moved = payload(sandbox.move("empty", "renamed-empty"))
    inside = payload(sandbox.move("full", "full/inner"))

    assert moved["ok"] is True
    assert moved["type"] == "dir"
    assert (workspace / "renamed-empty").is_dir()
    assert inside["error"]["code"] == "invalid_destination"


def test_move_directory_rejects_version_token(sandbox):
    result = payload(sandbox.move("empty", "renamed-empty", "0" * 64))

    assert result["error"]["code"] == "unexpected_version"


def test_move_refuses_sensitive_reserved_and_outside(sandbox, workspace):
    sensitive = payload(sandbox.move(".env", "moved.env", sha("SECRET=1")))
    reserved = payload(sandbox.move("notes.md", ".adrian/notes.md", sha("第一行\n第二行\n")))
    parent_reference = payload(sandbox.move("notes.md", "../escape.md", sha("第一行\n第二行\n")))
    absolute = payload(sandbox.move("notes.md", str(workspace / "abs.md"), sha("第一行\n第二行\n")))

    assert sensitive["error"]["code"] == "sensitive_path"
    assert reserved["error"]["code"] == "reserved_path"
    assert parent_reference["error"]["code"] == "invalid_path"
    assert absolute["error"]["code"] == "outside_workspace"
    assert (workspace / "notes.md").exists()


def test_move_is_blocked_by_gate(workspace):
    tools = {tool.name: tool for tool in filesystem_tools(workspace, gate=lambda: deny())}
    result = payload(tools["fs_move"].handler(source="notes.md", destination="x.md",
                                              expected_sha256=sha("第一行\n第二行\n")))

    assert result["error"]["code"] == "plan_read_only"
    assert (workspace / "notes.md").exists()


# ---------- delete ----------

def test_delete_moves_file_into_trash_and_can_be_restored(sandbox, workspace):
    result = payload(sandbox.delete("notes.md", sha("第一行\n第二行\n")))

    assert result["ok"] is True
    assert result["type"] == "file"
    assert not (workspace / "notes.md").exists()
    trashed = workspace / result["trashed_path"]
    assert trashed.read_bytes() == "第一行\n第二行\n".encode()
    assert result["trashed_path"].startswith(".adrian/trash/")

    restored = payload(sandbox.move(result["trashed_path"], "notes.md", result["sha256"]))
    assert restored["ok"] is True
    assert (workspace / "notes.md").read_bytes() == "第一行\n第二行\n".encode()


def test_delete_requires_fresh_file_version(sandbox, workspace):
    missing = payload(sandbox.delete("notes.md"))
    stale = payload(sandbox.delete("notes.md", "0" * 64))

    assert missing["error"]["code"] == "missing_expected_version"
    assert stale["error"]["code"] == "stale_version"
    assert (workspace / "notes.md").exists()


def test_delete_empty_directory_but_not_non_empty(sandbox, workspace):
    empty = payload(sandbox.delete("empty"))
    full = payload(sandbox.delete("full"))

    assert empty["ok"] is True
    assert empty["type"] == "dir"
    assert not (workspace / "empty").exists()
    assert full["error"]["code"] == "directory_not_empty"
    assert full["error"]["entries"] == 1
    assert (workspace / "full" / "inner.txt").exists()


def test_delete_refuses_sensitive_root_and_reserved(sandbox, workspace):
    sensitive = payload(sandbox.delete(".env", sha("SECRET=1")))
    root = payload(sandbox.delete("."))
    reserved = payload(sandbox.delete(".adrian/trash", None))

    assert sensitive["error"]["code"] == "sensitive_path"
    assert root["error"]["code"] in {"invalid_path", "reserved_path"}
    assert reserved["error"]["code"] == "reserved_path"
    assert (workspace / ".env").exists()


def test_delete_is_blocked_by_gate(workspace):
    tools = {tool.name: tool for tool in filesystem_tools(workspace, gate=lambda: deny())}
    result = payload(tools["fs_delete"].handler(path="notes.md",
                                                expected_sha256=sha("第一行\n第二行\n")))

    assert result["error"]["code"] == "plan_read_only"
    assert (workspace / "notes.md").exists()


# ---------- dry_run diff ----------

def test_dry_run_write_new_file_returns_diff_without_touching_disk(sandbox, workspace):
    result = payload(sandbox.write_text("new.md", "hello\nworld\n", dry_run=True))

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["existed"] is False
    assert result["added"] == 2
    assert "+hello" in result["diff"]
    assert not (workspace / "new.md").exists()
    assert not (workspace / ".adrian").exists()


def test_dry_run_write_existing_file_keeps_content_and_mtime(sandbox, workspace):
    before = (workspace / "notes.md").read_bytes()
    before_mtime = (workspace / "notes.md").stat().st_mtime_ns
    result = payload(sandbox.write_text("notes.md", "替换整个文件\n",
                                        sha("第一行\n第二行\n"), dry_run=True))

    assert result["ok"] is True
    assert result["current_sha256"] == sha("第一行\n第二行\n")
    assert result["version_checked"] is True
    assert "-第一行" in result["diff"]
    assert (workspace / "notes.md").read_bytes() == before
    assert (workspace / "notes.md").stat().st_mtime_ns == before_mtime


def test_dry_run_write_rejects_stale_version(sandbox, workspace):
    result = payload(sandbox.write_text("notes.md", "x\n", "0" * 64, dry_run=True))

    assert result["error"]["code"] == "stale_version"
    assert (workspace / "notes.md").read_bytes() == "第一行\n第二行\n".encode()


def test_dry_run_edit_previews_and_does_not_write(sandbox, workspace):
    result = payload(sandbox.edit_text("notes.md", "第二行", "第二行已修改",
                                       sha("第一行\n第二行\n"), dry_run=True))

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert "+第二行已修改" in result["diff"]
    assert (workspace / "notes.md").read_bytes() == "第一行\n第二行\n".encode()


def test_dry_run_edit_keeps_no_match_and_ambiguous_refusals(sandbox, workspace):
    (workspace / "dup.txt").write_bytes("重复 重复".encode())

    missing = payload(sandbox.edit_text("notes.md", "不存在", "x",
                                        sha("第一行\n第二行\n"), dry_run=True))
    ambiguous = payload(sandbox.edit_text("dup.txt", "重复", "x", sha("重复 重复"), dry_run=True))

    assert missing["error"]["code"] == "no_match"
    assert ambiguous["error"]["code"] == "ambiguous_match"
    assert ambiguous["error"]["occurrences"] == 2
    assert (workspace / "dup.txt").read_text(encoding="utf-8") == "重复 重复"


def test_diff_is_bounded_for_large_change(sandbox, workspace):
    big = "\n".join(f"line {index}" for index in range(600)) + "\n"
    result = payload(sandbox.write_text("big.md", big, dry_run=True))

    assert result["ok"] is True
    assert len(result["diff"].splitlines()) <= 200
    assert result["truncated"] is True


# ---------- cross-process lock ----------

def test_write_waits_for_lock_and_times_out(monkeypatch, sandbox, workspace):
    monkeypatch.setattr(filesystem, "LOCK_TIMEOUT_SECONDS", 0.2)
    target = workspace / "notes.md"
    results: list[dict] = []

    def attempt() -> None:
        results.append(payload(sandbox.write_text("notes.md", "并发写入\n",
                                                  sha("第一行\n第二行\n"))))

    with sandbox._locked(target):
        worker = threading.Thread(target=attempt)
        worker.start()
        worker.join(timeout=5)

    assert results and results[0]["error"]["code"] == "lock_timeout"
    assert results[0]["error"]["holder"]["pid"] == os.getpid()
    assert (workspace / "notes.md").read_bytes() == "第一行\n第二行\n".encode()
    assert not sandbox._lock_path(target).exists()


def test_stale_lock_is_taken_over(monkeypatch, sandbox, workspace):
    monkeypatch.setattr(filesystem, "LOCK_TIMEOUT_SECONDS", 0.5)
    target = workspace / "notes.md"
    lock_path = sandbox._lock_path(target)
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text('{"pid": 999999}', encoding="utf-8")
    old = time.time() - 120
    os.utime(lock_path, (old, old))

    result = payload(sandbox.write_text("notes.md", "接管成功\n", sha("第一行\n第二行\n")))

    assert result["ok"] is True
    assert (workspace / "notes.md").read_text(encoding="utf-8") == "接管成功\n"


def test_lock_is_released_after_failed_version_check(sandbox, workspace):
    result = payload(sandbox.write_text("notes.md", "x\n", "0" * 64))

    assert result["error"]["code"] == "stale_version"
    assert not sandbox._lock_path(workspace / "notes.md").exists()


def test_two_sequential_writers_with_same_version_do_not_both_succeed(sandbox, workspace):
    first = payload(sandbox.write_text("notes.md", "A\n", sha("第一行\n第二行\n")))
    second = payload(sandbox.write_text("notes.md", "B\n", sha("第一行\n第二行\n")))

    assert first["ok"] is True
    assert second["error"]["code"] == "stale_version"
    assert (workspace / "notes.md").read_text(encoding="utf-8") == "A\n"


def test_trash_directory_is_hidden_from_root_listing(sandbox, workspace):
    sandbox.delete("notes.md", sha("第一行\n第二行\n"))
    listed = payload(sandbox.list_dir("."))
    names = [entry["name"] for entry in listed["entries"]]

    assert ".adrian" not in names
    assert "notes.md" not in names
