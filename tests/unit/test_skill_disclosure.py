"""WIRING-001：目录型 skill、子文档白名单、清单段 token 上限。"""
from pathlib import Path

import pytest

from min_agent.compression.skills import SkillCatalog, SKILL_SECTION_TOKEN_LIMIT
from min_agent.ledger.resource_budget import estimate_tokens

HEADER = "---\nname: {name}\ndescription: 用途说明{name}\n---\n"


def make_flat(root, name, description=None):
    header = HEADER.format(name=name)
    if description is not None:
        header = f"---\nname: {name}\ndescription: {description}\n---\n"
    (root / f"{name}.md").write_text(header + f"# FULL_BODY_{name}\n", encoding="utf-8")


def make_dir(root, name, subdocs=None, description=None):
    directory = root / name
    (directory / "references").mkdir(parents=True)
    header = HEADER.format(name=name)
    if description is not None:
        header = f"---\nname: {name}\ndescription: {description}\n---\n"
    (directory / "SKILL.md").write_text(
        header + f"# FULL_BODY_{name}\n见 references/extra.md\n", encoding="utf-8")
    for rel, text in (subdocs or {"references/extra.md": f"# SUBDOC_{name}\n"}).items():
        target = directory / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return directory


def test_directory_skill_registered_with_subdocs(tmp_path):
    make_flat(tmp_path, "flat-one")
    make_dir(tmp_path, "dir-one")
    catalog = SkillCatalog(tmp_path)
    assert sorted(catalog.entries) == ["dir-one", "flat-one"]
    assert {s["name"] for s in catalog.summaries()} == {"dir-one", "flat-one"}
    assert "FULL_BODY_dir-one" in catalog.read("dir-one")
    assert "SUBDOC_dir-one" in catalog.read("dir-one", "references/extra.md")
    assert "FULL_BODY_dir-one" in catalog.read("dir-one", "SKILL.md")


def test_directory_skill_requires_frontmatter(tmp_path):
    directory = tmp_path / "broken"
    directory.mkdir()
    (directory / "SKILL.md").write_text("# 没有头部\n", encoding="utf-8")
    with pytest.raises(ValueError):
        SkillCatalog(tmp_path).summaries()
    (directory / "SKILL.md").write_text("---\nname: broken\n---\n正文\n", encoding="utf-8")
    with pytest.raises(ValueError):
        SkillCatalog(tmp_path).summaries()


def test_flat_and_directory_name_conflict_rejected(tmp_path):
    make_flat(tmp_path, "same")
    make_dir(tmp_path, "same")
    with pytest.raises(ValueError):
        SkillCatalog(tmp_path)


@pytest.mark.parametrize("path", ["../escape.md", "/etc/passwd", "missing.md", "references/../SKILL.md", ""])
def test_subdoc_path_whitelist(tmp_path, path):
    make_dir(tmp_path, "dir-one")
    catalog = SkillCatalog(tmp_path)
    with pytest.raises(PermissionError):
        catalog.read("dir-one", path)


def test_flat_skill_has_no_subdocs(tmp_path):
    make_flat(tmp_path, "flat-one")
    with pytest.raises(PermissionError):
        SkillCatalog(tmp_path).read("flat-one", "anything.md")


def test_symlink_subdoc_not_registered(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret", encoding="utf-8")
    directory = make_dir(tmp_path, "dir-one")
    try:
        (directory / "link.md").symlink_to(outside / "secret.md")
    except OSError:
        pytest.skip("本机不允许创建符号链接")
    catalog = SkillCatalog(tmp_path)
    with pytest.raises(PermissionError):
        catalog.read("dir-one", "link.md")


def test_prompt_section_lists_names_and_descriptions_not_bodies(tmp_path):
    make_flat(tmp_path, "flat-one")
    make_dir(tmp_path, "dir-one")
    section = SkillCatalog(tmp_path).prompt_section()
    assert section.startswith("## 技能")
    assert "flat-one：用途说明flat-one" in section
    assert "dir-one：用途说明dir-one" in section
    assert "read_skill(name)" in section
    assert "FULL_BODY" not in section and "SUBDOC" not in section
    assert estimate_tokens(section) <= SKILL_SECTION_TOKEN_LIMIT


def test_prompt_section_empty_catalog(tmp_path):
    assert SkillCatalog(tmp_path).prompt_section() == ""


def test_prompt_section_keeps_only_first_sentence(tmp_path):
    make_flat(tmp_path, "multi", description="首句用途。何时不用：某些场景。反例：错误做法。")
    section = SkillCatalog(tmp_path).prompt_section()
    assert "multi：首句用途。" in section
    assert "何时不用" not in section and "反例" not in section
    assert "首句用途。何时不用" in SkillCatalog(tmp_path)._metadata("multi")["description"]


def test_prompt_section_over_limit_falls_back_to_names(tmp_path):
    for index in range(30):
        make_flat(tmp_path, f"skill-{index:02d}", description="长描述" * 60)
    section = SkillCatalog(tmp_path).prompt_section()
    assert estimate_tokens(section) <= SKILL_SECTION_TOKEN_LIMIT
    assert "长描述" not in section
    assert "- skill-00" in section and "- skill-29" in section
    assert "已截断" not in section


def test_prompt_section_names_over_limit_truncates(tmp_path):
    for index in range(400):
        make_flat(tmp_path, f"skill-{index:03d}")
    section = SkillCatalog(tmp_path).prompt_section()
    assert estimate_tokens(section) <= SKILL_SECTION_TOKEN_LIMIT
    assert "（清单超长，已截断）" in section
    assert "- skill-000" in section
    assert "- skill-399" not in section


def test_real_skills_catalog_ships_three_new_skills():
    root = Path(__file__).resolve().parents[2] / "src" / "min_agent" / "skills"
    catalog = SkillCatalog(root)
    for name in ("code-entry-first", "criteria-before-tests", "evidence-before-compaction"):
        assert name in catalog.entries
        description = catalog._metadata(name)["description"]
        assert "何时不用" in description and "反例" in description
    checklist = catalog.read("evidence-before-compaction", "references/evidence-checklist.md")
    assert "判据原文" in checklist
    section = catalog.prompt_section()
    assert estimate_tokens(section) <= SKILL_SECTION_TOKEN_LIMIT
    assert "FULL_BODY" not in section