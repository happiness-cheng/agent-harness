"""Docker 沙箱后端（GOV-001-S7）：把 bash 放进容器，最小接入。

复用 spike 已验证的参数：--network none、--cap-drop ALL、no-new-privileges、
--read-only 根文件系统、tmpfs /tmp、内存/CPU/pids 限制、每命令一容器、超时强删。
工作区以 rw 挂到 /workspace；``src/ scripts/ tests/ eval/ .git/`` 若存在则叠挂
只读，防容器里的 bash 改门禁/评测代码（阻断 2 的最小防线）。

fail-closed：Docker 或镜像不可用 → ``sandbox_unavailable``，**不悄悄回落宿主**。
默认仍是宿主实验模式；只有显式 ``--sandbox docker`` 才走这里。

明确不保证：文件工具仍在宿主执行（A/B 未决），与容器共享 bind mount 的 TOCTOU
推迟；只读叠挂防改门禁代码，不防容器读工作区内的私有材料（dedicated workspace
另案）；镜像需用户自行构建（正式构建曾被 Docker Hub 网络阻塞）。
"""

from __future__ import annotations

import subprocess
import time
import uuid
from pathlib import Path

DEFAULT_IMAGE = "agent-harness-sandbox:spike"
NAME_PREFIX = "ah-sandbox-"
# 叠挂只读的目录：门禁/评测/版本控制代码，容器不得改。
PROTECTED_DIRS = ("src", "scripts", "tests", "eval", ".git")
MAX_OUTPUT_BYTES = 64 * 1024


def _cap(text: str, limit: int = MAX_OUTPUT_BYTES) -> tuple[str, bool]:
    raw = text.encode("utf-8", "replace")
    if len(raw) <= limit:
        return text, False
    return raw[:limit].decode("utf-8", "ignore") + "\n...[truncated]", True


class DockerSandbox:
    """每命令一容器的 bash 后端；execute 返回与宿主 _execute 同形的结果 dict。"""

    def __init__(self, workspace_root, *, image: str = DEFAULT_IMAGE) -> None:
        self._root = Path(workspace_root).resolve()
        self._image = image
        self._available: bool | None = None
        self._availability_detail = ""

    def _run(self, argv: list[str], *, timeout: float) -> subprocess.CompletedProcess:
        return subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, check=False,
            stdin=subprocess.DEVNULL,
        )

    def available(self) -> tuple[bool, str]:
        if self._available is not None:
            return self._available, self._availability_detail
        try:
            info = self._run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=20)
        except (OSError, subprocess.TimeoutExpired) as error:
            self._available, self._availability_detail = False, f"docker 不可用：{error}"
            return self._available, self._availability_detail
        if info.returncode != 0 or not info.stdout.strip():
            self._available = False
            self._availability_detail = "Docker 守护进程不可达"
            return self._available, self._availability_detail
        try:
            inspect = self._run(["docker", "image", "inspect", self._image], timeout=30)
        except (OSError, subprocess.TimeoutExpired) as error:
            self._available, self._availability_detail = False, f"镜像检查失败：{error}"
            return self._available, self._availability_detail
        if inspect.returncode != 0:
            self._available = False
            self._availability_detail = f"镜像 {self._image} 不存在；请先构建（不自动拉取）"
            return self._available, self._availability_detail
        self._available, self._availability_detail = True, "ready"
        return self._available, self._availability_detail

    def _build_args(self, command: str, timeout: float, container: str) -> list[str]:
        args = [
            "docker", "run", "--rm", "--name", container,
            "--network", "none",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--memory", "1g", "--cpus", "1", "--pids-limit", "256",
            "--read-only",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            "--user", "1000:1000",
            "-e", "HOME=/tmp", "-e", "PIP_NO_CACHE_DIR=1",
            "-v", f"{self._root}:/workspace",
            "-w", "/workspace",
        ]
        # 只读叠挂保护目录（存在才挂），防容器改门禁/评测代码。
        for name in PROTECTED_DIRS:
            directory = self._root / name
            if directory.is_dir():
                args.extend(["-v", f"{directory}:/workspace/{name}:ro"])
        args.extend([
            self._image,
            "timeout", "-k", "2", str(timeout), "bash", "-c", command,
        ])
        return args

    def execute(self, command: str, timeout_seconds: float) -> dict:
        ok, detail = self.available()
        if not ok:
            return {"ok": False, "error": {"code": "sandbox_unavailable", "message": detail}}
        container = f"{NAME_PREFIX}{uuid.uuid4().hex[:12]}"
        args = self._build_args(command, timeout_seconds, container)
        started = time.perf_counter()
        try:
            result = self._run(args, timeout=timeout_seconds + 15)
            returncode, stdout, stderr = result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            returncode, stdout, stderr = 124, "", "host_timeout"
        finally:
            # --rm 通常已清理；超时/异常兜底强删，避免残留容器。
            try:
                self._run(["docker", "rm", "-f", container], timeout=30)
            except (OSError, subprocess.TimeoutExpired):
                pass
        out, out_trunc = _cap(stdout)
        err, err_trunc = _cap(stderr)
        if returncode == 124:
            return {"ok": False, "error": {
                "code": "timeout", "message": f"命令超过 {timeout_seconds:g} 秒，已终止。",
                "stdout": out, "stderr": err,
                "duration_ms": int((time.perf_counter() - started) * 1000)}}
        return {"ok": True, "exit_code": returncode, "stdout": out, "stderr": err,
                "truncated": out_trunc or err_trunc,
                "duration_ms": int((time.perf_counter() - started) * 1000)}