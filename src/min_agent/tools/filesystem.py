"""工作区文件工具：读、写、建目录、移动、回收站式删除，全部限制在根内。

设计取自 deepseek-harness 的文件系统语义：
- 先 stat 再读，目录/特殊文件在读之前拒绝；
- 读写有硬上限，超限报错而**不截断**；
- 返回 sha256/mtime，写入/移动/删除前做“先读后改”的新鲜度校验；
- 删除是**移到回收站**（工作区内 `.adrian/trash/`），不是硬删除；
- 写类操作有跨进程锁，版本不符或锁超时都明确报错，不静默覆盖。

本批仍**不提供**递归删除、shell、全盘访问，也不允许覆盖式移动。
"""

import difflib
import errno
import hashlib
import json
import os
import secrets
import tempfile
import time
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from min_agent.tools.registry import ToolDefinition

MAX_LIST_ENTRIES = 500
MAX_READ_BYTES = 256 * 1024
MAX_WRITE_BYTES = 1024 * 1024
MAX_DELETE_BYTES = 64 * 1024 * 1024
MAX_PATH_LENGTH = 1024
MAX_DIFF_LINES = 200
MAX_DIFF_CHARS = 8000
LOCK_TIMEOUT_SECONDS = 3.0
LOCK_STALE_SECONDS = 30.0
LOCK_POLL_SECONDS = 0.05
RESERVED_DIR = ".adrian"

_SENSITIVE_SUFFIXES = (".sqlite3", ".db", ".pem", ".key", ".p12", ".pfx")
_SENSITIVE_PARTS = {".git", ".ssh"}
_SENSITIVE_NAME_PREFIXES = (".env", "credentials", "id_rsa", "id_ed25519")
_PATH_ERRNOS = {errno.ELOOP, errno.ENAMETOOLONG, errno.EINVAL, errno.ENOTDIR, errno.EISDIR}
_PATH_WINERRORS = {1920, 1921, 123, 161}
# Windows 32/33 是 sharing/lock violation；Python 会把它映射成 PermissionError，
# 但它不是权限问题，也不是路径无效，应归 I/O 错误。
_BUSY_WINERRORS = {32, 33}


def _is_path_error(error: OSError) -> bool:
    """只把已知的“路径无效/跨命名空间”错误归为 invalid_path。

    ENOSPC、EIO、Windows sharing violation（32）等是 I/O 错误，不是路径错误；
    混在一起会让模型错误地换路径重试，也会误导审计。
    """
    if getattr(error, "winerror", None) in _PATH_WINERRORS:
        return True
    return getattr(error, "errno", None) in _PATH_ERRNOS


def _os_failure(error: OSError, action: str) -> str:
    details = {
        "errno": getattr(error, "errno", None),
        "winerror": getattr(error, "winerror", None),
    }
    if _is_path_error(error):
        return _error("invalid_path", f"路径无法安全访问；{action}", **details)
    return _error("io_error", f"I/O 错误，{action}", **details)


def _write_failure(error: OSError, action: str) -> str:
    """写失败：共享冲突/磁盘满归 io_error，真权限问题归 permission_denied。"""
    if isinstance(error, PermissionError) \
            and getattr(error, "winerror", None) not in _BUSY_WINERRORS:
        return _error("permission_denied", f"没有权限{action}。")
    return _os_failure(error, action)


class FsListArguments(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    path: str = Field(default=".", min_length=1, max_length=MAX_PATH_LENGTH,
                      description="相对工作区根的路径；用 . 表示根目录。")


class FsReadArguments(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    path: str = Field(min_length=1, max_length=MAX_PATH_LENGTH,
                      description="相对工作区根的文本文件路径；不接受绝对路径或 ..。")


class FsWriteArguments(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    path: str = Field(min_length=1, max_length=MAX_PATH_LENGTH,
                      description="相对工作区根的目标路径；不接受绝对路径或 ..。")
    content: str = Field(max_length=MAX_WRITE_BYTES,
                         description="要写入的完整文本；新建时必须是新文件，覆盖时必须提供 expected_sha256。")
    expected_sha256: str | None = Field(default=None, min_length=64, max_length=64,
                                        pattern=r"^[0-9a-f]{64}$",
                                        description="上次 fs_read 返回的 sha256；新建文件时省略，覆盖已有文件时必填。")
    dry_run: bool = Field(default=False,
                          description="为 true 时只返回 unified diff 预览，不写文件。")


class FsEditArguments(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    path: str = Field(min_length=1, max_length=MAX_PATH_LENGTH,
                      description="要修改的文本文件，相对工作区根。")
    old_text: str = Field(min_length=1, max_length=MAX_WRITE_BYTES,
                          description="要被替换的原文，必须在文件中唯一出现。")
    new_text: str = Field(max_length=MAX_WRITE_BYTES, description="替换后的文本，可为空字符串。")
    expected_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$",
                                 description="上次 fs_read 返回的 sha256；不匹配说明文件已被改动，拒绝写入。")
    dry_run: bool = Field(default=False,
                          description="为 true 时只返回 unified diff 预览，不写文件。")


class FsMkdirArguments(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    path: str = Field(min_length=1, max_length=MAX_PATH_LENGTH,
                      description="要创建的目录，相对工作区根。")
    parents: bool = Field(default=False,
                          description="父目录缺失时是否一并创建；false 时直接报 parent_missing。")


class FsMoveArguments(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    source: str = Field(min_length=1, max_length=MAX_PATH_LENGTH,
                        description="要移动的源路径，相对工作区根。")
    destination: str = Field(min_length=1, max_length=MAX_PATH_LENGTH,
                             description="目标路径，相对工作区根；目标已存在时拒绝，不覆盖。")
    expected_sha256: str | None = Field(default=None, min_length=64, max_length=64,
                                        pattern=r"^[0-9a-f]{64}$",
                                        description="源文件上次 fs_read 的 sha256；移动文件时必填，移动目录时省略。")


class FsDeleteArguments(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    path: str = Field(min_length=1, max_length=MAX_PATH_LENGTH,
                      description="要删除的路径，相对工作区根。")
    expected_sha256: str | None = Field(default=None, min_length=64, max_length=64,
                                        pattern=r"^[0-9a-f]{64}$",
                                        description="待删文件上次 fs_read 的 sha256；删除文件时必填，空目录时省略。")


def _error(code: str, message: str, **details: object) -> str:
    return json.dumps({"ok": False, "error": {"code": code, "message": message, **details}},
                      ensure_ascii=False)


def _ok(payload: dict[str, object]) -> str:
    return json.dumps({"ok": True, **payload}, ensure_ascii=False)


def _bounded_diff(relative: str, before: str, after: str) -> tuple[str, int, int, bool]:
    """生成有界 unified diff，返回 (文本, 新增行数, 删除行数, 是否被截断)。"""
    lines = list(difflib.unified_diff(before.splitlines(), after.splitlines(),
                                      fromfile=f"a/{relative}", tofile=f"b/{relative}",
                                      lineterm="", n=3))
    added = sum(1 for line in lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in lines if line.startswith("-") and not line.startswith("---"))
    truncated = False
    if len(lines) > MAX_DIFF_LINES:
        lines = lines[:MAX_DIFF_LINES]
        truncated = True
    text = "\n".join(lines)
    if len(text) > MAX_DIFF_CHARS:
        text = text[:MAX_DIFF_CHARS]
        truncated = True
    return text, added, removed, truncated


class LockTimeout(RuntimeError):
    """等锁超时；holder 是当时锁文件里的占用者信息。"""

    def __init__(self, holder: dict[str, object] | None) -> None:
        super().__init__("lock_timeout")
        self.holder = holder


class FileSandbox:
    """把用户给的相对路径解析成根内绝对路径；任何逃逸都拒绝。"""

    def __init__(self, root: str | Path) -> None:
        root_path = Path(root).resolve()
        if not root_path.is_dir():
            raise ValueError("workspace_root_invalid")
        self.root = root_path

    def resolve(self, value: str) -> tuple[Path | None, str | None]:
        """返回 (路径, 错误)。绝对路径、..、符号链接逃逸都会落到根外而被拒绝。"""
        if not isinstance(value, str) or not value.strip():
            return None, _error("path_required", "必须提供相对路径。")
        if "\x00" in value:
            return None, _error("invalid_path", "路径包含非法字符。")
        try:
            candidate = (self.root / value).resolve()
        except (OSError, RuntimeError):
            return None, _error("invalid_path", "路径无法解析。")
        if candidate != self.root and self.root not in candidate.parents:
            return None, _error("outside_workspace", "路径超出工作区，已拒绝。")
        return candidate, None

    def _symlink_in_path(self, value: str) -> bool:
        """逐段检查**未展开**的路径是否含符号链接（resolve 会把它展开成目标）。"""
        current = self.root
        for part in Path(value).parts:
            if part in {"", "."}:
                continue
            current = current / part
            try:
                if os.path.islink(current):
                    return True
            except OSError:
                return True
        return False

    def _prepare_mutation(self, value: str) -> tuple[Path | None, str | None]:
        """写类操作的统一前置校验：拒绝绝对路径、..、符号链接与根外路径。"""
        if not isinstance(value, str) or not value.strip():
            return None, _error("path_required", "必须提供相对路径。")
        if "\x00" in value:
            return None, _error("invalid_path", "路径包含非法字符。")
        parts = Path(value).parts
        if Path(value).is_absolute() or Path(value).drive:
            return None, _error("outside_workspace", "写类操作只接受相对工作区根的路径。")
        if ".." in parts:
            return None, _error("invalid_path", "写类操作的路径不允许包含 ..。")
        if self._symlink_in_path(value):
            return None, _error("symlink_path", "路径包含符号链接，写类操作拒绝，避免写到链接目标。")
        return self.resolve(value)

    def _reserved(self, path: Path) -> bool:
        relative = path.relative_to(self.root)
        return any(part.lower() == RESERVED_DIR for part in relative.parts)

    def _in_trash(self, path: Path) -> bool:
        try:
            relative = path.relative_to(self.root / RESERVED_DIR / "trash")
        except ValueError:
            return False
        return bool(relative.parts)

    def _lock_path(self, target: Path) -> Path:
        relative = target.relative_to(self.root).as_posix()
        key = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:32]
        return self.root / RESERVED_DIR / "locks" / f"{key}.lock"

    @staticmethod
    def _lock_holder(lock_path: Path) -> dict[str, object] | None:
        try:
            value = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    @contextmanager
    def _locked(self, target: Path):
        """跨进程写锁：O_EXCL 抢占 + 陈旧锁接管 + 超时明确失败。"""
        lock_path = self._lock_path(target)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                handle = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                try:
                    stale = time.time() - lock_path.stat().st_mtime > LOCK_STALE_SECONDS
                except OSError:
                    stale = False
                if stale:
                    try:
                        os.unlink(lock_path)
                    except OSError:
                        pass
                    continue
                if time.monotonic() >= deadline:
                    raise LockTimeout(self._lock_holder(lock_path)) from None
                time.sleep(LOCK_POLL_SECONDS)
        try:
            payload = json.dumps({"pid": os.getpid(), "target": target.name,
                                  "time": datetime.now(UTC).isoformat()}, ensure_ascii=False)
            os.write(handle, payload.encode("utf-8"))
            os.close(handle)
            yield
        finally:
            try:
                os.unlink(lock_path)
            except OSError:
                pass

    @contextmanager
    def _lock_group(self, targets: list[Path]):
        """按路径排序依次加锁，避免两个移动操作交叉等待。"""
        with ExitStack() as stack:
            for target in sorted(set(targets), key=lambda item: item.as_posix().lower()):
                stack.enter_context(self._locked(target))
            yield

    def _sensitive(self, path: Path) -> bool:
        relative = path.relative_to(self.root)
        if any(part.lower() in _SENSITIVE_PARTS for part in relative.parts[:-1]):
            return True
        name = relative.name.lower()
        if name.startswith(_SENSITIVE_NAME_PREFIXES):
            return True
        return name.endswith(_SENSITIVE_SUFFIXES)

    def list_dir(self, path: str = ".") -> str:
        target, failure = self.resolve(path)
        if failure is not None:
            return failure
        assert target is not None
        if not target.exists():
            return _error("not_found", "路径不存在。")
        if not target.is_dir():
            return _error("not_a_directory", "目标不是目录。")
        entries = []
        truncated = False
        try:
            children = sorted(target.iterdir(), key=lambda item: item.name.lower())
        except OSError:
            return _error("list_failed", "目录无法读取。")
        for child in children:
            if child.name.lower() == RESERVED_DIR:
                continue
            if len(entries) >= MAX_LIST_ENTRIES:
                truncated = True
                break
            try:
                is_symlink = child.is_symlink()
                is_dir = child.is_dir()
                size = child.stat().st_size if child.is_file() else None
            except OSError:
                is_symlink, is_dir, size = False, False, None
            entries.append({
                "name": child.name,
                "type": "symlink" if is_symlink else "dir" if is_dir else "file",
                "size": size,
                "sensitive": self._sensitive(child),
            })
        display = "." if target == self.root else target.relative_to(self.root).as_posix()
        return _ok({"path": display, "entries": entries, "truncated": truncated,
                    "limit": MAX_LIST_ENTRIES})

    def _probe(self, target: Path) -> tuple[bool, bool, bool, str | None]:
        """安全探测类型；跨命名空间链接/权限不足不得让工具崩溃。"""
        try:
            return target.exists(), target.is_dir(), target.is_file(), None
        except PermissionError as error:
            if getattr(error, "winerror", None) in _BUSY_WINERRORS:
                return False, False, False, _os_failure(error, "文件被占用，未读取或修改。")
            return False, False, False, _error(
                "permission_denied", "没有权限访问该路径。")
        except OSError as error:
            return False, False, False, _os_failure(error, "未读取或修改文件。")

    def read_text(self, path: str) -> str:
        target, failure = self.resolve(path)
        if failure is not None:
            return failure
        assert target is not None
        # 跨命名空间/损坏的符号链接（例如容器里指向 /etc/passwd 的链接）
        # 在 Windows 宿主上可能让 exists()/is_dir() 直接抛 OSError；必须转成
        # 可诊断的拒绝，而不是让工具执行崩溃。
        exists, is_dir, is_file, failure = self._probe(target)
        if failure is not None:
            return failure
        if not exists:
            return _error("not_found", "文件不存在。")
        if is_dir:
            return _error("is_directory", "目标是目录，请用 fs_list 查看。")
        if not is_file:
            return _error("not_a_file", "目标不是普通文件。")
        if self._sensitive(target):
            return _error("sensitive_path", "该文件被视为敏感文件，拒绝读取。")
        try:
            size = target.stat().st_size
        except OSError as error:
            return _os_failure(error, "无法读取文件元数据。")
        if size > MAX_READ_BYTES:
            return _error("file_too_large", "文件超过读取上限，未截断、未返回内容。",
                          size=size, limit=MAX_READ_BYTES)
        try:
            data = target.read_bytes()
        except OSError:
            return _error("read_failed", "文件无法读取。")
        if b"\x00" in data:
            return _error("binary_file", "文件包含二进制内容，拒绝按文本读取。", size=len(data))
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return _error("not_utf8", "文件不是 UTF-8 文本。")
        return _ok({
            "path": target.relative_to(self.root).as_posix(),
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "mtime_ns": target.stat().st_mtime_ns,
            "content": text,
        })

    def _current_bytes(self, target: Path) -> tuple[bytes | None, str | None]:
        try:
            size = target.stat().st_size
        except OSError:
            return None, _error("stat_failed", "无法读取文件元数据。")
        if size > MAX_WRITE_BYTES:
            return None, _error("file_too_large", "现有文件超过写入上限，未做任何修改。",
                                size=size, limit=MAX_WRITE_BYTES)
        try:
            return target.read_bytes(), None
        except OSError:
            return None, _error("read_failed", "文件无法读取。")

    def _resolve_for_write(self, path: str) -> tuple[Path | None, str | None]:
        target, failure = self._prepare_mutation(path)
        if failure is not None:
            return None, failure
        assert target is not None
        if target == self.root:
            return None, _error("invalid_path", "不能把工作区根当作文件目标。")
        if self._reserved(target):
            return None, _error("reserved_path", "该路径属于工具内部保留目录，禁止直接操作。")
        return target, None

    def _file_sha256(self, target: Path) -> tuple[str | None, str | None]:
        """流式计算 sha256，不把整个大文件读进内存；超过上限则拒绝。"""
        digest = hashlib.sha256()
        total = 0
        try:
            with target.open("rb") as handle:
                while chunk := handle.read(64 * 1024):
                    total += len(chunk)
                    if total > MAX_DELETE_BYTES:
                        return None, _error("file_too_large", "文件超过删除校验上限，未删除。",
                                            size=total, limit=MAX_DELETE_BYTES)
                    digest.update(chunk)
        except OSError:
            return None, _error("read_failed", "文件无法读取，未删除。")
        return digest.hexdigest(), None

    def _write_locked(self, target: Path, data: bytes, expected_sha256: str | None,
                      require_version: bool = True) -> str:
        existed, is_dir, is_file, failure = self._probe(target)
        if failure is not None:
            return failure
        if is_dir:
            return _error("is_directory", "目标是目录，拒绝写入。")
        if existed and not is_file:
            return _error("not_regular_file", "目标不是普通文件，拒绝写入。")
        if existed:
            current, failure = self._current_bytes(target)
            if failure is not None:
                return failure
            assert current is not None
            current_hash = hashlib.sha256(current).hexdigest()
            if expected_sha256 is None and require_version:
                return _error("missing_expected_version",
                              "覆盖已有文件必须带 expected_sha256；请先 fs_read。",
                              current_sha256=current_hash)
            if expected_sha256 is not None and current_hash != expected_sha256:
                return _error("stale_version", "文件已被改动，拒绝覆盖；请重新读取。",
                              current_sha256=current_hash)
        elif expected_sha256 is not None:
            return _error("unexpected_version", "目标文件不存在，但提供了 expected_sha256。")
        _atomic_write(target, data)
        try:
            mtime_ns = target.stat().st_mtime_ns
        except OSError:
            # 写入已经成功；stat 失败不能把结果伪装成写入失败。
            mtime_ns = None
        return _ok({"path": target.relative_to(self.root).as_posix(), "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "mtime_ns": mtime_ns, "created": not existed})

    def _preview_write(self, target: Path, content: str, data: bytes,
                       expected_sha256: str | None) -> str:
        existed, is_dir, is_file, failure = self._probe(target)
        if failure is not None:
            return failure
        if is_dir:
            return _error("is_directory", "目标是目录，拒绝写入。")
        if existed and not is_file:
            return _error("not_regular_file", "目标不是普通文件，拒绝写入。")
        current_hash = None
        before = ""
        if existed:
            current, failure = self._current_bytes(target)
            if failure is not None:
                return failure
            assert current is not None
            current_hash = hashlib.sha256(current).hexdigest()
            if expected_sha256 is not None and current_hash != expected_sha256:
                return _error("stale_version", "文件已被改动，预览与写入都拒绝；请重新读取。",
                              current_sha256=current_hash)
            try:
                before = current.decode("utf-8")
            except UnicodeDecodeError:
                return _error("not_utf8", "现有文件不是 UTF-8 文本，无法生成 diff。")
        elif expected_sha256 is not None:
            return _error("unexpected_version", "目标文件不存在，但提供了 expected_sha256。")
        relative = target.relative_to(self.root).as_posix()
        diff, added, removed, truncated = _bounded_diff(relative, before, content)
        return _ok({"dry_run": True, "path": relative, "existed": existed,
                    "current_sha256": current_hash,
                    "proposed_sha256": hashlib.sha256(data).hexdigest(),
                    "version_checked": expected_sha256 is not None,
                    "diff": diff, "added": added, "removed": removed, "truncated": truncated})

    def write_text(self, path: str, content: str, expected_sha256: str | None = None,
                   dry_run: bool = False, require_version: bool = True) -> str:
        target, failure = self._resolve_for_write(path)
        if failure is not None:
            return failure
        assert target is not None
        if self._sensitive(target):
            return _error("sensitive_path", "该路径被视为敏感文件，拒绝写入。")
        exists, is_dir, _is_file, failure = self._probe(target)
        if failure is not None:
            return failure
        if exists and is_dir:
            return _error("is_directory", "目标是目录，拒绝写入。")
        try:
            parent_is_dir = target.parent.is_dir()
        except PermissionError:
            return _error("permission_denied", "没有权限访问目标目录。")
        except OSError as error:
            return _os_failure(error, "未写入。")
        if not parent_is_dir:
            return _error("parent_missing", "目标目录不存在；本工具不创建目录。")
        data = content.encode("utf-8")
        if len(data) > MAX_WRITE_BYTES:
            return _error("file_too_large", "内容超过写入上限，未写入。",
                          size=len(data), limit=MAX_WRITE_BYTES)
        if dry_run:
            return self._preview_write(target, content, data, expected_sha256)
        try:
            with self._locked(target):
                return self._write_locked(target, data, expected_sha256,
                                          require_version=require_version)
        except LockTimeout as busy:
            return _error("lock_timeout", "文件正被另一个写入操作占用，请稍后重试。",
                          holder=busy.holder)
        except OSError as error:
            return _write_failure(error, "写入文件")

    def _edited_bytes(self, target: Path, old_text: str, new_text: str,
                      expected_sha256: str | None, require_version: bool = True,
                      ) -> tuple[bytes | None, str | None, str | None]:
        current, failure = self._current_bytes(target)
        if failure is not None:
            return None, failure, None
        assert current is not None
        current_hash = hashlib.sha256(current).hexdigest()
        if expected_sha256 is None and require_version:
            return None, _error("missing_expected_version",
                                "修改文件必须带 expected_sha256；请先 fs_read。",
                                current_sha256=current_hash), None
        if expected_sha256 is not None and current_hash != expected_sha256:
            return None, _error("stale_version", "文件已被改动，拒绝修改；请重新读取。",
                                current_sha256=current_hash), None
        try:
            text = current.decode("utf-8")
        except UnicodeDecodeError:
            return None, _error("not_utf8", "文件不是 UTF-8 文本，拒绝修改。"), None
        occurrences = text.count(old_text)
        if occurrences == 0:
            return None, _error("no_match", "原文没有出现，未做修改。"), None
        if occurrences > 1:
            return None, _error("ambiguous_match", "原文出现多次，未做修改；请提供更长的唯一片段。",
                                occurrences=occurrences), None
        edited = text.replace(old_text, new_text, 1)
        data = edited.encode("utf-8")
        if len(data) > MAX_WRITE_BYTES:
            return None, _error("file_too_large", "修改后内容超过写入上限，未写入。",
                                size=len(data), limit=MAX_WRITE_BYTES), None
        return data, None, edited

    def _edit_locked(self, target: Path, old_text: str, new_text: str,
                     expected_sha256: str | None, require_version: bool = True) -> str:
        data, failure, _ = self._edited_bytes(target, old_text, new_text, expected_sha256,
                                              require_version=require_version)
        if failure is not None:
            return failure
        assert data is not None
        _atomic_write(target, data)
        return _ok({"path": target.relative_to(self.root).as_posix(), "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "mtime_ns": target.stat().st_mtime_ns, "replacements": 1})

    def _preview_edit(self, target: Path, old_text: str, new_text: str,
                      expected_sha256: str | None, require_version: bool = True) -> str:
        data, failure, edited = self._edited_bytes(target, old_text, new_text, expected_sha256,
                                                    require_version=require_version)
        if failure is not None:
            return failure
        assert data is not None and edited is not None
        relative = target.relative_to(self.root).as_posix()
        try:
            before = target.read_text(encoding="utf-8")
        except PermissionError:
            return _error("permission_denied", "没有权限读取该文件。")
        except OSError as error:
            return _os_failure(error, "未修改。")
        diff, added, removed, truncated = _bounded_diff(relative, before, edited)
        return _ok({"dry_run": True, "path": relative,
                    "current_sha256": hashlib.sha256(before.encode("utf-8")).hexdigest(),
                    "proposed_sha256": hashlib.sha256(data).hexdigest(),
                    "diff": diff, "added": added, "removed": removed, "truncated": truncated})

    def edit_text(self, path: str, old_text: str, new_text: str,
                  expected_sha256: str | None = None,
                  dry_run: bool = False, require_version: bool = True) -> str:
        target, failure = self._resolve_for_write(path)
        if failure is not None:
            return failure
        assert target is not None
        exists, is_dir, is_file, failure = self._probe(target)
        if failure is not None:
            return failure
        if not exists:
            return _error("not_found", "文件不存在。")
        if is_dir:
            return _error("is_directory", "目标是目录，拒绝修改。")
        if not is_file:
            return _error("not_regular_file", "目标不是普通文件，拒绝修改。")
        if self._sensitive(target):
            return _error("sensitive_path", "该路径被视为敏感文件，拒绝修改。")
        if dry_run:
            return self._preview_edit(target, old_text, new_text, expected_sha256,
                                      require_version=require_version)
        try:
            with self._locked(target):
                return self._edit_locked(target, old_text, new_text, expected_sha256,
                                         require_version=require_version)
        except LockTimeout as busy:
            return _error("lock_timeout", "文件正被另一个写入操作占用，请稍后重试。",
                          holder=busy.holder)
        except OSError as error:
            return _write_failure(error, "写入文件")

    def make_directory(self, path: str, parents: bool = False) -> str:
        target, failure = self._prepare_mutation(path)
        if failure is not None:
            return failure
        assert target is not None
        if target == self.root:
            return _error("invalid_path", "工作区根目录已经存在。")
        if self._reserved(target):
            return _error("reserved_path", "该路径属于工具内部保留目录，禁止直接操作。")
        if self._sensitive(target):
            return _error("sensitive_path", "该路径被视为敏感目录，拒绝创建。")
        if target.exists():
            kind = "dir" if target.is_dir() else "file"
            return _error("already_exists", "目标已存在，未创建。", type=kind)
        if not parents and not target.parent.is_dir():
            return _error("parent_missing", "父目录不存在；可用 parents=true 一并创建。")
        try:
            target.mkdir(parents=parents, exist_ok=False)
        except FileExistsError:
            return _error("already_exists", "目标已存在，未创建。", type="unknown")
        except OSError:
            return _error("mkdir_failed", "目录创建失败，未做任何修改。")
        return _ok({"path": target.relative_to(self.root).as_posix(), "created": True,
                    "parents": parents})

    def move(self, source: str, destination: str,
             expected_sha256: str | None = None) -> str:
        src, failure = self._prepare_mutation(source)
        if failure is not None:
            return failure
        dst, failure = self._prepare_mutation(destination)
        if failure is not None:
            return failure
        assert src is not None and dst is not None
        if src == self.root or dst == self.root:
            return _error("invalid_path", "不能移动工作区根目录。")
        if self._reserved(dst):
            return _error("reserved_path", "目标路径属于工具内部保留目录，禁止移入。")
        if self._reserved(src) and not self._in_trash(src):
            return _error("reserved_path", "源路径属于工具内部保留目录，只能从回收站移出。")
        if self._sensitive(src) or self._sensitive(dst):
            return _error("sensitive_path", "源或目标被视为敏感路径，拒绝移动。")
        if not src.exists():
            return _error("not_found", "源路径不存在。")
        if dst.exists():
            return _error("destination_exists", "目标已存在；本工具不覆盖，请换一个名字。")
        if not dst.parent.is_dir():
            return _error("parent_missing", "目标目录不存在；本工具不创建目录。")
        is_directory = src.is_dir()
        if is_directory:
            if expected_sha256 is not None:
                return _error("unexpected_version", "移动目录不需要 expected_sha256。")
            if dst.is_relative_to(src):
                return _error("invalid_destination", "不能把目录移动到自身内部。")
        else:
            if not src.is_file():
                return _error("not_a_file", "源不是普通文件。")
            if expected_sha256 is None:
                return _error("missing_expected_version",
                              "移动文件必须带 expected_sha256；请先 fs_read。")
        try:
            with self._lock_group([src, dst]):
                if not src.exists():
                    return _error("not_found", "源路径不存在。")
                if dst.exists():
                    return _error("destination_exists", "目标已存在；本工具不覆盖。")
                if is_directory:
                    os.rename(src, dst)
                    sha = None
                else:
                    current, failure = self._current_bytes(src)
                    if failure is not None:
                        return failure
                    assert current is not None
                    current_hash = hashlib.sha256(current).hexdigest()
                    if current_hash != expected_sha256:
                        return _error("stale_version", "源文件已被改动，拒绝移动；请重新读取。",
                                      current_sha256=current_hash)
                    os.rename(src, dst)
                    sha = current_hash
        except LockTimeout as busy:
            return _error("lock_timeout", "源或目标正被另一个操作占用，请稍后重试。",
                          holder=busy.holder)
        except OSError:
            return _error("move_failed", "移动失败，源文件未改动。")
        return _ok({"source": source, "destination": dst.relative_to(self.root).as_posix(),
                    "type": "dir" if is_directory else "file", "sha256": sha})

    def delete(self, path: str, expected_sha256: str | None = None) -> str:
        target, failure = self._prepare_mutation(path)
        if failure is not None:
            return failure
        assert target is not None
        if target == self.root:
            return _error("invalid_path", "不能删除工作区根目录。")
        if self._reserved(target):
            return _error("reserved_path", "该路径属于工具内部保留目录，禁止删除。")
        if self._sensitive(target):
            return _error("sensitive_path", "该路径被视为敏感文件，拒绝删除。")
        if not target.exists():
            return _error("not_found", "路径不存在。")
        try:
            with self._locked(target):
                if not target.exists():
                    return _error("not_found", "路径不存在。")
                if target.is_dir():
                    if expected_sha256 is not None:
                        return _error("unexpected_version", "删除目录不需要 expected_sha256。")
                    try:
                        count = sum(1 for _ in target.iterdir())
                    except OSError:
                        return _error("read_failed", "目录无法读取，未删除。")
                    if count:
                        return _error("directory_not_empty",
                                      "目录非空，未删除；请先逐个删除其中条目。",
                                      entries=count)
                    kind, sha = "dir", None
                else:
                    if not target.is_file():
                        return _error("not_a_file", "目标不是普通文件，未删除。")
                    if expected_sha256 is None:
                        return _error("missing_expected_version",
                                      "删除文件必须带 expected_sha256；请先 fs_read。")
                    current_hash, failure = self._file_sha256(target)
                    if failure is not None:
                        return failure
                    if current_hash != expected_sha256:
                        return _error("stale_version", "文件已被改动，拒绝删除；请重新读取。",
                                      current_sha256=current_hash)
                    kind, sha = "file", current_hash
                stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
                folder = self.root / RESERVED_DIR / "trash" / f"{stamp}-{secrets.token_hex(4)}"
                try:
                    folder.mkdir(parents=True, exist_ok=False)
                    trashed = folder / target.name
                    os.replace(target, trashed)
                except OSError:
                    return _error("delete_failed", "移入回收站失败，原文件未改动。")
        except LockTimeout as busy:
            return _error("lock_timeout", "文件正被另一个操作占用，请稍后重试。",
                          holder=busy.holder)
        trashed_relative = trashed.relative_to(self.root).as_posix()
        return _ok({"path": target.relative_to(self.root).as_posix(), "type": kind,
                    "sha256": sha, "trashed_path": trashed_relative,
                    "restore_hint": f"用 fs_move 把 {trashed_relative} 移回原路径即可恢复。"})


def _atomic_write(target: Path, data: bytes) -> None:
    """同目录临时文件 + os.replace；失败时删除临时文件，原文件保持不变。"""
    handle = tempfile.NamedTemporaryFile(prefix=".dsh-write-", dir=target.parent, delete=False)
    name = handle.name
    try:
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, target)
    except BaseException:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise


def build_write_gate(controller, context):
    """写入授权门：Build + 已确认目标 + 非等待确认。粒度是会话级，不是逐动作签名。"""

    def check() -> tuple[bool, str, str]:
        state = controller.snapshot(context)
        if state.mode != "build":
            return False, "plan_read_only", "Plan 模式只读；写入需要切换到 Build。"
        if state.confirmed_goal_id is None:
            return False, "goal_unconfirmed", "写入需要先确认目标。"
        if state.status == "waiting_user":
            return False, "waiting_user", "等待用户确认期间不写入。"
        return True, "allowed", ""

    return check


def filesystem_tools(root: str | Path, *, gate=None) -> list[ToolDefinition]:
    """构造工具；未提供 gate 时只返回只读工具，写入能力不会出现。"""
    sandbox = FileSandbox(root)

    def list_directory(path: str = ".") -> str:
        return sandbox.list_dir(path)

    def read_file(path: str) -> str:
        return sandbox.read_text(path)

    tools = [
        ToolDefinition(
            name="fs_list",
            description=(
                "列出工作区内某目录的直接子项（名称/类型/大小/是否敏感）。"
                "路径必须相对工作区根；最多返回 500 条并标记 truncated。"
                "只读，不返回文件内容，不跟随符号链接逃逸出工作区。"
            ),
            parameters=FsListArguments,
            handler=list_directory,
        ),
        ToolDefinition(
            name="fs_read",
            description=(
                "读取工作区内的 UTF-8 文本文件，返回内容与 sha256/mtime_ns/size。"
                "路径必须相对工作区根；超过 256 KiB、二进制、非 UTF-8、目录、"
                "以及 .env/*.sqlite3/凭据类文件都会被拒绝，且**不做截断**。"
                "只读；写入请用 fs_write / fs_edit。"
            ),
            parameters=FsReadArguments,
            handler=read_file,
        ),
    ]
    if gate is None:
        return tools

    def write_file(path: str, content: str, expected_sha256: str | None = None,
                   dry_run: bool = False) -> str:
        allowed, code, message = gate()
        if not allowed:
            return _error(code, message)
        return sandbox.write_text(path, content, expected_sha256, dry_run)

    def edit_file(path: str, old_text: str, new_text: str, expected_sha256: str,
                  dry_run: bool = False) -> str:
        allowed, code, message = gate()
        if not allowed:
            return _error(code, message)
        return sandbox.edit_text(path, old_text, new_text, expected_sha256, dry_run)

    def make_directory(path: str, parents: bool = False) -> str:
        allowed, code, message = gate()
        if not allowed:
            return _error(code, message)
        return sandbox.make_directory(path, parents)

    def move_path(source: str, destination: str, expected_sha256: str | None = None) -> str:
        allowed, code, message = gate()
        if not allowed:
            return _error(code, message)
        return sandbox.move(source, destination, expected_sha256)

    def delete_path(path: str, expected_sha256: str | None = None) -> str:
        allowed, code, message = gate()
        if not allowed:
            return _error(code, message)
        return sandbox.delete(path, expected_sha256)

    tools.extend([
        ToolDefinition(
            name="fs_write",
            description=(
                "在工作区内新建或覆盖文本文件，返回 sha256/size。"
                "需 Build 且已确认目标；新建要求文件不存在，覆盖必须带上次 fs_read 的 "
                "expected_sha256（不匹配即拒绝，防止覆盖别人的修改）。"
                "dry_run=true 只返回 unified diff 预览（最多 200 行），不写文件。"
                "超过 1 MiB、目录、父目录不存在、敏感路径一律拒绝；原子写入，失败不改原文件。"
            ),
            parameters=FsWriteArguments,
            handler=write_file,
        ),
        ToolDefinition(
            name="fs_edit",
            description=(
                "把工作区内文本文件中的一段原文替换成新文本，原文必须唯一出现。"
                "需 Build 且已确认目标；必须带上次 fs_read 的 expected_sha256。"
                "dry_run=true 只返回 unified diff 预览，不写文件；预览同样校验版本与唯一匹配。"
                "原文未出现或多处出现都拒绝，不做模糊匹配；原子写入。"
            ),
            parameters=FsEditArguments,
            handler=edit_file,
        ),
        ToolDefinition(
            name="fs_mkdir",
            description=(
                "在工作区内创建一个目录。需 Build 且已确认目标；默认不创建缺失的父目录，"
                "parents=true 时一并创建。已存在、敏感路径、保留目录（.adrian）一律拒绝。"
            ),
            parameters=FsMkdirArguments,
            handler=make_directory,
        ),
        ToolDefinition(
            name="fs_move",
            description=(
                "在工作区内重命名或移动文件/目录。需 Build 且已确认目标；目标已存在时拒绝，"
                "本工具不覆盖。移动文件必须带上次 fs_read 的 expected_sha256；移动目录不需要。"
                "目标父目录必须已存在；不能移动到自身内部；可从 .adrian/trash 移出以恢复删除。"
            ),
            parameters=FsMoveArguments,
            handler=move_path,
        ),
        ToolDefinition(
            name="fs_delete",
            description=(
                "把工作区内的文件或空目录移入回收站（.adrian/trash），不是硬删除，返回 "
                "trashed_path 与恢复方法。需 Build 且已确认目标；删除文件必须带上次 fs_read 的 "
                "expected_sha256；非空目录拒绝（不递归删除）；敏感路径与保留目录拒绝。"
            ),
            parameters=FsDeleteArguments,
            handler=delete_path,
        ),
    ])
    return tools
