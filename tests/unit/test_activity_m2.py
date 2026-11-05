"""M2 固定合成验收：真实文件、重开连接、子进程强退。"""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from min_agent.activity_identity import canonical, derive, digest
from min_agent.activity_store import IntegrityError, M1Store, StoreConflict
from min_agent.activity_m2 import M2Store


def make(path):
    return M2Store.create(path, collector_namespace="synthetic-m2", manifest_epoch="fixture-1",
                          platform="synthetic", object_namespace="thread", logical_id="M2-Example",
                          object_role="history", native_branch_key="root", root_evidence="native_branch_key",
                          first_content=b"one\ntwo\nthree\n")


def reopened(store):
    return M2Store.open(store.path)


def transition_args(store, transition_id="t1"):
    cp = store.checkpoint()
    return dict(transition_id=transition_id, from_generation=cp["key"]["source_generation_id"],
                expected_revision=cp["revision"], expected_hash=cp["hash"],
                first_content=b"new\n", migration_plan_ref="synthetic-reset-to-root")


def test_normal_ingest_and_replay(tmp_path):
    store = make(tmp_path / "normal.db")
    original = store.summary()
    store.consume()
    restarted = reopened(store)
    assert len(restarted.events()) == 3
    assert restarted.checkpoint()["position"] == 14
    before = restarted.export_bundle()
    restarted.consume()
    assert reopened(restarted).export_bundle() == before
    assert restarted.summary()["manifest_id"] == original["manifest_id"]


def test_checkpoint_cannot_skip_events_or_record_boundaries(tmp_path):
    store = make(tmp_path / "order.db")
    cp = store.checkpoint()
    with pytest.raises(StoreConflict, match="events_not_committed"):
        store.advance_checkpoint(14, expected_revision=cp["revision"], expected_hash=cp["hash"])
    records = store.read_events()
    store.persist_events([records[2]])
    with pytest.raises(StoreConflict, match="events_not_committed"):
        store.advance_checkpoint(14, expected_revision=cp["revision"], expected_hash=cp["hash"])
    store.consume()
    assert reopened(store).checkpoint()["position"] == 14


@pytest.mark.parametrize("stage,event_count,position", [
    ("events.after_insert", 0, 0), ("events.before_commit", 0, 0),
    ("events.after_commit", 3, 0), ("consume.after_events", 3, 0),
    ("checkpoint.after_write", 3, 0), ("checkpoint.before_commit", 3, 0),
    ("checkpoint.after_commit", 3, 14),
])
def test_ingest_exception_recovery(tmp_path, stage, event_count, position):
    store = make(tmp_path / "fault.db")

    def fault(point):
        if point == stage:
            raise RuntimeError("injected")

    store.fault = fault
    with pytest.raises(RuntimeError, match="injected"):
        store.consume()
    restarted = reopened(store)
    assert len(restarted.events()) == event_count
    assert restarted.checkpoint()["position"] == position
    restarted.consume()
    final = reopened(restarted)
    assert len(final.events()) == 3
    assert final.checkpoint()["position"] == 14


def crash_child(path, stage, action):
    code = '''
import os, sys
from min_agent.activity_m2 import M2Store
s=M2Store.open(sys.argv[1])
def fault(point):
    if point == sys.argv[2]: os._exit(73)
s.fault=fault
if sys.argv[3] == "consume": s.consume()
else:
    cp=s.checkpoint()
    s.transition(transition_id="t1", from_generation=cp["key"]["source_generation_id"],
        expected_revision=cp["revision"], expected_hash=cp["hash"],
        first_content=b"new\\n", migration_plan_ref="synthetic-reset-to-root")
raise SystemExit(99)
'''
    result = subprocess.run([sys.executable, "-c", code, str(path), stage, action],
                            capture_output=True, text=True, timeout=20,
                            cwd=Path(__file__).resolve().parents[2])
    assert result.returncode == 73, result.stderr


@pytest.mark.parametrize("stage,event_count,position", [
    ("events.after_insert", 0, 0), ("events.before_commit", 0, 0),
    ("consume.after_events", 3, 0), ("checkpoint.after_write", 3, 0),
    ("checkpoint.before_commit", 3, 0), ("checkpoint.after_commit", 3, 14),
])
def test_process_exit_ingest_recovery(tmp_path, stage, event_count, position):
    store = make(tmp_path / "crash.db")
    crash_child(store.path, stage, "consume")
    restarted = reopened(store)
    assert len(restarted.events()) == event_count
    assert restarted.checkpoint()["position"] == position
    restarted.consume()
    assert len(reopened(restarted).events()) == 3


@pytest.mark.parametrize("stage", [
    "transition.after_manifest", "transition.after_pending", "transition.after_old_closed",
    "transition.after_new_active", "transition.after_checkpoint", "transition.before_commit",
    "transition.after_commit",
])
def test_transition_process_exit_atomicity(tmp_path, stage):
    store = make(tmp_path / "transition.db")
    store.consume()
    before = store.export_bundle()
    old = store.summary()["generation_id"]
    args = transition_args(store)
    crash_child(store.path, stage, "transition")
    restarted = reopened(store)
    if stage == "transition.after_commit":
        assert restarted.summary()["generation_id"] != old
        assert len(restarted.generations()) == 2
    else:
        assert restarted.export_bundle() == before
        assert restarted.summary()["generation_id"] == old
    new_id = restarted.transition(**args)
    again = reopened(restarted)
    assert len(again.generations()) == 2
    assert again.summary()["generation_id"] == new_id
    assert again.checkpoint()["position"] == 0
    stable = again.export_bundle()
    assert again.transition(**args) == new_id
    assert again.export_bundle() == stable
    again.consume()
    assert len(reopened(again).events()) == 4


def test_independent_reader_never_sees_partial_transition(tmp_path):
    store = make(tmp_path / "isolation.db")
    store.consume()
    old = store.summary()["generation_id"]
    observed = []

    def observer(stage):
        if stage.startswith("transition.") and stage != "transition.after_commit":
            # 不使用同一连接，也不申请写锁；读取已提交快照。
            view = M2Store(store.path).summary()
            observed.append(view["generation_id"])

    store.fault = observer
    new = store.transition(**transition_args(store))
    assert len(observed) >= 6 and set(observed) == {old}
    assert reopened(store).summary()["generation_id"] == new


def test_wrong_id_reuse_stale_cas_and_unconsumed_generation(tmp_path):
    store = make(tmp_path / "guards.db")
    with pytest.raises(StoreConflict, match="generation_not_drained"):
        store.transition(**transition_args(store))
    stale = transition_args(store)
    store.consume()
    with pytest.raises(StoreConflict, match="checkpoint_cas"):
        store.transition(**stale)
    args = transition_args(store)
    store.transition(**args)
    with pytest.raises(StoreConflict, match="transition_id_conflict"):
        reopened(store).transition(**{**args, "first_content": b"different\n"})
    with pytest.raises(IntegrityError):
        M1Store.open(store.path)


def test_two_transitions_keep_append_only_history(tmp_path):
    store = make(tmp_path / "chain.db")
    store.consume()
    before = store.export_bundle()
    store.transition(**transition_args(store, "t1"))
    store.consume()
    store.transition(**transition_args(store, "t2"))
    final = reopened(store).export_bundle()
    assert len(reopened(store).generations()) == 3
    assert final["manifest"][:len(before["manifest"])] == before["manifest"]
    assert final["observations"][:len(before["observations"])] == before["observations"]


def fixture_observation(bundle, kind, payload):
    body = dict(manifest_id=bundle["head"]["manifest_id"], observation_revision=0,
                parent_observation_hash=None, kind=kind, payload=payload)
    bundle["observations"].append(dict(seq=0, body=body, hash=""))


def fixture_state(bundle, target, dimension, value):
    payload = dict(state_event_log_root_id=bundle["manifest"][0]["body"]["state_event_log_root_id"],
                   state_event_seq=0, target_id=target, state_dimension=dimension, state_value=value,
                   parent_event_hash=None, evidence_ref="synthetic-recovery-fixture",
                   observed_at="2026-11-05T00:00:00+00:00", event_hash="")
    fixture_observation(bundle, "state", payload)


def install_fixture(store, bundle):
    """在合成副本重算链，模拟旧版本遗留的逻辑异常，不只破坏哈希。"""
    parent, state_parent, state_seq = None, None, 0
    for seq, row in enumerate(bundle["observations"], 1):
        body = row["body"]
        row["seq"] = body["observation_revision"] = seq
        body["parent_observation_hash"] = parent
        if body["kind"] == "state":
            payload = body["payload"]
            state_seq += 1
            payload.update(state_event_seq=state_seq, parent_event_hash=state_parent)
            payload["event_hash"] = digest({k: v for k, v in payload.items() if k != "event_hash"})
            state_parent = payload["event_hash"]
        row["hash"] = parent = digest(body)
    with closing(sqlite3.connect(store.path)) as conn, conn:
        # 仅测试造样本，生产 API 从不删除事件历史。
        conn.execute("DELETE FROM observations")
        for row in bundle["observations"]:
            conn.execute("INSERT INTO observations VALUES(?,?,?)", (row["seq"], canonical(row["body"]).decode(), row["hash"]))
        conn.execute("DELETE FROM checkpoints")
        for row in bundle["checkpoints"]:
            conn.execute("INSERT INTO checkpoints VALUES(?,?,?)", (row["id"], canonical(row["body"]).decode(), row["hash"]))


def test_pending_without_changes_is_aborted_on_reopen(tmp_path):
    store = make(tmp_path / "pending.db")
    store.consume()
    args, view = transition_args(store), store.summary()
    target = derive("generation", dict(source_member_id=view["member_id"], generation_epoch="t1", derived_from=view["generation_id"]))
    record = {k: args[k] for k in ("transition_id", "from_generation", "expected_revision", "expected_hash", "migration_plan_ref")}
    record.update(from_checkpoint_id=view["checkpoint_id"], to_generation=target,
                  to_checkpoint_id=derive("checkpoint", dict(source_member_id=view["member_id"], source_stream_id=view["stream_id"], source_generation_id=target)),
                  first_content_hex=b"new\n".hex(), initial_position=0)
    bundle = store.export_bundle()
    fixture_observation(bundle, "transition", record)
    fixture_state(bundle, "t1", "transition_status", "pending")
    install_fixture(store, bundle)
    before = len(bundle["observations"])
    restarted = reopened(store)
    assert restarted.fold_state()[("t1", "transition_status")] == "aborted"
    assert restarted.summary()["generation_id"] == view["generation_id"]
    assert restarted.checkpoint()["position"] == 14
    assert len(restarted.export_bundle()["observations"]) == before + 1
    stable = restarted.export_bundle()
    assert reopened(restarted).export_bundle() == stable
    with pytest.raises(StoreConflict, match="transition_aborted"):
        restarted.transition(**args)


def test_zero_active_uninitialized_backfills_from_root(tmp_path):
    store = make(tmp_path / "uninitialized.db")
    bundle = store.export_bundle()
    bundle["checkpoints"] = []
    bundle["observations"] = [r for r in bundle["observations"]
                              if r["body"]["payload"].get("state_dimension") != "checkpoint_status"]
    install_fixture(store, bundle)
    restarted = reopened(store)
    assert restarted.checkpoint()["position"] == 0
    assert restarted.checkpoint()["backfill"] is True
    assert restarted.checkpoint()["snapshot_id"] == restarted.summary()["snapshot_chain_root_id"]
    restarted.consume()
    assert len(reopened(restarted).events()) == 3


@pytest.mark.parametrize("scenario,error", [
    ("zero", "transition_incomplete"),
    ("multiple", "multiple_active_checkpoints"),
    ("committed_wrong_active", "transition_committed_inconsistent"),
])
def test_illegal_active_states_freeze_across_restarts(tmp_path, scenario, error):
    store = make(tmp_path / "invalid.db")
    store.consume()
    old = store.summary()
    new_gid = store.transition(**transition_args(store))
    new_cp = store.checkpoint()["id"]
    bundle = store.export_bundle()
    if scenario == "zero":
        # 旧 closed 已持久化，新 active/committed 尚未持久化。
        bundle["observations"] = [r for r in bundle["observations"] if not (
            r["body"]["kind"] == "state" and (
                r["body"]["payload"]["target_id"] in (new_gid, new_cp)
                or r["body"]["payload"]["state_dimension"] == "transition_status"
                and r["body"]["payload"]["state_value"] == "committed"))]
    else:
        # 新 active 已持久化，但旧 closed 丢失；构成两个 active。
        bundle["observations"] = [r for r in bundle["observations"] if not (
            r["body"]["kind"] == "state" and r["body"]["payload"]["target_id"] in (old["generation_id"], old["checkpoint_id"])
            and r["body"]["payload"]["state_value"] == "closed")]
        if scenario == "committed_wrong_active":
            fixture_state(bundle, new_cp, "checkpoint_status", "closed")
            fixture_state(bundle, new_gid, "generation_status", "closed")
    install_fixture(store, bundle)
    before = store.path.read_bytes()
    for _ in range(2):
        with pytest.raises(StoreConflict, match=error):
            reopened(store)
    with pytest.raises(StoreConflict, match="frozen"):
        store.consume()
    assert store.path.read_bytes() == before


def test_persisted_checkpoint_ahead_of_events_is_corruption(tmp_path):
    store = make(tmp_path / "ahead.db")
    bundle = store.export_bundle()
    cp = bundle["checkpoints"][0]
    cp["body"]["position"] = 14
    cp["hash"] = digest(cp["body"])
    install_fixture(store, bundle)
    with pytest.raises(IntegrityError, match="checkpoint_ahead_of_events"):
        reopened(store)


def test_m2_rebuild_preserves_transition_and_business_events(tmp_path):
    store = make(tmp_path / "original.db")
    store.consume()
    store.transition(**transition_args(store))
    store.consume()
    bundle = json.loads(json.dumps(store.export_bundle()))
    restored = M2Store.rebuild(tmp_path / "restore.db", bundle)
    assert restored.export_bundle() == bundle
    assert restored.identity_inventory() == store.identity_inventory()
    assert len(restored.events()) == 4


def test_partial_trailing_line_and_wrong_event_payload(tmp_path):
    store = make(tmp_path / "partial.db")
    store.append_snapshot(b"one\ntwo\nthree\npart")
    store.consume()
    assert reopened(store).checkpoint()["position"] == 14
    with pytest.raises(StoreConflict, match="generation_not_drained"):
        store.transition(**transition_args(store))
    store.append_snapshot(b"one\ntwo\nthree\npartial\n")
    record = store.read_events()[0]
    record["body"]["content_hex"] = b"fake\n".hex()
    record["hash"] = digest(record["body"])
    with pytest.raises(StoreConflict, match="event_key_or_payload_conflict"):
        store.persist_events([record])
    store.consume()
    assert len(reopened(store).events()) == 4


def test_concurrent_transition_has_one_cas_winner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    store = make(tmp_path / "transition-race.db")
    store.consume()
    peers = [reopened(store), reopened(store)]
    args = transition_args(store)
    barrier = Barrier(2)

    def run(index):
        barrier.wait(timeout=5)
        try:
            peers[index].transition(**{**args, "transition_id": f"race-{index}"})
            return "committed"
        except StoreConflict as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(run, (0, 1))) == ["checkpoint_cas", "committed"]
    assert len(reopened(store).generations()) == 2


def test_same_transition_id_concurrent_replay_is_idempotent(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    store = make(tmp_path / "same-transition.db")
    store.consume()
    peers = [reopened(store), reopened(store)]
    args = transition_args(store)
    barrier = Barrier(2)

    def run(peer):
        barrier.wait(timeout=5)
        return peer.transition(**args)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, peers))
    assert results[0] == results[1]
    assert len(reopened(store).generations()) == 2


def test_late_old_generation_event_write_is_rejected(tmp_path):
    store = make(tmp_path / "late.db")
    old_records = store.read_events()
    store.consume()
    peer = reopened(store)
    store.transition(**transition_args(store))
    with pytest.raises(StoreConflict, match="event_key_or_payload_conflict"):
        peer.persist_events(old_records)
    assert len(reopened(store).events()) == 3


def test_reopen_does_not_mutate_normal_committed_store(tmp_path):
    store = make(tmp_path / "normal-open.db")
    store.consume()
    store.transition(**transition_args(store))
    before = store.path.read_bytes()
    reopened(store)
    reopened(store)
    assert store.path.read_bytes() == before
