"""R4 审核调用方：每个真实 run/resume 段都有不可变前后态并合取。"""
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("audit_state_bar_r4", ROOT / "scripts" / "audit_state_bar.py")
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)
SUITE = json.loads((ROOT / "docs/acceptance/b01-state-bar-suite-v1.json").read_text(encoding="utf-8"))


def case(scenario):
    return next(item for item in SUITE["cases"] if item["scenario"] == scenario)


@pytest.mark.parametrize("scenario", ["unknown", "ended", "checkpoint"])
def test_every_real_segment_has_immutable_snapshot_and_is_conjoined(scenario):
    record = PROBE.run_case(case(scenario))
    assert record["passed"] is True
    segments = record["observed"]["lifecycle_segments"]
    assert len(segments) == 2, (scenario, len(segments))
    assert all(item["valid"] is True and item["error"] is None for item in segments)
    for item in segments:
        assert item["before"].get("grant") is not None or scenario == "unknown"


def test_validation_exception_fails_closed(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("synthetic lifecycle read failure")
    monkeypatch.setattr(PROBE, "grant_lifecycle_valid", boom)
    record = PROBE.run_case(case("ended"))
    assert record["passed"] is False
    assert record["observed"]["grant_lifecycle_valid"] is False
    assert all(item["error"] for item in record["observed"]["lifecycle_segments"])


def test_second_segment_projection_mutation_is_detected(monkeypatch):
    original_run = PROBE.Environment.run
    runs = []
    def run(self, *args, **kwargs):
        runs.append(1)
        result = original_run(self, *args, **kwargs)
        if len(runs) == 2:
            snapshot = self.store._sessions[self.context.session_id]
            grant = snapshot.work_state.grant.model_copy(update={"grant_id": __import__("uuid").uuid4()})
            snapshot.work_state = snapshot.work_state.model_copy(update={"grant": grant})
        return result
    monkeypatch.setattr(PROBE.Environment, "run", run)
    record = PROBE.run_case(case("ended"))
    assert len(runs) == 2
    assert record["passed"] is False
    assert record["observed"]["lifecycle_segments"][-1]["valid"] is False
