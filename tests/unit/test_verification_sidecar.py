"""GOV-001-S6：bash 执行后验证 + 上下文 sidecar。"""

import json

import pytest

from min_agent.context.sidecar import ContextSidecar
from min_agent.tools.generic import GenericWorkspace


@pytest.fixture()
def workspace(tmp_path):
    return GenericWorkspace(tmp_path)


def test_verify_readonly_passes(workspace):
    result = json.loads(workspace.bash("echo hi > out.txt", verify="test -f out.txt"))
    assert result["ok"] is True and result["exit_code"] == 0
    assert result["verification"]["passed"] is True
    assert result["verification"]["exit_code"] == 0


def test_verify_failure_reported_not_overriding_main(workspace):
    result = json.loads(workspace.bash("echo hi", verify="test -f nonexistent.txt"))
    assert result["ok"] is True  # 主命令成功不被推翻
    assert result["verification"]["passed"] is False
    assert result["verification"]["exit_code"] != 0


def test_verify_must_be_readonly(workspace):
    result = json.loads(workspace.bash("echo hi", verify="rm out.txt"))
    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_verify"


def test_verify_skipped_when_main_fails(workspace):
    result = json.loads(workspace.bash("exit 3", verify="test -f out.txt"))
    assert result["ok"] is True and result["exit_code"] == 3
    assert "verification" not in result  # 主命令非 0，不跑验证


def test_no_verify_no_verification_field(workspace):
    result = json.loads(workspace.bash("echo hi"))
    assert "verification" not in result


def test_sidecar_section_has_source_and_index(tmp_path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    (tmp_path / "src").mkdir()
    sidecar = ContextSidecar(tmp_path)
    sidecar.start()
    section = sidecar.section()
    assert section is not None
    assert "source=sidecar" in section
    assert "a.txt" in section and "src/" in section
    assert sidecar.error is None


def test_sidecar_failure_degrades_to_none(tmp_path):
    sidecar = ContextSidecar(tmp_path)
    sidecar._result = None  # 模拟采集失败/超时
    sidecar._thread = None
    assert sidecar.section() is None