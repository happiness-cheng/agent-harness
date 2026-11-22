"""通用工具：read / write / edit / bash。

这是 pi 式的最小工具面：模型用四个通用入口自己编排，而不是为每类操作
增加一个专用工具。文件三件套仍复用 ``FileSandbox`` 的工作区约束、敏感
路径拒绝、原子写与写锁；``bash`` 是唯一能越出工作区的入口，默认按宿主
用户权限运行，**不是沙箱**。本模块不检查 Plan/Build 或目标确认——那是
调用方（``HarnessApplication`` 的 generic 模式）显式选择的取舍。
"""

import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from min_agent.tools.filesystem import (
    MAX_PATH_LENGTH,
    MAX_WRITE_BYTES,
    FileSandbox,
)
from min_agent.tools.registry import ToolDefinition

MAX_READ_LINES = 2000
MAX_COMMAND_CHARS = 20_000
MAX_BASH_OUTPUT_BYTES = 64 * 1024
DEFAULT_TIMEOUT_SECONDS = 120.0
MAX_TIMEOUT_SECONDS = 600.0


class ReadArguments(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    path: str = Field(min_length=1, max_length=MAX_PATH_LENGTH,
                      description="相对工作区根的文本文件路径；不接受绝对路径或 ..。")
    offset: int = Field(default=1, ge=1, le=10_000_000,
                        description="起始行号，从 1 开始。")
    limit: int = Field(default=MAX_READ_LINES, ge=1, le=MAX_READ_LINES,
                       description="最多读取的行数，上限 2000。")


class WriteArguments(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    path: str = Field(min_length=1, max_length=MAX_PATH_LENGTH,
                      description="相对工作区根的目标路径；父目录必须已存在。")
    content: str = Field(max_length=MAX_WRITE_BYTES,
                         description="要写入的完整文本；已存在文件会被原子覆盖。")


class EditArguments(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    path: str = Field(min_length=1, max_length=MAX_PATH_LENGTH,
                      description="要修改的文本文件，相对工作区根。")
    old_text: str = Field(min_length=1, max_length=MAX_WRITE_BYTES,
                          description="要被替换的原文，必须在文件中唯一出现。")
    new_text: str = Field(max_length=MAX_WRITE_BYTES,
                          description="替换后的文本，可为空字符串。")


class BashArguments(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    command: str = Field(min_length=1, max_length=MAX_COMMAND_CHARS,
                         description="交给 bash -c 的命令；工作目录是工作区根。")
    timeout_seconds: float = Field(default=DEFAULT_TIMEOUT_SECONDS, ge=1,
                                   le=MAX_TIMEOUT_SECONDS,
                                   description="超时秒数，1–600，默认 120。")


def _ok(**payload: object) -> str:
    return json.dumps({"ok": True, **payload}, ensure_ascii=False)


def _error(code: str, message: str, **details: object) -> str:
    return json.dumps({"ok": False, "error": {"code": code, "message": message, **details}},
                      ensure_ascii=False)


def _cap_utf8(text: str, limit: int) -> tuple[str, bool]:
    """按 UTF-8 字节截断，不把多字节字符切成半个。"""
    raw = text.encode("utf-8", "replace")
    if len(raw) <= limit:
        return text, False
    return raw[:limit].decode("utf-8", "ignore") + "\n...[truncated]", True


def _git_bash() -> str | None:
    """优先 Git Bash；拒绝 System32 的 WSL 启动器（cwd/路径语义不同）。"""
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if base:
                candidate = Path(base) / "Git" / "bin" / "bash.exe"
                if candidate.is_file():
                    return str(candidate)
        found = shutil.which("bash")
        if found:
            system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
            try:
                if Path(found).resolve() == (system_root / "System32" / "bash.exe").resolve():
                    return None
            except OSError:
                return None
            return found
        return None
    return shutil.which("bash")


def _bash_environment(root: Path, shell: str) -> dict[str, str]:
    """清掉常见凭据环境变量，再把项目 venv 放到 PATH 前面。"""
    markers = ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
    environment = {
        key: value for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in markers)
    }
    environment.pop("ADRIAN_CREDENTIALS_FILE", None)
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    parts: list[str] = []
    # 优先工作区自己的 venv；没有则退回应用所在仓库的 venv，避免外部工作区里
    # `python` 落到系统解释器。
    candidates = [
        root / ".venv" / ("Scripts" if os.name == "nt" else "bin"),
        Path(__file__).resolve().parents[3] / ".venv" / ("Scripts" if os.name == "nt" else "bin"),
    ]
    for venv in candidates:
        if venv.is_dir():
            parts.append(str(venv))
            break
    if os.name == "nt":
        git_root = Path(shell).parent.parent
        for relative in ("usr/bin", "mingw64/bin"):
            candidate = git_root / relative
            if candidate.is_dir():
                parts.append(str(candidate))
    existing = environment.get("PATH")
    if existing:
        parts.append(existing)
    environment["PATH"] = os.pathsep.join(parts)
    return environment


class GenericWorkspace:
    """四个通用工具的实现；文件操作仍限制在 root 内。"""

    def __init__(self, root: str | Path) -> None:
        self.sandbox = FileSandbox(root)
        self.root = self.sandbox.root

    def read(self, path: str, offset: int = 1, limit: int = MAX_READ_LINES) -> str:
        target, failure = self.sandbox.resolve(path)
        if failure is not None:
            return failure
        assert target is not None
        if target.is_dir():
            # 默认模式的 fs_read 会提示用 fs_list；本模式没有 fs_list。
            return _error("is_directory", "目标是目录；用 bash ls 查看。")
        payload = json.loads(self.sandbox.read_text(path))
        if not payload.get("ok"):
            return json.dumps(payload, ensure_ascii=False)
        lines = payload["content"].splitlines(keepends=True)
        total = len(lines)
        start = min(offset, total + 1)
        chunk = lines[start - 1:start - 1 + limit]
        end = start - 1 + len(chunk)
        return _ok(
            path=payload["path"],
            content="".join(chunk),
            start_line=start,
            end_line=end,
            total_lines=total,
            truncated=end < total,
            sha256=payload["sha256"],
            size=payload["size"],
        )

    def write(self, path: str, content: str) -> str:
        return self.sandbox.write_text(path, content, expected_sha256=None,
                                       require_version=False)

    def edit(self, path: str, old_text: str, new_text: str) -> str:
        return self.sandbox.edit_text(path, old_text, new_text,
                                      expected_sha256=None, require_version=False)

    def bash(self, command: str, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> str:
        if "\x00" in command:
            return _error("invalid_command", "命令包含 NUL 字符。")
        shell = _git_bash()
        if shell is None:
            return _error("bash_not_found",
                          "未找到 Git Bash；请安装 Git for Windows 或把 bash 加入 PATH。")
        started = time.perf_counter()
        options: dict = {
            "cwd": str(self.root),
            "env": _bash_environment(self.root, shell),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
        }
        if os.name == "nt":
            options["creationflags"] = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
        else:
            # 独立进程组，超时才能连同子进程一起终止。
            options["start_new_session"] = True
        try:
            process = subprocess.Popen([shell, "-c", command], **options)
        except OSError as error:
            return _error("exec_failed", f"无法启动 bash：{error}")
        try:
            stdout_bytes, stderr_bytes = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            _kill_tree(process)
            try:
                stdout_bytes, stderr_bytes = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                stdout_bytes, stderr_bytes = b"", b""
            stdout, _ = _cap_utf8(_decode(stdout_bytes), MAX_BASH_OUTPUT_BYTES)
            stderr, _ = _cap_utf8(_decode(stderr_bytes), MAX_BASH_OUTPUT_BYTES)
            return _error(
                "timeout", f"命令超过 {timeout_seconds:g} 秒，已终止。",
                stdout=stdout, stderr=stderr,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        stdout, stdout_truncated = _cap_utf8(_decode(stdout_bytes), MAX_BASH_OUTPUT_BYTES)
        stderr, stderr_truncated = _cap_utf8(_decode(stderr_bytes), MAX_BASH_OUTPUT_BYTES)
        return _ok(
            exit_code=process.returncode,
            stdout=stdout,
            stderr=stderr,
            truncated=stdout_truncated or stderr_truncated,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )


def _kill_tree(process: subprocess.Popen) -> None:
    """超时后连同子进程一起终止；Windows 用 taskkill /T，POSIX 用进程组。"""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, check=False,
        )
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except OSError:
            process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def _decode(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.decode("utf-8", "replace")


def generic_tools(root: str | Path) -> list[ToolDefinition]:
    """构造四个通用工具；调用方负责决定是否注册以及权限门。"""
    workspace = GenericWorkspace(root)
    return [
        ToolDefinition(
            name="read",
            description=(
                "读取工作区内的 UTF-8 文本文件，按行切片。offset 从 1 开始，"
                "limit 最多 2000 行；返回原文、起止行、总行数、sha256。"
                "大文件只读请求的片段；二进制、非 UTF-8、敏感路径和超过 256 KiB 的文件直接拒绝，不截断。"
            ),
            parameters=ReadArguments,
            handler=workspace.read,
        ),
        ToolDefinition(
            name="write",
            description=(
                "在工作区内新建或原子覆盖文本文件，返回 sha256/size。"
                "父目录必须已存在；敏感路径、目录和超过 1 MiB 的内容拒绝。"
                "覆盖不需要先读，但写前应确认目标路径。"
            ),
            parameters=WriteArguments,
            handler=workspace.write,
        ),
        ToolDefinition(
            name="edit",
            description=(
                "把工作区文件中唯一出现的 old_text 替换为 new_text。"
                "原文未出现或多处出现都拒绝，不做模糊匹配；原子写入，失败不改原文件。"
                "替换前应先 read 确认上下文。"
            ),
            parameters=EditArguments,
            handler=workspace.edit,
        ),
        ToolDefinition(
            name="bash",
            description=(
                "在工作区根目录执行 bash 命令，返回 exit_code/stdout/stderr"
                "（各截断到 64 KiB）和耗时。默认超时 120 秒，范围 1–600。"
                "命令以当前用户权限运行，能访问工作区外的文件和网络，**不是沙箱**；"
                "不要用它读取或外传凭据。需要过滤大结果时，在命令里只输出必要部分。"
            ),
            parameters=BashArguments,
            handler=workspace.bash,
        ),
    ]