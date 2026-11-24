"""FS-READ-001：只读文件工具的路径、上限与拒绝分支（离线，无外部调用）。"""

import json
import os
from pathlib import Path

import pytest

from min_agent.tools import filesystem
from min_agent.tools.filesystem import FileSandbox, filesystem_tools


def payload(text: str) -> dict:
    return json.loads(text)


@pytest.fixture()
def workspace(tmp_path):
    # 用 write_bytes 避免 Windows 文本模式把 \n 改写成 \r\n，否则字节数断言会漂移
    (tmp_path / "notes.md").write_bytes("# 笔记\n这是内容。".encode())
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "deep.txt").write_bytes("深层文件".encode())
    (tmp_path / "binary.bin").write_bytes(b"\x00\x01\x02")
    (tmp_path / "latin1.txt").write_bytes("café".encode("latin-1"))
    (tmp_path / ".env").write_text("SECRET=1", encoding="utf-8")
    (tmp_path / "credentials.json").write_text('{"k":"v"}', encoding="utf-8")
    (tmp_path / "app.sqlite3").write_bytes(b"SQLite format 3\x00")
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("根外内容", encoding="utf-8")
    return tmp_path


def test_read_returns_content_and_freshness(workspace):
    sandbox = FileSandbox(workspace)
    result = payload(sandbox.read_text("notes.md"))

    assert result["ok"] is True
    assert result["content"].startswith("# 笔记")
    assert result["size"] == len("# 笔记\n这是内容。".encode())
    assert len(result["sha256"]) == 64
    assert result["mtime_ns"] > 0


def test_list_returns_sorted_entries_and_marks_sensitive(workspace):
    sandbox = FileSandbox(workspace)
    result = payload(sandbox.list_dir("."))

    names = [entry["name"] for entry in result["entries"]]
    assert names == sorted(names, key=str.lower)
    sensitive = {entry["name"] for entry in result["entries"] if entry["sensitive"]}
    assert {".env", "credentials.json", "app.sqlite3"} <= sensitive
    assert result["truncated"] is False


def test_list_truncates_at_limit(workspace, monkeypatch):
    monkeypatch.setattr(filesystem, "MAX_LIST_ENTRIES", 2)
    result = payload(FileSandbox(workspace).list_dir("."))

    assert len(result["entries"]) == 2
    assert result["truncated"] is True


@pytest.mark.parametrize("path", ["../outside.txt", "sub/../../outside.txt", "/etc/passwd"])
def test_paths_outside_workspace_are_refused(workspace, path):
    result = payload(FileSandbox(workspace).read_text(path))

    assert result["ok"] is False
    assert result["error"]["code"] == "outside_workspace"


def test_symlink_escape_is_refused(workspace):
    link = workspace / "escape.txt"
    try:
        os.symlink(workspace.parent / "outside.txt", link)
    except (OSError, NotImplementedError):
        pytest.skip("当前环境无法创建符号链接")
    result = payload(FileSandbox(workspace).read_text("escape.txt"))

    assert result["ok"] is False
    assert result["error"]["code"] == "outside_workspace"


def test_unreadable_reparse_point_is_refused_not_crash(workspace, monkeypatch):
    """容器/跨命名空间符号链接在 Windows 上可能让 exists() 抛 OSError。"""
    (workspace / "opaque.txt").write_text("x", encoding="utf-8")
    original = Path.exists

    def exists(self):
        if self.name == "opaque.txt":
            raise OSError(None, "cannot access the file", None, 1920)
        return original(self)

    monkeypatch.setattr(Path, "exists", exists)

    result = payload(FileSandbox(workspace).read_text("opaque.txt"))

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_path"


def test_missing_directory_and_special_files_are_refused(workspace):
    sandbox = FileSandbox(workspace)

    assert payload(sandbox.read_text("missing.txt"))["error"]["code"] == "not_found"
    assert payload(sandbox.read_text("sub"))["error"]["code"] == "is_directory"
    assert payload(sandbox.read_text("binary.bin"))["error"]["code"] == "binary_file"
    assert payload(sandbox.read_text("latin1.txt"))["error"]["code"] == "not_utf8"


def test_sensitive_files_are_refused(workspace):
    sandbox = FileSandbox(workspace)

    for name in (".env", "credentials.json", "app.sqlite3"):
        result = payload(sandbox.read_text(name))
        assert result["ok"] is False
        assert result["error"]["code"] == "sensitive_path", name


def test_oversized_file_is_refused_without_truncation(workspace, monkeypatch):
    monkeypatch.setattr(filesystem, "MAX_READ_BYTES", 8)
    (workspace / "big.txt").write_text("0123456789", encoding="utf-8")

    result = payload(FileSandbox(workspace).read_text("big.txt"))

    assert result["ok"] is False
    assert result["error"]["code"] == "file_too_large"
    assert "content" not in result
    assert result["error"]["size"] == 10 and result["error"]["limit"] == 8


def test_invalid_root_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        FileSandbox(tmp_path / "not-there")


def test_tools_are_registered_with_expected_schema(workspace):
    tools = filesystem_tools(workspace)
    names = [tool.name for tool in tools]

    assert names == ["fs_list", "fs_read"]
    assert tools[0].parameters.model_json_schema()["type"] == "object"
    assert payload(tools[1].handler(path="notes.md"))["ok"] is True