"""上下文输入契约，不测试下游分类语义或历史摘要算法。"""

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("task_input_probe", ROOT / "scripts/audit_task_input.py")
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)
SUITE = json.loads((ROOT / "docs/acceptance/c05-task-input-suite-v1.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", SUITE["cases"], ids=lambda case: case["id"])
def test_uncovered_task_material_contract(case):
    record = PROBE.run_case(case)
    assert record["passed"], {"expected": record["expected"], "observed": record["observed"]}
