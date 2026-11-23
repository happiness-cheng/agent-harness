"""效果分类器（effects.py）的离线单元判据。"""

from pathlib import Path

import pytest

from min_agent.tools.effects import classify_bash, classify_call


@pytest.fixture
def root(tmp_path):
    return tmp_path


@pytest.mark.parametrize("command", [
    "ls", "cat notes.txt", "head -5 a.txt", "grep ERROR log.txt",
    "rg foo src", "wc -l a.txt", "pwd", "echo hi", "git status",
    "git log --oneline", "git diff", "find . -name '*.py'", "diff a b",
    "pip list",
])
def test_read_only_commands_are_low(command, root):
    assert classify_bash(command, root).level == "low", command


@pytest.mark.parametrize("command", [
    "rm victim.txt", "rmdir d", "dd if=/dev/zero of=x", "shred a",
    "git push origin main", "git reset --hard HEAD", "git clean -fd",
    "curl http://example.com", "wget http://x/y", "ssh host",
    "pip install requests", "sudo ls", "chmod 777 a", "kill 123",
])
def test_high_risk_commands_are_high(command, root):
    assert classify_bash(command, root).level == "high", command


@pytest.mark.parametrize("command", [
    "python script.py", "pytest -q", "node app.js", "awk '{print $1}' a",
    "sed -i 's/a/b/' f", "some_unknown_tool x", "git commit -m x",
    "echo hi > out.txt", "cat a >> b",
])
def test_gray_commands_are_medium(command, root):
    assert classify_bash(command, root).level == "medium", command


def test_destructive_flag_set_for_rm(root):
    effect = classify_bash("rm a.txt", root)
    assert effect.level == "high" and effect.destructive is True


def test_mixed_segments_take_max_level(root):
    # ls（低）&& rm（高）→ 整体高。
    assert classify_bash("ls && rm a.txt", root).level == "high"
    # ls（低）; python x.py（中）→ 整体中。
    assert classify_bash("ls; python x.py", root).level == "medium"


def test_find_with_delete_is_not_read_only(root):
    # find -delete 效果等同删除；分类至少不是 low（规则层会拦）。
    assert classify_bash("find . -name x -delete", root).level != "low"


def test_redirect_to_dev_null_stays_low(root):
    assert classify_bash("grep x a 2>/dev/null", root).level == "low"


def test_parse_failure_is_high(root):
    assert classify_bash("echo 'unclosed", root).level == "high"


def test_classify_call_read_write_edit(root):
    assert classify_call("read", {"path": "a"}, root).level == "low"
    assert classify_call("write", {"path": "new.txt"}, root).level == "low"
    (root / "exist.txt").write_text("x", encoding="utf-8")
    assert classify_call("write", {"path": "exist.txt"}, root).level == "medium"
    assert classify_call("edit", {"path": "exist.txt"}, root).level == "medium"


def test_classify_call_bash_delegates(root):
    assert classify_call("bash", {"command": "rm a"}, root).level == "high"
    assert classify_call("bash", {"command": "ls"}, root).level == "low"