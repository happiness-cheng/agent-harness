"""GENERIC-TOOLS-001：私有路径必须落在工作区之外。"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from min_agent.application import HarnessApplication
from min_agent.session.paths import (
    canonical_path,
    data_dir,
    default_database,
    default_env_file,
    ensure_workspace_outside_private,
    owner_id_for_database,
    sensitive_external_paths,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_default_database_and_env_are_outside_repo():
    for path in (default_database(), default_env_file(), data_dir()):
        assert not path.resolve().is_relative_to(REPO_ROOT), path


def test_sensitive_external_paths_are_outside_repo():
    paths = sensitive_external_paths()

    assert paths
    for path in paths:
        assert not path.resolve().is_relative_to(REPO_ROOT), path


def test_default_database_name_is_stable():
    assert default_database().name == "workspace.sqlite3"
    assert default_env_file().name == ".env"


def test_workspace_cannot_be_the_user_home(tmp_path):
    with pytest.raises(ValueError, match="workspace_root_contains_private_path"):
        HarnessApplication(tmp_path / "home-workspace.sqlite3", workspace_root=Path.home())


def test_workspace_cannot_be_inside_the_data_dir(tmp_path):
    with pytest.raises(ValueError, match="workspace_root_contains_private_path"):
        ensure_workspace_outside_private(data_dir() / "project")


def test_workspace_cannot_contain_the_actual_database(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = workspace / "workspace.sqlite3"

    with pytest.raises(ValueError, match="workspace_root_contains_private_path"):
        ensure_workspace_outside_private(workspace, database=database)
    with pytest.raises(ValueError, match="workspace_root_contains_private_path"):
        HarnessApplication(database, workspace_root=workspace)


def test_workspace_cannot_contain_the_env_file(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    env_file = workspace / ".env"

    with pytest.raises(ValueError, match="workspace_root_contains_private_path"):
        ensure_workspace_outside_private(workspace, env_file=env_file)


def test_workspace_cannot_be_drive_root_or_contain_home():
    anchor = Path(Path.home().anchor)
    with pytest.raises(ValueError, match="workspace_root_contains_private_path"):
        ensure_workspace_outside_private(anchor)
    with pytest.raises(ValueError, match="workspace_root_contains_private_path"):
        ensure_workspace_outside_private(Path.home().parent)


def test_workspace_check_is_case_insensitive_on_windows():
    if os.name != "nt":
        pytest.skip("Windows 专属路径语义")
    with pytest.raises(ValueError, match="workspace_root_contains_private_path"):
        ensure_workspace_outside_private(str(Path.home()).upper())


def test_workspace_check_expands_windows_short_name():
    if os.name != "nt":
        pytest.skip("Windows 专属路径语义")
    import ctypes

    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetShortPathNameW(
        str(Path.home()), buffer, len(buffer))
    if not length:
        pytest.skip("当前系统没有 8.3 短名")
    short = buffer.value
    if short.lower() == str(Path.home()).lower():
        pytest.skip("当前系统未启用 8.3 短名")
    with pytest.raises(ValueError, match="workspace_root_contains_private_path"):
        ensure_workspace_outside_private(short)


def test_canonical_path_expands_nonexistent_child_via_parent(monkeypatch, tmp_path):
    if os.name != "nt":
        pytest.skip("Windows 专属路径语义")
    import ctypes

    calls: list[str] = []

    def fake(path, buffer, size):
        calls.append(path)
        buffer.value = r"C:\LONGPATH"
        return len(buffer.value)

    monkeypatch.setattr(ctypes.windll.kernel32, "GetLongPathNameW", fake)

    result = canonical_path(tmp_path / "missing" / "child.txt")

    assert result == os.path.normcase(r"C:\LONGPATH\missing\child.txt")
    assert calls


def test_canonical_path_falls_back_when_long_name_unavailable(monkeypatch, tmp_path):
    if os.name != "nt":
        pytest.skip("Windows 专属路径语义")
    import ctypes

    def fake(path, buffer, size):
        return 0

    monkeypatch.setattr(ctypes.windll.kernel32, "GetLongPathNameW", fake)

    result = canonical_path(tmp_path / "missing" / "child.txt")

    assert result == os.path.normcase(str((tmp_path / "missing" / "child.txt").resolve()))


def test_cli_rejects_database_inside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "min_agent.web",
         "--demo", "--workspace-root", str(workspace),
         "--database", str(workspace / "workspace.sqlite3"),
         "--port", "8799", "--no-browser"],
        cwd=str(REPO_ROOT), env=dict(os.environ, PYTHONPATH=str(REPO_ROOT / "src")),
        capture_output=True, text=True, timeout=30,
    )

    assert result.returncode == 2
    assert "workspace_root_contains_private_path" in (result.stderr + result.stdout)
    assert not (workspace / "workspace.sqlite3").exists()


def test_missing_database_notice_names_path(tmp_path):
    from min_agent.web.__main__ import _database_notice

    missing = tmp_path / "new.sqlite3"
    notice = _database_notice(missing)

    assert notice is not None
    assert str(missing.resolve()) in notice
    assert "旧会话不会自动出现" in notice

    missing.write_text("", encoding="utf-8")
    assert _database_notice(missing) is None


def test_owner_id_helper_matches_application(tmp_path):
    database = tmp_path / "owner.sqlite3"
    app = HarnessApplication(database, memory_mode="off")
    try:
        assert app.owner_id == owner_id_for_database(database)
    finally:
        app.close()