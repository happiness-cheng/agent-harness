"""包结构门禁：根目录只保留入口，模块必须归类到子包且子包有说明。

这条门禁是给未来的自己用的：新增模块应放进对应子包，而不是继续堆在
``src/min_agent`` 根目录，让 60 多个文件再次变得难找。
"""

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "src" / "min_agent"
ALLOWED_ROOT = {"__init__.py", "application.py"}
NON_CODE_DIRS = {"prompts", "skills", "__pycache__"}


def test_root_only_keeps_entrypoint_and_package_init():
    assert {path.name for path in PACKAGE.glob("*.py")} == ALLOWED_ROOT


def test_every_code_subpackage_declares_its_purpose():
    for directory in sorted(PACKAGE.iterdir()):
        if not directory.is_dir() or directory.name in NON_CODE_DIRS:
            continue
        if not any(directory.glob("*.py")):
            continue
        init = directory / "__init__.py"
        assert init.exists(), f"缺少 {init}"
        docstring = ast.get_docstring(ast.parse(init.read_text(encoding="utf-8")))
        assert docstring, f"{init} 需要一句话说明职责"
