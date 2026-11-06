"""M4 固定合成样本；检查实际合成身份、历史与位点副作用。"""
from dataclasses import replace

import pytest

from min_agent.activity_changes import Schema, ReadSample, classify, initial_state, apply_change
from min_agent.activity_store import StoreConflict, _snapshot as m1_snapshot


SCHEMA = Schema("v1", ("kind", "payload"), "synthetic.ordinal")


def sample(content=b"one\ntwo\n", *, schema=SCHEMA, compression="none", physical=None, **kwargs):
    return ReadSample(content if physical is None else physical, content, compression, schema, **kwargs)


def run(old, new, *, checkpoint=None, marker=None):
    state = initial_state(old, checkpoint_position=len(old.logical) if checkpoint is None else checkpoint)
    result = apply_change(state, new, new, observation_id="change-1", expected_snapshot_id=state.latest_snapshot_id, rewrite_marker=marker)
    return state, result


@pytest.mark.parametrize("kind,new,marker", [
    ("append", sample(b"one\ntwo\nthree\n"), None),
    ("compression_change", sample(compression="zstd", physical=b"synthetic-zstd-frame"), None),
    ("migration", sample(b"changed\n", schema=Schema("v2", ("kind", "payload"), "synthetic.ordinal")), "rewrite-flag"),
    ("semantic_rewrite", sample(b"changed\n"), "rewrite-flag"),
    ("truncation", sample(b"one\n"), None),
    ("unstable_read", sample(b"changed\n", complete=False), "rewrite-flag"),
    ("unresolved", sample(b"changed\n"), None),
])
def test_classifications_and_actual_side_effects(kind, new, marker):
    old, result = run(sample(), new, marker=marker)
    assert result.observations[-1].decision.kind == kind
    assert old.checkpoints[0].position == 8
    if kind in ("append", "compression_change"):
        assert result.members == old.members
        assert result.generations == old.generations
        assert len(result.snapshots) == 2
        assert result.snapshots[0] == old.snapshots[0]
        assert result.latest_snapshot_id != old.latest_snapshot_id
        assert result.checkpoints == old.checkpoints
    elif kind in ("migration", "semantic_rewrite"):
        assert len(result.members) == (2 if kind == "migration" else 1)
        assert len(result.generations) == 2
        assert result.checkpoints[0].status == "closed"
        assert result.checkpoints[0].position == 8
        assert result.checkpoints[-1].position == 0
        assert result.checkpoints[-1].status == "active"
        assert result.checkpoints[-1].id != old.checkpoints[0].id
    else:
        assert result.members == old.members
        assert result.generations == old.generations
        assert result.snapshots == old.snapshots
        assert result.checkpoints == old.checkpoints
        assert result.blocked_reason
        assert len(result.observations) == 1


def test_two_different_reads_are_unstable_before_migration():
    old = initial_state(sample())
    a = sample(b"new\n", schema=Schema("v2", ("x",), "new.seq"))
    b = replace(a, physical=b"newer\n", logical=b"newer\n")
    result = apply_change(old, a, b, observation_id="unstable", expected_snapshot_id=old.latest_snapshot_id, rewrite_marker="explicit")
    assert result.observations[-1].decision.kind == "unstable_read"
    assert result.members == old.members and result.generations == old.generations
    assert result.checkpoints == old.checkpoints


def test_truncation_precedes_schema_migration():
    old, result = run(sample(), sample(b"one\n", schema=Schema("v2", ("other",), "new.seq")), marker="explicit")
    assert result.observations[-1].decision.kind == "truncation"
    assert result.members == old.members
    assert result.checkpoints == old.checkpoints


def test_reorder_is_explicit_computable_rewrite_evidence():
    old, result = run(sample(), sample(b"two\none\n"))
    assert result.observations[-1].decision.kind == "semantic_rewrite"
    assert result.generations[-1].derived_from == old.generations[0].id


def test_encoding_round_trip_reuses_identity_but_updates_latest():
    raw = sample()
    old, compressed = run(raw, sample(compression="zstd", physical=b"synthetic-zstd"))
    restored = apply_change(compressed, raw, raw, observation_id="restore", expected_snapshot_id=compressed.latest_snapshot_id)
    assert restored.latest_snapshot_id == old.latest_snapshot_id
    assert len(restored.snapshots) == 2
    assert restored.checkpoints == old.checkpoints


@pytest.mark.parametrize("schema", [Schema("v2", ("kind", "payload"), "synthetic.ordinal"),
    Schema("v1", ("kind", "data"), "synthetic.ordinal"),
    Schema("v1", ("kind", "payload"), "synthetic.seq0")])
@pytest.mark.parametrize("content", [b"one\ntwo\n", b"one\ntwo\nthree\n", b"two\none\n"])
def test_each_schema_change_wins_over_encoding_append_or_rewrite(schema, content):
    old, result = run(sample(), sample(content, schema=schema, compression="zstd", physical=b"encoded"), marker="explicit-rewrite")
    assert result.observations[-1].decision.kind == "migration"
    assert result.members[-1].id != old.members[0].id
    assert result.members[-1].derived_from == old.members[0].id
    assert result.members[0] == old.members[0]
    assert result.checkpoints[-1].source_member_id == result.members[-1].id
    assert result.checkpoints[-1].position == 0


@pytest.mark.parametrize("change", [dict(complete=False), dict(writing=True), dict(logical=None)])
def test_unstable_precedes_truncation_and_has_no_identity_effects(change):
    new = replace(sample(b"one\n", schema=Schema("v2", ("new",), "new.seq")), **change)
    old, result = run(sample(), new, marker="explicit")
    assert result.observations[-1].decision.kind == "unstable_read"
    assert result.snapshots == old.snapshots
    assert result.generations == old.generations and result.members == old.members
    assert result.observations[-1].second_read == new
    assert result.checkpoints == old.checkpoints


@pytest.mark.parametrize("new", [sample(b"one\n"), sample(complete=False), sample(b"changed\n")])
def test_frozen_state_cannot_auto_resume_into_new_identity(new):
    old, blocked = run(sample(), new)
    next_read = sample(b"new-schema\n", schema=Schema("v2", ("new",), "new.seq"))
    result = apply_change(blocked, next_read, next_read, observation_id="after-freeze", expected_snapshot_id=blocked.latest_snapshot_id)
    assert result.observations[-1].decision.kind == "migration"
    assert not result.observations[-1].applied
    assert result.members == old.members and result.generations == old.generations
    assert result.checkpoints == old.checkpoints


@pytest.mark.parametrize("new,marker", [(sample(b"changed\n"), "rewrite"),
    (sample(b"new\n", schema=Schema("v2", ("x",), "new.seq")), None)])
def test_undrained_generation_is_not_replaced(new, marker):
    old, result = run(sample(), new, checkpoint=4, marker=marker)
    assert result.observations[-1].decision.identity_action in ("new_member", "new_generation")
    assert result.blocked_reason == "old_generation_not_drained"
    assert result.members == old.members and result.generations == old.generations
    assert result.checkpoints == old.checkpoints


def test_missing_schema_cannot_be_assumed_unchanged():
    old, result = run(sample(), sample(b"one\ntwo\nthree\n", schema=None), marker="rewrite")
    assert result.observations[-1].decision.kind == "unresolved"
    assert result.checkpoints == old.checkpoints and result.snapshots == old.snapshots


def test_schema_key_order_is_not_migration():
    reordered = Schema("v1", ("payload", "kind", "kind"), "synthetic.ordinal")
    old, result = run(sample(), sample(b"one\ntwo\nthree\n", schema=reordered))
    assert result.observations[-1].decision.kind == "append"
    assert result.members == old.members


def test_no_change_does_not_create_snapshot_or_freeze():
    old, result = run(sample(), sample())
    assert result.observations[-1].decision.reason == "unchanged"
    assert not result.blocked_reason
    assert result.snapshots == old.snapshots and result.checkpoints == old.checkpoints


def test_physical_length_shrink_can_be_logical_append(tmp_path):
    # 原始文件路径仅为测试夹具；分类器接收已解压逻辑字节，不读取私人目录。
    raw_path, encoded_path = tmp_path / "raw.jsonl", tmp_path / "encoded.zst"
    old_content = b"one\ntwo\n" * 20
    new_content = old_content + b"three\n"
    raw_path.write_bytes(old_content)
    encoded_path.write_bytes(b"synthetic-compressed-frame")
    before = sample(old_content, physical=raw_path.read_bytes())
    after = sample(new_content, compression="zstd", physical=encoded_path.read_bytes())
    old, result = run(before, after)
    assert after.physical_byte_length < before.physical_byte_length
    assert after.decompressed_content_length > before.decompressed_content_length
    assert result.observations[-1].decision.kind == "append"
    latest = result.snapshots[-1]
    assert latest.physical_byte_length == len(encoded_path.read_bytes())
    assert latest.decompressed_content_length == len(new_content)
    assert result.checkpoints == old.checkpoints
    assert raw_path.read_bytes() == old_content


def test_logical_truncation_despite_larger_physical_representation():
    old, result = run(sample(), sample(b"one\n", compression="zstd", physical=b"large-encoding" * 50))
    assert result.observations[-1].decision.kind == "truncation"
    assert result.snapshots == old.snapshots and result.checkpoints == old.checkpoints


def test_uncompressed_snapshot_ids_match_m1():
    state = initial_state(sample())
    expected = m1_snapshot(state.generations[0].id, state.accepted.logical)
    assert state.snapshots[0].id == expected["id"]


def test_idempotent_observation_and_stale_evidence_do_not_change_state():
    old, result = run(sample(), sample(b"one\ntwo\nthree\n"))
    new = result.accepted
    assert apply_change(result, new, new, observation_id="change-1", expected_snapshot_id=old.latest_snapshot_id) is result
    with pytest.raises(StoreConflict, match="observation_id_conflict"):
        apply_change(result, sample(), sample(), observation_id="change-1", expected_snapshot_id=old.latest_snapshot_id)
    with pytest.raises(StoreConflict, match="stale_snapshot"):
        apply_change(result, new, new, observation_id="stale", expected_snapshot_id=old.latest_snapshot_id)
    assert len(result.observations) == 1
    assert len(old.snapshots) == 1


def test_reorder_requires_record_multiplicities_and_complete_records():
    for old_bytes, new_bytes in [(b"a\na\nb\n", b"a\nb\nb\n"), (b"a\nb", b"b\na")]:
        old, result = run(sample(old_bytes), sample(new_bytes))
        assert result.observations[-1].decision.kind == "unresolved"
        assert result.generations == old.generations and result.checkpoints == old.checkpoints


def test_codec_label_alone_without_changed_bytes_is_unresolved():
    old, result = run(sample(), sample(compression="zstd"))
    assert result.observations[-1].decision.kind == "unresolved"
    assert result.snapshots == old.snapshots and result.checkpoints == old.checkpoints


def test_initial_state_requires_complete_and_known_schema():
    for read in (sample(complete=False), sample(schema=None), replace(sample(), logical=None)):
        with pytest.raises(ValueError):
            initial_state(read)
    with pytest.raises(ValueError):
        initial_state(sample(), checkpoint_position=True)


def test_two_reads_physical_change_even_with_same_logical_is_unstable():
    old = initial_state(sample())
    a, b = sample(compression="zstd", physical=b"encoding-a"), sample(compression="zstd", physical=b"encoding-b")
    result = apply_change(old, a, b, observation_id="physical-race", expected_snapshot_id=old.latest_snapshot_id)
    assert result.observations[-1].decision.kind == "unstable_read"
    assert result.snapshots == old.snapshots and result.checkpoints == old.checkpoints


def test_fixed_zstd_raw_block_and_plain_logical_bytes_are_same_identity():
    # 固定合成单段 raw block：magic、frame header、last block header、8 字节内容。
    # 分类器不负责解压，调用方提供已解压字节和流完整性证据。
    frame = bytes.fromhex("28b52ffd20084100006f6e650a74776f0a")
    logical = b"one\ntwo\n"
    assert frame[9:] == logical
    old, result = run(sample(logical), sample(logical, compression="zstd", physical=frame))
    assert result.observations[-1].decision.kind == "compression_change"
    assert result.members == old.members and result.generations == old.generations
    assert result.snapshots[-1].content_sha256 == old.snapshots[0].content_sha256
    assert result.snapshots[-1].file_sha256 != old.snapshots[0].file_sha256
    assert result.snapshots[-1].physical_byte_length == 17
    assert result.snapshots[-1].decompressed_content_length == 8
    assert result.checkpoints == old.checkpoints


def test_unfinished_zstd_stream_cannot_create_snapshot():
    frame_prefix = bytes.fromhex("28b52ffd20084100006f6e65")
    old, result = run(sample(), sample(b"one", compression="zstd", physical=frame_prefix, complete=False))
    assert result.observations[-1].decision.kind == "unstable_read"
    assert result.snapshots == old.snapshots and result.checkpoints == old.checkpoints
