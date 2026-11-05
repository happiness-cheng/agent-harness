"""M3 固定合成记录及独立 SQLite 验收。"""
import pytest
import copy
import os
from pathlib import Path
import subprocess
import sys

from min_agent.activity_records import make_record
from min_agent.activity_m3 import M3Store
from min_agent.activity_identity import canonical, derive, digest
from min_agent.activity_store import StoreConflict, IntegrityError, M1Store
from min_agent.activity_m2 import M2Store


def envelope(seq=1, payload="same", *, profile="codex/rollout", namespace="codex.ordinal", missing=False, run="run1"):
    field = {"codex.ordinal": "ordinal", "dsh.seq": "seq", "dsh.seq0": "seq0"}[namespace]
    locator = {field: seq, "line_index": seq, "byte_offset_in_decompressed_stream": seq * 10}
    if missing:
        del locator["line_index"]
    return dict(profile=profile, seq_namespace=namespace, locator=locator,
                structure={"type": "message"}, payload={"text": payload},
                observation_run_id=run, observation_ordinal=seq)


def content(*rows):
    return b"".join(canonical(r) + b"\n" for r in rows)


def make(path, *rows, profile="codex/rollout", namespace="codex.ordinal"):
    return M3Store.create(path, profile=profile, seq_namespace=namespace,
        collector_namespace="synthetic-m3", manifest_epoch="fixture1", platform="synthetic",
        object_namespace="thread", logical_id="M3-Example", object_role="history",
        native_branch_key="root", root_evidence="native_branch_key", first_content=content(*rows))


def identity(row):
    return make_record(row, source_member_id="member", source_generation_id="generation")


@pytest.mark.parametrize("profile,namespace", [("codex/rollout", "codex.ordinal"),
    ("dsh/v4", "dsh.seq"), ("dsh/old", "dsh.seq"), ("dsh/old", "dsh.seq0")])
def test_profiles_replay(tmp_path, profile, namespace):
    row = envelope(profile=profile, namespace=namespace)
    store = make(tmp_path / "s.db", row, row, profile=profile, namespace=namespace)
    store.consume()
    store = M3Store.open(store.path)
    assert len(store.records()) == 1
    before = store.export_bundle()
    store.consume()
    assert store.export_bundle() == before
    assert store.checkpoint()["position"] == len(content(row, row))


def test_fingerprint_never_changes_locator_id():
    a, b = identity(envelope()), identity(envelope(payload="changed"))
    assert a["native_record_id"] == b["native_record_id"] == a["record_locator_key"]
    assert a["canonical_event_id"] == b["canonical_event_id"]
    assert a["record_fingerprint"] != b["record_fingerprint"]


def test_conflict_rolls_back_batch_and_freezes_restart(tmp_path):
    a = envelope()
    store = make(tmp_path / "s.db", a)
    store.consume()
    cp = store.checkpoint()
    store.append_snapshot(content(a, envelope(2), envelope(payload="changed")))
    with pytest.raises(StoreConflict, match="record_conflict"):
        store.consume()
    for _ in range(2):
        store = M3Store.open(store.path)
        assert len(store.records()) == 1
        assert len(store.events()) == 1
        assert store.checkpoint() == cp
        with pytest.raises(StoreConflict, match="record_conflict"):
            store.advance_checkpoint(cp["position"], expected_revision=cp["revision"], expected_hash=cp["hash"])
        with pytest.raises(StoreConflict, match="record_conflict"):
            store.consume()


def test_different_locators_same_fingerprint_remain(tmp_path):
    store = make(tmp_path / "s.db", envelope(1), envelope(2))
    store.consume()
    records = store.records()
    assert len(records) == 2
    assert records[0]["record_fingerprint"] == records[1]["record_fingerprint"]
    assert all(r["is_same_payload"] for r in records)


def test_provisional_and_ambiguous_mapping(tmp_path):
    store = make(tmp_path / "s.db", envelope(missing=True), envelope(1), envelope(2))
    store.consume()
    rows = store.records()
    provisional = next(r for r in rows if r["identity_provisional"])
    assert provisional["native_record_id"] is None
    assert provisional["dedup_confidence"] == "LOW"
    result = store.map_identity(provisional["provisional_record_id"])
    assert result["status"] == "identity_mapping_ambiguous"
    assert result["candidates"] == frozenset(r["native_record_id"] for r in rows if not r["identity_provisional"])
    assert not M3Store.open(store.path).mappings()
    assert len(store.records()) == 3


@pytest.mark.parametrize("field", ["ordinal", "line_index", "byte_offset_in_decompressed_stream"])
def test_missing_required_is_provisional(field):
    row = envelope()
    del row["locator"][field]
    record = identity(row)
    assert record["identity_provisional"]
    assert record["record_locator_key"] is record["native_record_id"] is record["canonical_event_id"] is None
    assert record["provisional_record_id"].startswith("id1_")


@pytest.mark.parametrize("value", [-1, True, "1", 1.5])
def test_invalid_locator_numbers_rejected(value):
    row = envelope()
    row["locator"]["ordinal"] = value
    with pytest.raises(ValueError):
        identity(row)


def test_optional_fields_and_decompressed_representation():
    a = envelope()
    b = copy.deepcopy(a)
    b["locator"].update(metadata={"compression": "zst"}, timestamp="synthetic")
    assert identity(a)["native_record_id"] == identity(b)["native_record_id"]
    old = envelope(profile="dsh/old", namespace="dsh.seq")
    del old["locator"]["byte_offset_in_decompressed_stream"]
    assert identity(old)["locator_confidence"] == "HIGH"
    assert identity(old)["locator_fields"]["byte_offset_in_decompressed_stream"] is None


def test_seq_and_seq0_have_separate_streams_and_checkpoints(tmp_path):
    a = envelope(profile="dsh/old", namespace="dsh.seq")
    b = envelope(profile="dsh/old", namespace="dsh.seq0")
    left = make(tmp_path / "seq.db", a, profile="dsh/old", namespace="dsh.seq")
    right = make(tmp_path / "seq0.db", b, profile="dsh/old", namespace="dsh.seq0")
    assert left.summary()["member_id"] == right.summary()["member_id"]
    assert left.summary()["stream_id"] != right.summary()["stream_id"]
    assert left.checkpoint()["id"] != right.checkpoint()["id"]
    left.consume()
    assert right.checkpoint()["position"] == 0
    right.consume()
    assert left.records()[0]["native_record_id"] != right.records()[0]["native_record_id"]


def test_namespace_cannot_be_mixed(tmp_path):
    store = make(tmp_path / "s.db", envelope(profile="dsh/old", namespace="dsh.seq0"))
    with pytest.raises(ValueError, match="profile_mismatch"):
        store.consume()
    assert not M3Store.open(store.path).records()
    assert store.checkpoint()["position"] == 0


def test_unique_mapping_needs_bound_evidence_and_preserves_history(tmp_path):
    store = make(tmp_path / "s.db", envelope(missing=True), envelope())
    store.consume()
    rows = store.records()
    source = next(r["id"] for r in rows if r["identity_provisional"])
    target = next(r["id"] for r in rows if not r["identity_provisional"])
    assert store.map_identity(source)["status"] == "evidence_required"
    evidence = dict(kind="lineage", source_id=source, target_id=target, reference="synthetic-lineage-1")
    assert store.map_identity(source, evidence={**evidence, "target_id": "wrong"})["status"] == "evidence_required"
    before = store.export_bundle()["record_log"]
    result = store.map_identity(source, evidence=evidence)
    assert result["status"] == "mapped"
    store = M3Store.open(store.path)
    assert store.export_bundle()["record_log"][:len(before)] == before
    assert store.records() == rows
    assert store.map_identity(source, evidence=evidence) == result
    assert len(store.mappings()) == 1
    clone = M3Store.rebuild(tmp_path / "clone.db", store.export_bundle())
    assert clone.export_bundle() == store.export_bundle()


def test_ambiguous_never_chooses_even_with_target_evidence(tmp_path):
    store = make(tmp_path / "s.db", envelope(missing=True), envelope(), envelope(2))
    store.consume()
    rows = store.records()
    source = next(r["id"] for r in rows if r["identity_provisional"])
    target = next(r["id"] for r in rows if not r["identity_provisional"])
    result = store.map_identity(source, evidence=dict(kind="native_id", source_id=source, target_id=target, reference="synthetic"))
    assert result["status"] == "identity_mapping_ambiguous"
    assert len(result["candidates"]) == 2
    assert not store.mappings()


def test_provisional_runs_not_deduplicated_by_content(tmp_path):
    store = make(tmp_path / "s.db", envelope(missing=True), envelope(missing=True, run="run2"))
    store.consume()
    assert len(store.records()) == 2
    for row in store.records():
        assert store.map_identity(row["id"])["status"] == "unresolved"


def test_same_provisional_key_conflict_freezes(tmp_path):
    store = make(tmp_path / "s.db", envelope(missing=True), envelope(payload="changed", missing=True))
    with pytest.raises(StoreConflict, match="record_conflict"):
        store.consume()
    store = M3Store.open(store.path)
    assert not store.records()
    assert not store.events()
    assert store.checkpoint()["position"] == 0
    with pytest.raises(StoreConflict, match="record_conflict"):
        store.append_snapshot(b"anything\n")


@pytest.mark.parametrize("point,committed,advanced", [
    ("events.after_insert", False, False), ("record_log.after_record", False, False),
    ("events.before_commit", False, False), ("events.after_commit", True, False),
    ("checkpoint.after_write", True, False), ("checkpoint.before_commit", True, False),
    ("checkpoint.after_commit", True, True),
])
def test_process_crashes_reopen_and_replay(tmp_path, point, committed, advanced):
    row = envelope()
    store = make(tmp_path / "s.db", row)
    script = """
import os,sys
from min_agent.activity_m3 import M3Store
s=M3Store.open(sys.argv[1])
s.fault=lambda point: os._exit(73) if point==sys.argv[2] else None
s.consume()
"""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")}
    result = subprocess.run([sys.executable, "-c", script, str(store.path), point], env=env, capture_output=True, timeout=20)
    assert result.returncode == 73, result.stderr
    store = M3Store.open(store.path)
    assert len(store.records()) == int(committed)
    assert store.checkpoint()["position"] == (len(content(row)) if advanced else 0)
    store.consume()
    store = M3Store.open(store.path)
    assert len(store.records()) == 1
    assert store.checkpoint()["position"] == len(content(row))


@pytest.mark.parametrize("point,durable", [("record_log.after_record_conflict", False),
    ("events.before_commit", False), ("events.after_commit", True)])
def test_conflict_commit_crash_has_no_checkpoint_escape(tmp_path, point, durable):
    store = make(tmp_path / "s.db", envelope(), envelope(payload="changed"))
    script = """
import os,sys
from min_agent.activity_m3 import M3Store
s=M3Store.open(sys.argv[1])
s.fault=lambda point: os._exit(73) if point==sys.argv[2] else None
s.consume()
"""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")}
    result = subprocess.run([sys.executable, "-c", script, str(store.path), point], env=env, capture_output=True, timeout=20)
    assert result.returncode == 73, result.stderr
    store = M3Store.open(store.path)
    assert bool(store.export_bundle()["record_log"]) == durable
    cp = store.checkpoint()
    with pytest.raises(StoreConflict):
        store.advance_checkpoint(len(content(envelope(), envelope(payload="changed"))), expected_revision=cp["revision"], expected_hash=cp["hash"])
    with pytest.raises(StoreConflict, match="record_conflict"):
        store.consume()
    assert not M3Store.open(store.path).records()
    assert store.checkpoint()["position"] == 0


def test_old_entrypoints_cannot_bypass_records(tmp_path):
    store = make(tmp_path / "s.db", envelope())
    for cls in (M1Store, M2Store):
        with pytest.raises(IntegrityError):
            cls.open(store.path)
    with pytest.raises(IntegrityError, match="transport_record_not_committed"):
        M2Store.persist_events(store, store.read_events())
    store = M3Store.open(store.path)
    assert not store.events()
    assert store.checkpoint()["position"] == 0


def test_m2_transition_integrates_with_m3(tmp_path):
    row = envelope()
    store = make(tmp_path / "s.db", row)
    store.consume()
    old = store.records()[0]
    cp = store.checkpoint()
    args = dict(transition_id="synthetic-transition", from_generation=cp["key"]["source_generation_id"],
        expected_revision=cp["revision"], expected_hash=cp["hash"], first_content=content(row), migration_plan_ref="synthetic-root")
    new_generation = store.transition(**args)
    store = M3Store.open(store.path)
    assert store.transition(**args) == new_generation
    assert store.checkpoint()["position"] == 0
    store.consume()
    assert len(store.records()) == 2
    assert old["id"] in {r["id"] for r in store.records()}


def test_conflict_blocks_already_open_writer_and_rebuild(tmp_path):
    row = envelope()
    store = make(tmp_path / "s.db", row)
    store.consume()
    stale = M3Store.open(store.path)
    store.append_snapshot(content(row, envelope(payload="changed")))
    with pytest.raises(StoreConflict):
        store.consume()
    with pytest.raises(StoreConflict, match="record_conflict"):
        stale.persist_events([])
    clone = M3Store.rebuild(tmp_path / "clone.db", store.export_bundle())
    with pytest.raises(StoreConflict, match="record_conflict"):
        clone.consume()


def test_locator_exact_frozen_inputs_and_independent_hashes():
    row = envelope()
    record = identity(row)
    fields = dict(source_member_id="member", source_stream_id=derive("stream", dict(source_member_id="member", seq_namespace="codex.ordinal")),
        source_generation_id="generation", format_version="rollout", seq_namespace="codex.ordinal",
        seq_value=1, line_index=1, byte_offset_in_decompressed_stream=10)
    assert record["native_record_id"] == derive("record_locator", fields)
    parts = dict(struct_hash=digest(row["structure"]), payload_hash=digest(row["payload"]))
    assert record["record_fingerprint"] == {**parts, "record_hash": digest(parts)}


def test_structure_change_also_conflicts(tmp_path):
    a, b = envelope(), envelope()
    b["structure"] = {"type": "tool"}
    store = make(tmp_path / "s.db", a, b)
    with pytest.raises(StoreConflict, match="record_conflict"):
        store.consume()
    assert not M3Store.open(store.path).records()


def test_seq0_never_falls_back_to_seq():
    row = envelope(profile="dsh/old", namespace="dsh.seq0")
    row["locator"]["seq"] = row["locator"].pop("seq0")
    assert identity(row)["identity_provisional"]


def test_rebuild_rejects_missing_record_even_with_valid_transport(tmp_path):
    store = make(tmp_path / "s.db", envelope())
    store.consume()
    bundle = store.export_bundle()
    bundle["record_log"] = []
    with pytest.raises(IntegrityError, match="transport_record_not_committed"):
        M3Store.rebuild(tmp_path / "bad.db", bundle)
    assert not (tmp_path / "bad.db").exists()


def test_mapping_does_not_match_across_generations(tmp_path):
    a = envelope(missing=True)
    store = make(tmp_path / "s.db", a)
    store.consume()
    source = store.records()[0]["id"]
    cp = store.checkpoint()
    store.transition(transition_id="t", from_generation=cp["key"]["source_generation_id"],
        expected_revision=cp["revision"], expected_hash=cp["hash"], first_content=content(envelope()), migration_plan_ref="synthetic")
    store.consume()
    assert store.map_identity(source)["status"] == "unresolved"


def test_transaction_exception_rolls_back_transport_and_record_log(tmp_path):
    store = make(tmp_path / "s.db", envelope())
    before = store.export_bundle()
    def fault(point):
        if point == "record_log.after_record":
            raise RuntimeError("synthetic-injection")
    store.fault = fault
    with pytest.raises(RuntimeError, match="synthetic-injection"):
        store.consume()
    store = M3Store.open(store.path)
    assert store.export_bundle() == before
    store.consume()
    assert len(store.records()) == 1
