"""Docker 沙箱 spike：验证四工具模式把 bash 放进容器后的 6 项边界。

这个脚本不是产品代码，不改默认行为；它只回答：
1. 容器内工具链可用；
2. 工作区读写与宿主一致；
3. 网络关闭；
4. 敏感文件是否可见（raw / masked 两种）；
5. 容器内符号链接逃逸时，宿主 FileSandbox 是否拒绝；
6. 超时后容器内无残留进程。

运行：
    .venv\\Scripts\\python.exe -X utf8 scripts\\spike_docker_sandbox.py --report reports\\docker-spike.json
    .venv\\Scripts\\python.exe -X utf8 scripts\\spike_docker_sandbox.py --workspace . --report reports\\docker-spike-repo.json

需要 Docker Desktop 已启动。只用于本机验证；不联网拉取依赖以外的镜像。
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

IMAGE = "agent-harness-sandbox:spike"
NAME_PREFIX = "ah-spike-"
DEFAULT_TIMEOUT = 30


def run(argv: list[str], *, timeout: float = 120, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv, cwd=str(cwd) if cwd else None, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout, check=False,
    )


def docker_ready() -> tuple[bool, str]:
    result = run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=20)
    return result.returncode == 0 and bool(result.stdout.strip()), (
        result.stdout.strip() or result.stderr.strip())


def build_image() -> tuple[bool, str]:
    inspect = run(["docker", "image", "inspect", IMAGE], timeout=30)
    if inspect.returncode == 0:
        return True, "image already present"
    with tempfile.TemporaryDirectory(prefix="ah-spike-build-") as directory:
        context = Path(directory)
        (context / "Dockerfile").write_text(
            "FROM python:3.13-slim\n"
            "RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates "
            "&& rm -rf /var/lib/apt/lists/*\n"
            "WORKDIR /workspace\n",
            encoding="utf-8",
        )
        build = run(["docker", "build", "-t", IMAGE, str(context)], timeout=600)
    return build.returncode == 0, (build.stdout + build.stderr).strip()[-2000:]


def docker_run(
    workspace: Path, command: str, *, timeout: float = DEFAULT_TIMEOUT,
    extra_mounts: list[str] | None = None, network: str = "none",
    name: str | None = None, read_only: bool = False,
) -> subprocess.CompletedProcess:
    container = name or f"{NAME_PREFIX}{uuid.uuid4().hex[:12]}"
    mount = f"{workspace}:/workspace{':ro' if read_only else ''}"
    args = [
        "docker", "run", "--rm", "--name", container,
        "--network", network,
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--memory", "1g", "--cpus", "1", "--pids-limit", "256",
        "--read-only",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        "--user", "1000:1000",
        "-e", "HOME=/tmp", "-e", "PIP_NO_CACHE_DIR=1",
        "-v", mount,
        "-w", "/workspace",
    ]
    for mount in extra_mounts or []:
        args.extend(["-v", mount])
    args.extend([
        IMAGE,
        "timeout", "-k", "2", str(timeout), "bash", "-c", command,
    ])
    try:
        return run(args, timeout=timeout + 15)
    except subprocess.TimeoutExpired:
        # 宿主侧兜底：容器可能仍在跑，下面 finally 会强制删除。
        return subprocess.CompletedProcess(args, 124, "", "host_timeout")
    finally:
        # --rm 通常已清理；超时/异常时兜底，避免留下容器。
        run(["docker", "rm", "-f", container], timeout=30)


def check(name: str, ok: bool, detail: object, *, informational: bool = False) -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail, "informational": informational}


def main() -> int:
    global IMAGE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=None,
                        help="工作区；默认临时目录。传仓库根可检查 .env/data 是否可见。")
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--image", default=IMAGE,
                        help="已有镜像可跳过构建（例如本机缓存的 python 镜像）")
    args = parser.parse_args()
    image = args.image
    IMAGE = image

    ready, version = docker_ready()
    if not ready:
        print(json.dumps({"run_state": "blocked", "reason": "docker_not_ready", "detail": version},
                         ensure_ascii=False, indent=2))
        return 2

    if not args.skip_build:
        if image == "agent-harness-sandbox:spike":
            built, detail = build_image()
        else:
            inspect = run(["docker", "image", "inspect", image], timeout=30)
            built, detail = inspect.returncode == 0, (
                "image already present" if inspect.returncode == 0 else inspect.stderr.strip()[-1000:])
        if not built:
            print(json.dumps({"run_state": "blocked", "reason": "image_build_failed", "detail": detail},
                             ensure_ascii=False, indent=2))
            return 2

    workspace = (args.workspace or Path(tempfile.mkdtemp(prefix="ah-spike-ws-"))).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    is_temp = args.workspace is None
    # 临时工作区：造假的 `.env`/data 来验证“原始挂载会暴露、遮罩是否有效”。
    # 真实仓库根：不写任何文件，只读挂载并断言 CLEAN（迁移后的验收点）。
    if is_temp:
        (workspace / ".env").write_text("SPIKE_SECRET=not-a-real-key\n", encoding="utf-8")
        (workspace / "data").mkdir(exist_ok=True)
        (workspace / "data" / "secret.sqlite3").write_bytes(b"SQLite format 3\x00spike")
        (workspace / "note.txt").write_text("before\n", encoding="utf-8")

    checks: list[dict] = []

    # 1. 工具链
    result = docker_run(workspace, "python --version; bash --version | head -1; "
                               "timeout --version | head -1; (git --version || echo GIT_MISSING)",
                        read_only=not is_temp)
    checks.append(check("toolchain",
                        result.returncode == 0 and "Python 3." in result.stdout
                        and "GNU bash" in result.stdout and "timeout (GNU coreutils)" in result.stdout,
                        {"stdout": result.stdout.strip(), "stderr": result.stderr.strip()[-500:]}))

    # 2. 工作区读写（临时）/ 真实仓库只读 CLEAN（迁移后）
    if is_temp:
        result = docker_run(workspace, "printf 'from-container\\n' > note.txt; cat note.txt")
        host_text = (workspace / "note.txt").read_text(encoding="utf-8")
        checks.append(check("workspace_round_trip",
                            result.returncode == 0 and host_text == "from-container\n",
                            {"container_stdout": result.stdout.strip(), "host": host_text}))
    else:
        result = docker_run(
            workspace,
            "test ! -e .env && test ! -e data && echo NO_ENV_DATA",
            read_only=True,
        )
        checks.append(check("repo_no_env_or_data", "NO_ENV_DATA" in result.stdout,
                            {"stdout": result.stdout.strip(),
                             "stderr": result.stderr.strip()[-500:]}))
        private = docker_run(
            workspace,
            "for p in .codex .workbuddy .workbuddy-ai .dsh-acl-report reports "
            "docs/ai-conversations docs/career-positioning-2026-11-05 "
            "docs/interview-2026-11-15 docs/interview-2026-11-21 knowledge; do "
            "test -e \"$p\" && echo \"$p\"; done",
            read_only=True,
        )
        checks.append(check(
            "other_private_dirs_visible", True,
            {"visible": [line for line in private.stdout.splitlines() if line.strip()],
             "note": "仓库根仍含私有目录；Docker 只挂工作区时它们也可读，"
                     "需要 dedicated workspace 或 allowlist 挂载"},
            informational=True))

    # 3. 网络关闭
    probe = ("python -c \"import socket; socket.create_connection(('1.1.1.1', 80), 2); "
             "print('NETWORK_OPEN')\"")
    result = docker_run(workspace, probe, timeout=10, read_only=not is_temp)
    checks.append(check("network_disabled", "NETWORK_OPEN" not in result.stdout,
                        {"stdout": result.stdout.strip(), "stderr": result.stderr.strip()[-300:]}))

    # 3b. 容器不继承宿主凭据环境变量
    result = docker_run(
        workspace,
        "env | grep -E 'API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL' || echo NO_SECRETS",
        read_only=not is_temp)
    checks.append(check("container_env_no_secrets", "NO_SECRETS" in result.stdout,
                        {"stdout": result.stdout.strip()}))

    if is_temp:
        # 4a. raw mount：敏感文件应当可见（证明问题存在）
        result = docker_run(workspace, "test -e .env && echo ENV_VISIBLE; "
                                       "test -e data/secret.sqlite3 && echo DB_VISIBLE")
        visible = "ENV_VISIBLE" in result.stdout and "DB_VISIBLE" in result.stdout
        checks.append(check("sensitive_visible_raw_mount", visible,
                            {"stdout": result.stdout.strip()}, informational=True))

        # 4b. masked mount：空文件盖住 .env，空目录盖住 data/
        # Docker Desktop 上文件级遮罩不可靠；这里只作已知限制记录，不让红灯变常态。
        mask_root = Path(tempfile.mkdtemp(prefix="ah-spike-mask-"))
        empty_file = mask_root / "empty"
        empty_file.write_text("", encoding="utf-8")
        empty_dir = mask_root / "empty-data"
        empty_dir.mkdir()
        result = docker_run(
            workspace,
            "test -e .env && echo ENV_VISIBLE; test -e data/secret.sqlite3 && echo DB_VISIBLE; "
            "test -e .env || echo ENV_HIDDEN; test -e data/secret.sqlite3 || echo DB_HIDDEN",
            extra_mounts=[f"{empty_file}:/workspace/.env:ro", f"{empty_dir}:/workspace/data:ro"],
        )
        checks.append(check("sensitive_hidden_masked_mount",
                            "ENV_HIDDEN" in result.stdout and "DB_HIDDEN" in result.stdout,
                            {"stdout": result.stdout.strip(),
                             "note": "Docker Desktop 文件级遮罩不可靠；仓库已迁移，不依赖遮罩"},
                            informational=True))

        # 5. 容器内建符号链接，宿主 FileSandbox 必须拒绝逃逸
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from min_agent.tools.filesystem import FileSandbox

        link_result = docker_run(workspace, "ln -sfn /etc/passwd escape_passwd; "
                                            "ln -sfn ../outside.txt escape_parent; ls -l escape_passwd")
        symlink_detail: dict = {"stdout": link_result.stdout.strip(),
                                "stderr": link_result.stderr.strip()[-500:]}
        if link_result.returncode != 0:
            checks.append(check("symlink_escape_rejected", False, symlink_detail))
        else:
            sandbox = FileSandbox(workspace)
            try:
                passwd = json.loads(sandbox.read_text("escape_passwd"))
                parent = json.loads(sandbox.read_text("escape_parent"))
                detail = {**symlink_detail, "passwd": passwd, "parent": parent}
                rejected = passwd.get("ok") is False and parent.get("ok") is False
            except Exception as error:  # 宿主崩溃本身就是失败证据
                detail = {**symlink_detail, "host_error": repr(error)}
                rejected = False
            checks.append(check("symlink_escape_rejected", rejected, detail))

    # 6. 超时后容器内无残留 sleep
    started = time.perf_counter()
    timed = docker_run(workspace, "sleep 300", timeout=2, read_only=not is_temp)
    elapsed = time.perf_counter() - started
    leftovers = run(["docker", "ps", "-a", "--filter", f"name={NAME_PREFIX}", "--format", "{{.Names}}"])
    checks.append(check("timeout_no_leftover",
                        elapsed < 10 and not leftovers.stdout.strip(),
                        {"elapsed_s": round(elapsed, 2), "containers": leftovers.stdout.strip(),
                         "returncode": timed.returncode, "stderr": timed.stderr.strip()[-300:]}))

    # 7. 延迟（热容器/冷启动都记录；这里测每次新建容器）
    samples: list[float] = []
    for _ in range(3):
        begin = time.perf_counter()
        result = docker_run(workspace, "true", timeout=10, read_only=not is_temp)
        if result.returncode == 0:
            samples.append((time.perf_counter() - begin) * 1000)
    checks.append(check("latency_ms", len(samples) == 3,
                        {"samples": [round(value, 1) for value in samples],
                         "p50": round(statistics.median(samples), 1) if samples else None}))

    summary = {
        "run_state": "completed",
        "docker": version,
        "workspace": str(workspace),
        "checks": checks,
        "passed": sum(1 for item in checks if item["ok"]),
        "failed": sum(1 for item in checks if not item["ok"] and not item["informational"]),
        "informational_failures": sum(1 for item in checks if not item["ok"] and item["informational"]),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())