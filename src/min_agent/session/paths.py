"""应用私有路径：凭据与数据库默认放在工作区之外。

这些路径不进入仓库，也不进入模型可见的工作区；沙箱只挂工作区时，
它们不会被容器看到。路径选择遵循各平台惯例：
- Windows: %LOCALAPPDATA%\\agent-harness
- macOS:   ~/Library/Application Support/agent-harness
- Linux:   $XDG_DATA_HOME/agent-harness 或 ~/.local/share/agent-harness

凭据单独放在用户主目录下的 ``.agent-harness``，便于人工管理且不随
应用数据目录迁移。所有函数只返回路径，不创建目录、不读取内容。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

APP_NAME = "agent-harness"


def owner_id_for_database(database: str | Path) -> UUID:
    """与 HarnessApplication 一致的本地 owner 派生；迁移时必须同公式。"""
    return uuid5(NAMESPACE_URL, "agent-harness-local:" + str(Path(database).resolve()))


def _long_path(value: str) -> str:
    """Windows 8.3 短名展开；不存在时逐级向上找存在的父目录再展开。"""
    if os.name != "nt":
        return value
    path = Path(value)
    suffix: list[str] = []
    current = path
    while True:
        try:
            exists = current.exists()
        except OSError:
            exists = False
        if exists:
            break
        parent = current.parent
        if parent == current:
            return value
        suffix.append(current.name)
        current = parent
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(32768)
        length = ctypes.windll.kernel32.GetLongPathNameW(
            str(current), buffer, len(buffer))
        expanded = buffer.value if length else str(current)
    except Exception:
        expanded = str(current)
    if suffix:
        expanded = str(Path(expanded).joinpath(*reversed(suffix)))
    return expanded


def canonical_path(value: str | Path) -> str:
    """真实路径 + 8.3 展开 + 大小写归一，用于包含关系比较。"""
    path = Path(value).expanduser()
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        resolved = path.absolute()
    return os.path.normcase(_long_path(str(resolved)))


def _within(child: str | Path, parent: str | Path) -> bool:
    child_text, parent_text = canonical_path(child), canonical_path(parent)
    try:
        return os.path.commonpath([child_text, parent_text]) == parent_text
    except ValueError:
        # 不同盘符/不同命名空间，无法构成包含关系。
        return False


def app_data_home() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / APP_NAME
        return Path.home() / "AppData" / "Local" / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = os.environ.get("XDG_DATA_HOME")
    return Path(base) / APP_NAME if base else Path.home() / ".local" / "share" / APP_NAME


def data_dir() -> Path:
    return app_data_home() / "data"


def default_database() -> Path:
    return data_dir() / "workspace.sqlite3"


def credentials_dir() -> Path:
    return Path.home() / ".agent-harness"


def default_env_file() -> Path:
    return credentials_dir() / ".env"


def sensitive_external_paths() -> tuple[Path, ...]:
    """工作区之外、bash 规则层应默认拒绝的路径。

    这是给后续规则引擎/审批器用的声明，不是执行边界；在规则层落地前，
    宿主 ``--no-sandbox`` 模式仍能读到它们。
    """
    return (
        credentials_dir(),
        app_data_home(),
        Path.home() / "agent-harness-credentials.json",
    )


def ensure_workspace_outside_private(
    workspace_root: str | Path,
    *,
    database: str | Path | None = None,
    env_file: str | Path | None = None,
    credentials_file: str | Path | None = None,
) -> None:
    """fail-closed：工作区不能包含实际生效的数据库/凭据，也不能是 home/盘符根。

    只比较默认私有目录不够——`--database <workspace>/x.sqlite3` 或把 env 文件
    放进工作区时，容器/bash 仍能读到。这里把调用方实际传入的路径一起纳入比较。
    工作区不得等于或包含用户主目录、盘符根目录。
    """
    root = Path(workspace_root)
    home = Path.home()
    for private in (home, Path(root.anchor)):
        if not str(private):
            continue
        if _within(home, root) or _within(private, root):
            raise ValueError("workspace_root_contains_private_path")
    candidates: list[Path] = list(sensitive_external_paths())
    for value in (database, env_file, credentials_file):
        if value is not None:
            candidates.append(Path(value))
    for private in candidates:
        if _within(root, private) or _within(private, root):
            raise ValueError("workspace_root_contains_private_path")