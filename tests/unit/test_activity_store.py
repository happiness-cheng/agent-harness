"""A–E 固定验收及反例；所有数据库均在 pytest 临时目录。"""

import copy
import json
import sqlite3
from contextlib import closing

import pytest

from min_agent.activity_store import IntegrityError, M1Store, StateConflict, StoreConflict
from min_agent.activity_identity import digest


def make_store(path):
    return M1Store.create(
        path, collector_namespace="synthetic-m1", manifest_epoch="fixture-1",
        platform="synthetic", object_namespace="thread", logical_id="M1-Example",
        object_role="history", native_branch_key="root", root_evidence="native_branch_key",
        first_content=b"first\n",
    )


def test_a_identity_and_revision_stable_on_append_checkpoint_and_state(tmp_path):
    store = make_store(tmp_path / "a.db")
    initial = store.summary()
    cp = store.checkpoint()
    for content in (b"first\nsecond\n", b"first\nsecond\nthird\n"):
        store.append_snapshot(content)
        cp = store.advance_checkpoint(len(content), expected_revision=cp["revision"], expected_hash=cp["hash"])
    store.append_state(initial["member_id"], "identity_status", "provisional", evidence_ref="fixture")
    store.append_state(initial["member_id"], "identity_status", "definitive", evidence_ref="fixture")
    after = store.summary()
    for key in ("manifest_id", "manifest_revision", "manifest_hash", "object_id", "member_id", "generation_id", "stream_id", "checkpoint_id"):
        assert after[key] == initial[key]
    assert store.checkpoint()["position"] == len(b"first\nsecond\nthird\n")


def test_b_root_and_latest_separate_and_duplicate_snapshot_idempotent(tmp_path):
    store = make_store(tmp_path / "b.db")
    root = store.summary()["snapshot_chain_root_id"]
    latest = store.append_snapshot(b"first\nsecond\n")
    before_duplicate = store.export_bundle()
    assert store.append_snapshot(b"first\nsecond\n") == latest
    assert store.export_bundle() == before_duplicate
    assert store.summary()["snapshot_chain_root_id"] == root
    assert store.summary()["latest_snapshot_id"] == latest != root
    assert M1Store.open(store.path).summary() == store.summary()


def test_c_fold_conflict_rejects_without_mutating_and_freezes_writer(tmp_path):
    store = make_store(tmp_path / "c.db")
    target = store.summary()["member_id"]
    store.append_state(target, "identity_status", "provisional", evidence_ref="fixture")
    store.append_state(target, "identity_status", "definitive", evidence_ref="fixture-upgrade")
    assert store.fold_state()[(target, "identity_status")] == "definitive"
    before = store.export_bundle()
    with pytest.raises(StateConflict, match="state_fold_conflict"):
        store.append_state(target, "identity_status", "provisional", evidence_ref="bad-reversal")
    assert M1Store.open(store.path).export_bundle() == before
    with pytest.raises(StoreConflict, match="frozen"):
        store.append_snapshot(b"first\nsecond\n")


def test_d_rebuild_from_durable_json_preserves_all_ids_and_derived_state(tmp_path):
    store = make_store(tmp_path / "original.db")
    store.register_stream("synthetic.other")
    store.append_snapshot(b"first\nsecond\n")
    cp = store.checkpoint()
    store.advance_checkpoint(6, expected_revision=cp["revision"], expected_hash=cp["hash"])
    store.append_state(store.summary()["member_id"], "identity_status", "definitive", evidence_ref="fixture")
    # 独立 JSON 往返，重建新 schema/行，不复制 SQLite 文件。
    saved = json.loads(json.dumps(store.export_bundle()))
    rebuilt = M1Store.rebuild(tmp_path / "rebuilt.db", saved)
    assert rebuilt.summary() == store.summary()
    assert rebuilt.identity_inventory() == store.identity_inventory()
    assert rebuilt.fold_state() == store.fold_state()
    assert rebuilt.checkpoint() == store.checkpoint()
    assert rebuilt.export_bundle() == store.export_bundle()
    before_revision = rebuilt.summary()["manifest_revision"]
    rebuilt.register_stream("synthetic.third")
    assert rebuilt.summary()["manifest_revision"] == before_revision + 1


@pytest.mark.parametrize("field,value", [
    ("revision_no", 44), ("manifest_id", "id1_wrong"),
    ("parent_revision_no", 42), ("parent_manifest_hash", "0" * 64),
])
def test_e_corrupt_manifest_body_blocks_open_and_existing_writer(tmp_path, field, value):
    store = make_store(tmp_path / "e.db")
    store.register_stream("synthetic.second")
    with closing(sqlite3.connect(store.path)) as conn, conn:
        body = json.loads(conn.execute("SELECT body FROM manifest_revisions WHERE seq=2").fetchone()[0])
        body[field] = value
        conn.execute("UPDATE manifest_revisions SET body=? WHERE seq=2", (json.dumps(body),))
        count = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    with pytest.raises(IntegrityError):
        M1Store.open(store.path)
    with pytest.raises(IntegrityError):
        store.append_snapshot(b"first\nsecond\n")
    with closing(sqlite3.connect(store.path)) as conn, conn:
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == count


@pytest.mark.parametrize("sql", [
    "UPDATE manifest_revisions SET hash='bad' WHERE seq=1",
    "UPDATE manifest_head SET manifest_id='other'",
    "UPDATE manifest_head SET seq=0",
    "UPDATE observations SET hash='bad' WHERE seq=1",
    "UPDATE checkpoints SET hash='bad'",
])
def test_e_corrupt_hashes_or_committed_pointer_fail_closed(tmp_path, sql):
    store = make_store(tmp_path / "e.db")
    with closing(sqlite3.connect(store.path)) as conn, conn:
        conn.execute(sql)
    with pytest.raises(IntegrityError):
        store.append_snapshot(b"first\nsecond\n")


def test_checkpoint_cas_and_bounds(tmp_path):
    store = make_store(tmp_path / "cp.db")
    stale = store.checkpoint()
    peer = M1Store.open(store.path)
    store.advance_checkpoint(3, expected_revision=stale["revision"], expected_hash=stale["hash"])
    with pytest.raises(StoreConflict, match="checkpoint_cas"):
        peer.advance_checkpoint(5, expected_revision=stale["revision"], expected_hash=stale["hash"])
    cp = store.checkpoint()
    for bad in (-1, 2, 7, True):
        with pytest.raises(ValueError):
            store.advance_checkpoint(bad, expected_revision=cp["revision"], expected_hash=cp["hash"])


def test_closed_checkpoint_cannot_advance(tmp_path):
    store = make_store(tmp_path / "closed.db")
    cp = store.checkpoint()
    store.append_state(cp["id"], "checkpoint_status", "closed", evidence_ref="fixture")
    with pytest.raises(StoreConflict, match="checkpoint_closed"):
        store.advance_checkpoint(3, expected_revision=cp["revision"], expected_hash=cp["hash"])


def test_bad_bundle_does_not_create_target_and_existing_db_not_overwritten(tmp_path):
    store = make_store(tmp_path / "existing.db")
    bundle = store.export_bundle()
    corrupted = copy.deepcopy(bundle)
    corrupted["manifest"][0]["hash"] = "bad"
    with pytest.raises(IntegrityError):
        M1Store.rebuild(tmp_path / "bad.db", corrupted)
    assert not (tmp_path / "bad.db").exists()
    with pytest.raises(FileExistsError):
        M1Store.rebuild(store.path, bundle)
    assert store.export_bundle() == bundle


def test_unsupported_or_ambiguous_inputs_never_silently_make_identity(tmp_path):
    store = make_store(tmp_path / "input.db")
    before = store.export_bundle()
    with pytest.raises(ValueError, match="append_only"):
        store.append_snapshot(b"changed\n")
    with pytest.raises(ValueError):
        store.append_state("unknown", "identity_status", "definitive", evidence_ref="fixture")
    assert store.export_bundle() == before


def test_duplicate_json_keys_are_corruption_even_if_last_value_matches(tmp_path):
    store = make_store(tmp_path / "duplicate.db")
    with closing(sqlite3.connect(store.path)) as conn, conn:
        raw = conn.execute("SELECT body FROM manifest_revisions WHERE seq=1").fetchone()[0]
        conn.execute("UPDATE manifest_revisions SET body=? WHERE seq=1", ('{"revision_no":999,' + raw[1:],))
    with pytest.raises(IntegrityError):
        store.append_snapshot(b"first\nsecond\n")


def test_boolean_revision_rejected_even_with_recomputed_hash(tmp_path):
    store = make_store(tmp_path / "bool.db")
    bundle = store.export_bundle()
    bundle["manifest"][0]["body"]["revision_no"] = True
    bundle["manifest"][0]["hash"] = digest(bundle["manifest"][0]["body"])
    bundle["head"]["hash"] = bundle["manifest"][0]["hash"]
    with pytest.raises(IntegrityError):
        M1Store.rebuild(tmp_path / "invalid.db", bundle)


def test_simultaneous_checkpoint_cas_has_one_winner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    store = make_store(tmp_path / "race.db")
    cp = store.checkpoint()
    peers = [M1Store.open(store.path), M1Store.open(store.path)]
    barrier = Barrier(2)

    def attempt(peer):
        barrier.wait(timeout=5)
        try:
            peer.advance_checkpoint(3, expected_revision=cp["revision"], expected_hash=cp["hash"])
            return "committed"
        except StoreConflict as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, peers)) == ["checkpoint_cas", "committed"]
    assert store.checkpoint()["revision"] == cp["revision"] + 1


def test_registration_replay_is_idempotent(tmp_path):
    store = make_store(tmp_path / "reg.db")
    store.register_stream("synthetic.second")
    before = store.export_bundle()
    store.register_stream("synthetic.second")
    assert store.export_bundle() == before


def test_wrong_database_and_missing_database_are_not_initialized(tmp_path):
    missing = tmp_path / "missing.db"
    with pytest.raises(IntegrityError):
        M1Store.open(missing)
    assert not missing.exists()
    other = tmp_path / "unrelated.db"
    with closing(sqlite3.connect(other)) as conn, conn:
        conn.execute("CREATE TABLE unrelated(value)")
    before = other.read_bytes()
    with pytest.raises(IntegrityError):
        M1Store.open(other)
    assert other.read_bytes() == before
