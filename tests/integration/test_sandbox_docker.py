"""GOV-001-S7：Docker 沙箱真实容器判据（Docker/镜像不可用时整文件 skip，NOT RUN）。"""

import json
import os
from pathlib import Path

import pytest

from min_agent.tools.generic import GenericWorkspace
from min_agent.tools.sandbox import DEFAULT_IMAGE, DockerSandbox

# 镜像可用环境变量覆盖（正式镜像构建被网络阻塞时，用本机缓存镜像复验参数行为）。
_IMAGE = os.environ.get("AGENT_HARNESS_SANDBOX_IMAGE", DEFAULT_IMAGE)
_probe = DockerSandbox(Path(__file__).resolve().parents[2], image=_IMAGE)
_READY, _DETAIL = _probe.available()
pytestmark = pytest.mark.skipif(not _READY, reason=f"Docker/镜像不可用：{_DETAIL}")


@pytest.fixture()
def workspace(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "gate.py").write_text("# gate\n", encoding="utf-8")
    return tmp_path


def test_workspace_rw_round_trip(workspace):
    backend = DockerSandbox(workspace, image=_IMAGE)
    ws = GenericWorkspace(workspace, backend=backend)
    result = json.loads(ws.bash("printf 'from-container\\n' > note.txt"))
    assert result["ok"] is True and result["exit_code"] == 0
    assert (workspace / "note.txt").read_text(encoding="utf-8") == "from-container\n"


def test_cannot_modify_gate_code_readonly_overlay(workspace):
    backend = DockerSandbox(workspace, image=_IMAGE)
    ws = GenericWorkspace(workspace, backend=backend)
    before = (workspace / "src" / "gate.py").read_bytes()
    result = json.loads(ws.bash("echo x >> src/gate.py"))
    # 只读叠挂：写门禁代码必须失败（非 0 退出），文件不变。
    assert result["ok"] is True and result["exit_code"] != 0
    assert (workspace / "src" / "gate.py").read_bytes() == before


def test_network_disabled(workspace):
    backend = DockerSandbox(workspace, image=_IMAGE)
    ws = GenericWorkspace(workspace, backend=backend)
    result = json.loads(ws.bash(
        "python -c \"import socket; socket.create_connection(('1.1.1.1', 80), 2)\""))
    assert result["exit_code"] != 0  # 网络关闭，连接失败