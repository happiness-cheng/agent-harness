"""GOV-001-S7：Docker 沙箱后端的离线判据（参数、只读叠挂、fail-closed）。"""

import json

import pytest

from min_agent.tools.generic import GenericWorkspace
from min_agent.tools.sandbox import DockerSandbox


def test_build_args_hardening_and_overlays(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    sandbox = DockerSandbox(tmp_path, image="img:test")
    args = sandbox._build_args("ls", 30, "ah-sandbox-x")
    joined = " ".join(args)
    assert "--network none" in joined
    assert "--cap-drop ALL" in joined
    assert "--read-only" in joined
    assert "no-new-privileges" in joined
    assert f"{tmp_path}:/workspace" in joined  # 工作区 rw
    assert f"{tmp_path / 'src'}:/workspace/src:ro" in joined
    assert f"{tmp_path / 'tests'}:/workspace/tests:ro" in joined
    assert "img:test" in args
    # 不存在的保护目录不叠挂。
    assert "/workspace/eval:ro" not in joined


def test_available_fail_closed_when_docker_missing(tmp_path, monkeypatch):
    sandbox = DockerSandbox(tmp_path)

    def raise_oserror(argv, *, timeout):
        raise OSError("docker not found")

    monkeypatch.setattr(sandbox, "_run", raise_oserror)
    ok, detail = sandbox.available()
    assert not ok
    result = sandbox.execute("ls", 30)
    assert result["ok"] is False
    assert result["error"]["code"] == "sandbox_unavailable"


def test_available_fail_closed_when_image_missing(tmp_path, monkeypatch):
    sandbox = DockerSandbox(tmp_path, image="missing:tag")
    import subprocess

    def fake_run(argv, *, timeout):
        if argv[:2] == ["docker", "info"]:
            return subprocess.CompletedProcess(argv, 0, "29.0", "")
        return subprocess.CompletedProcess(argv, 1, "", "no such image")

    monkeypatch.setattr(sandbox, "_run", fake_run)
    ok, detail = sandbox.available()
    assert not ok and "不存在" in detail


def test_bash_routes_to_backend(tmp_path):
    class FakeBackend:
        def __init__(self):
            self.commands = []

        def execute(self, command, timeout):
            self.commands.append(command)
            return {"ok": True, "exit_code": 0, "stdout": "from-container", "stderr": "",
                    "truncated": False, "duration_ms": 1}

    backend = FakeBackend()
    workspace = GenericWorkspace(tmp_path, backend=backend)
    result = json.loads(workspace.bash("echo hi"))
    assert result["stdout"] == "from-container"
    assert backend.commands == ["echo hi"]


def test_bash_backend_unavailable_no_host_fallback(tmp_path):
    sandbox = DockerSandbox(tmp_path)
    sandbox._available = False
    sandbox._availability_detail = "Docker 守护进程不可达"
    workspace = GenericWorkspace(tmp_path, backend=sandbox)
    result = json.loads(workspace.bash("echo hi"))
    assert result["ok"] is False
    assert result["error"]["code"] == "sandbox_unavailable"