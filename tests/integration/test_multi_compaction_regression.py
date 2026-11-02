"""仅验证主上下文多次压缩：固定样本和选择规则，不代表模型语义效果。"""

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("multi_compaction_probe", ROOT / "scripts/audit_multi_compaction.py")
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)
SUITE = json.loads((ROOT / "docs/acceptance/c04-multi-compaction-suite-v1.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def evidence():
    return PROBE.run_regression()


@pytest.mark.parametrize("case", SUITE["cases"], ids=lambda case: case["id"])
def test_multi_compaction_contract(case, evidence):
    assert evidence["outcomes"][case["id"]] == case["expected"], {
        "case": case, "covered": evidence["covered"],
        "missing": evidence["round_coverage_missing"], "errors": evidence["errors"]}
