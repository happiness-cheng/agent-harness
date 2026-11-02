"""目录必须来自完整YAML字段，不能读取正文来猜描述。"""
from pathlib import Path

import pytest

from min_agent.skills import SkillCatalog


def catalog(tmp_path, header, body=b"# BODY_MARKER\n"):
    (tmp_path / "example.md").write_bytes(header.encode() + body)
    return SkillCatalog(tmp_path)


@pytest.mark.parametrize("value,expected", [
    ('description: "用途：整理文本"', "用途：整理文本"),
    ('description: >-\n  第一行完整用途。\n  第二行完整触发条件。', "第一行完整用途。 第二行完整触发条件。"),
    ('description: |\n  第一行完整用途。\n  第二行完整触发条件。', "第一行完整用途。\n第二行完整触发条件。\n"),
    ('description: "' + "完整描述" * 100 + '"', "完整描述" * 100),
])
def test_full_description(tmp_path, value, expected):
    skills = catalog(tmp_path, "---\nname: example\n" + value + "\n---\n")
    assert skills.summaries() == [{"name": "example", "description": expected}]


def test_catalog_does_not_read_or_decode_body(tmp_path, monkeypatch):
    skills = catalog(tmp_path, "---\nname: example\ndescription: 完整描述\n---\n", b"\xffBODY")
    def forbidden(*args, **kwargs): raise AssertionError("目录不得调用全文read")
    monkeypatch.setattr(skills, "read", forbidden)
    assert skills.summaries()[0]["description"] == "完整描述"


@pytest.mark.parametrize("header", [
    "# 没有头部\n", "---\nname: example\n---\n", "---\ndescription: 用途\n---\n",
    "---\nname: other\ndescription: 用途\n---\n", "---\nname: example\ndescription: []\n---\n",
    "---\nname: example\ndescription: ''\n---\n", "---\nname: example\ndescription: 用途\n",
    "---\nname: example\ndescription: 一\ndescription: 二\n---\n",
    "---\nname: example\ndescription: [\n---\n",
    "---\nname: example\ndescription: !!python/object:example {}\n---\n",
    "---\nname: example\ndescription: &a [*a]\n---\n",
    "---\nname: example\ndescription: " + "长" * 1025 + "\n---\n",
    "---\nname: example\ndescription: 用途\n#" + "a" * 17000 + "\n---\n",
])
def test_invalid_headers_are_rejected_without_fallback(tmp_path, header):
    with pytest.raises(ValueError): catalog(tmp_path, header).summaries()


def test_full_file_is_still_available_on_explicit_read(tmp_path):
    header = "---\nname: example\ndescription: 用途\n---\n"
    skills = catalog(tmp_path, header)
    assert skills.read("example") == header + "# BODY_MARKER\n"
