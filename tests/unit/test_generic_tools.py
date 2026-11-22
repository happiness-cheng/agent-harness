"""GENERIC-TOOLS-001：read/write/edit/bash 四个通用工具（离线）。"""

import json
import os

import pytest

from min_agent.tools.generic import (
    MAX_BASH_OUTPUT_BYTES,
    GenericWorkspace,
    _git_bash,
)


@pytest.fixture()
def workspace(tmp_path):
    (tmp_path / "lines.txt").write_bytes("l1\nl2\nl3\nl4\n".encode())
    (tmp_path / ".env").write_bytes(b"SECRET=1")
    return tmp_path


def payload(text: str) -> dict:
    return json.loads(text)


def requires_bash():
    if _git_bash() is None:
        pytest.skip("本机没有可用的 Git Bash")


# ---------- read ----------

def test_read_slices_by_line_and_reports_range(workspace):
    result = payload(GenericWorkspace(workspace).read("lines.txt", offset=2, limit=2))

    assert result["ok"] is True
    assert result["content"] == "l2\nl3\n"
    assert (result["start_line"], result["end_line"], result["total_lines"]) == (2, 3, 4)
    assert result["truncated"] is True
    assert result["sha256"]


def test_read_defaults_to_whole_file(workspace):
    result = payload(GenericWorkspace(workspace).read("lines.txt"))

    assert result["content"] == "l1\nl2\nl3\nl4\n"
    assert result["truncated"] is False


def test_read_beyond_end_returns_empty_without_error(workspace):
    result = payload(GenericWorkspace(workspace).read("lines.txt", offset=9, limit=2))

    assert result["ok"] is True
    assert result["content"] == ""
    assert result["start_line"] == 5
    assert result["end_line"] == 4
    assert result["truncated"] is False


def test_read_rejects_escape_and_sensitive_paths(workspace):
    workspace_api = GenericWorkspace(workspace)

    assert payload(workspace_api.read("../outside.txt"))["error"]["code"] == "outside_workspace"
    assert payload(workspace_api.read(".env"))["error"]["code"] == "sensitive_path"


def test_read_directory_points_to_bash(workspace):
    result = payload(GenericWorkspace(workspace).read("."))

    assert result["error"]["code"] == "is_directory"
    assert "bash ls" in result["error"]["message"]


# ---------- write ----------

def test_write_creates_then_overwrites_without_version(workspace):
    workspace_api = GenericWorkspace(workspace)

    created = payload(workspace_api.write("new.txt", "first"))
    overwritten = payload(workspace_api.write("new.txt", "second"))

    assert created["created"] is True
    assert overwritten["created"] is False
    assert (workspace / "new.txt").read_text(encoding="utf-8") == "second"


def test_write_rejects_missing_parent(workspace):
    result = payload(GenericWorkspace(workspace).write("missing/child.txt", "x"))

    assert result["error"]["code"] == "parent_missing"
    assert not (workspace / "missing").exists()


# ---------- edit ----------

def test_edit_replaces_unique_match(workspace):
    result = payload(GenericWorkspace(workspace).edit(
        "lines.txt", "l2\nl3", "changed"))

    assert result["ok"] is True
    assert (workspace / "lines.txt").read_text(encoding="utf-8") == "l1\nchanged\nl4\n"


def test_edit_rejects_ambiguous_and_missing_matches(workspace):
    (workspace / "dup.txt").write_text("same same", encoding="utf-8")
    workspace_api = GenericWorkspace(workspace)

    ambiguous = payload(workspace_api.edit("dup.txt", "same", "x"))
    missing = payload(workspace_api.edit("lines.txt", "nope", "x"))

    assert ambiguous["error"]["code"] == "ambiguous_match"
    assert missing["error"]["code"] == "no_match"


# ---------- bash ----------

def test_bash_runs_in_workspace_and_returns_stdout(workspace):
    requires_bash()

    result = payload(GenericWorkspace(workspace).bash("pwd; echo ok"))

    assert result["ok"] is True
    assert result["exit_code"] == 0
    assert "ok" in result["stdout"]
    assert str(workspace).split("\\")[-1] in result["stdout"]


def test_bash_returns_exit_code_and_stderr(workspace):
    requires_bash()

    result = payload(GenericWorkspace(workspace).bash("echo bad >&2; exit 7"))

    assert result["ok"] is True
    assert result["exit_code"] == 7
    assert "bad" in result["stderr"]


def test_bash_timeout_kills_child_process(workspace):
    requires_bash()

    result = payload(GenericWorkspace(workspace).bash("sleep 5", timeout_seconds=1))

    assert result["ok"] is False
    assert result["error"]["code"] == "timeout"
    assert result["error"]["duration_ms"] < 4000


def test_bash_caps_large_output(workspace):
    requires_bash()

    result = payload(GenericWorkspace(workspace).bash(
        "python -c \"print('x' * 200000)\""))

    assert result["truncated"] is True
    assert len(result["stdout"].encode("utf-8")) <= MAX_BASH_OUTPUT_BYTES + 64


def test_bash_scrubs_secret_environment(workspace, monkeypatch):
    requires_bash()
    monkeypatch.setenv("TEST_API_KEY", "should-not-leak")

    result = payload(GenericWorkspace(workspace).bash(
        "echo \"${TEST_API_KEY:-missing}\""))

    assert "should-not-leak" not in result["stdout"]
    assert "missing" in result["stdout"]