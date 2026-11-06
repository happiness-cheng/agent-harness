"""M4：冻结顺序分类器和纯内存合成执行器；不是生产文件读取器或 Store。"""
from collections import Counter
from dataclasses import dataclass, replace
from hashlib import sha256

from min_agent.activity_identity import derive, digest, object_identity
from min_agent.activity_store import StoreConflict


@dataclass(frozen=True)
class Schema:
    format_version: str
    structure_keys: tuple[str, ...]
    seq_namespace: str

    def __post_init__(self):
        if (not all(type(s) is str and s for s in (self.format_version, self.seq_namespace))
                or type(self.structure_keys) is not tuple or not self.structure_keys
                or not all(type(k) is str and k for k in self.structure_keys)):
            raise ValueError("invalid_schema_evidence")
        object.__setattr__(self, "structure_keys", tuple(sorted(set(self.structure_keys))))


@dataclass(frozen=True)
class ReadSample:
    physical: bytes
    logical: bytes | None
    compression: str
    schema: Schema | None
    complete: bool = True
    writing: bool = False

    def __post_init__(self):
        if (type(self.physical) is not bytes or self.logical is not None and type(self.logical) is not bytes
                or self.compression not in ("none", "zstd")
                or self.schema is not None and type(self.schema) is not Schema
                or type(self.complete) is not bool or type(self.writing) is not bool):
            raise ValueError("invalid_read_evidence")

    @property
    def physical_byte_length(self):
        return len(self.physical)

    @property
    def decompressed_content_length(self):
        return None if self.logical is None else len(self.logical)


@dataclass(frozen=True)
class Decision:
    kind: str
    identity_action: str
    checkpoint_action: str
    reason: str


def _stable(sample):
    return (sample.complete and not sample.writing and sample.logical is not None
            and (sample.compression != "none" or sample.physical == sample.logical))


def classify(old: ReadSample, first: ReadSample, second: ReadSample, *, rewrite_marker=None):
    """先排除高优先级；未知 Schema 不得降级猜成 append/rewrite。"""
    if rewrite_marker is not None and (type(rewrite_marker) is not str or not rewrite_marker.strip()):
        raise ValueError("invalid_rewrite_marker")
    if not _stable(old) or not _stable(first) or not _stable(second) or first != second:
        return Decision("unstable_read", "none", "freeze", "incomplete_or_inconsistent_reads")
    new = second
    if len(new.logical) < len(old.logical) and old.logical.startswith(new.logical):
        return Decision("truncation", "none", "freeze", "shorter_logical_prefix")
    if old.schema is None or new.schema is None:
        return Decision("unresolved", "none", "freeze", "missing_schema_evidence")
    if old.schema != new.schema:
        return Decision("migration", "new_member", "new_checkpoint_from_zero", "schema_or_sequence_namespace_changed")
    if old.logical == new.logical:
        if old.physical != new.physical:
            return Decision("compression_change", "new_snapshot", "preserve", "same_logical_bytes_different_encoding")
        if old.compression == new.compression:
            return Decision("unresolved", "none", "preserve", "unchanged")
        return Decision("unresolved", "none", "freeze", "encoding_change_without_physical_evidence")
    if len(new.logical) > len(old.logical) and new.logical.startswith(old.logical):
        return Decision("append", "new_snapshot", "preserve", "longer_logical_prefix_same_schema")
    # 合成换行记录的重排证据保留重复次数；不是按集合去重后猜测。
    reordered = (old.logical.endswith(b"\n") and new.logical.endswith(b"\n")
                 and Counter(old.logical.split(b"\n")) == Counter(new.logical.split(b"\n")))
    if rewrite_marker is not None or reordered:
        return Decision("semantic_rewrite", "new_generation", "close_old_and_start_zero",
                        "platform_rewrite_marker" if rewrite_marker is not None else "complete_record_reordering")
    return Decision("unresolved", "none", "freeze", "missing_explicit_rewrite_evidence")


@dataclass(frozen=True)
class Member:
    id: str
    source_object_id: str
    native_branch_key: str
    branch_epoch: str
    schema: Schema
    derived_from: str | None = None


@dataclass(frozen=True)
class Generation:
    id: str
    source_member_id: str
    generation_epoch: str
    root_snapshot_id: str
    derived_from: str | None = None


@dataclass(frozen=True)
class Snapshot:
    id: str
    source_generation_id: str
    file_sha256: str
    content_sha256: str
    compression: str
    physical_byte_length: int
    decompressed_content_length: int


def _snapshot(generation_id, sample):
    fields = dict(source_generation_id=generation_id, file_sha256=sha256(sample.physical).hexdigest(),
                  content_sha256=sha256(sample.logical).hexdigest(), compression=sample.compression,
                  physical_byte_length=sample.physical_byte_length,
                  decompressed_content_length=sample.decompressed_content_length)
    return Snapshot(derive("snapshot", fields), **fields)


@dataclass(frozen=True)
class Checkpoint:
    id: str
    source_member_id: str
    source_stream_id: str
    source_generation_id: str
    position: int
    status: str = "active"


def _checkpoint(member, generation_id, position=0):
    stream = derive("stream", dict(source_member_id=member.id, seq_namespace=member.schema.seq_namespace))
    fields = dict(source_member_id=member.id, source_stream_id=stream, source_generation_id=generation_id)
    return Checkpoint(derive("checkpoint", fields), **fields, position=position)


@dataclass(frozen=True)
class Observation:
    observation_id: str
    evidence_hash: str
    decision: Decision
    applied: bool
    blocked_reason: str | None
    first_read: ReadSample
    second_read: ReadSample


@dataclass(frozen=True)
class SyntheticState:
    """不可变合成执行结果；不持久化、不代表 M2/M3 写路径已接入。"""
    members: tuple[Member, ...]
    generations: tuple[Generation, ...]
    snapshots: tuple[Snapshot, ...]
    checkpoints: tuple[Checkpoint, ...]
    accepted: ReadSample
    latest_snapshot_id: str
    observations: tuple[Observation, ...] = ()
    blocked_reason: str | None = None

def initial_state(sample, *, checkpoint_position=0, logical_id="M4-Synthetic"):
    """位点是已消费合成夹具，不提供无事件证据的推进 API。"""
    if not _stable(sample) or sample.schema is None:
        raise ValueError("initial_read_not_complete_or_schema_unknown")
    if type(checkpoint_position) is not int or not 0 <= checkpoint_position <= len(sample.logical):
        raise ValueError("invalid_initial_checkpoint")
    obj = object_identity("synthetic", "m4-fixture", logical_id, "history")
    fields = dict(source_object_id=obj["id"], native_branch_key="root", branch_epoch="ROOT_EPOCH")
    member = Member(derive("member", fields), **fields, schema=sample.schema)
    fields = dict(source_member_id=member.id, generation_epoch="INITIAL")
    gid = derive("generation", fields)
    snapshot = _snapshot(gid, sample)
    generation = Generation(gid, **fields, root_snapshot_id=snapshot.id)
    return SyntheticState((member,), (generation,), (snapshot,), (_checkpoint(member, gid, checkpoint_position),), sample, snapshot.id)


def _evidence(sample):
    schema = sample.schema
    return dict(physical_hex=sample.physical.hex(), logical_hex=None if sample.logical is None else sample.logical.hex(),
                compression=sample.compression, complete=sample.complete, writing=sample.writing,
                schema=None if schema is None else dict(format_version=schema.format_version,
                    structure_keys=list(schema.structure_keys), seq_namespace=schema.seq_namespace))


def apply_change(state, first, second, *, observation_id, expected_snapshot_id, rewrite_marker=None):
    """重新分类后一次返回新状态；调用者不能注入分类结果跳过优先级。"""
    if type(observation_id) is not str or not observation_id:
        raise ValueError("missing_observation_id")
    evidence_hash = digest(dict(first=_evidence(first), second=_evidence(second),
                                expected_snapshot_id=expected_snapshot_id, rewrite_marker=rewrite_marker))
    previous = next((o for o in state.observations if o.observation_id == observation_id), None)
    if previous is not None:
        if previous.evidence_hash != evidence_hash:
            raise StoreConflict("observation_id_conflict")
        return state
    if expected_snapshot_id != state.latest_snapshot_id:
        raise StoreConflict("stale_snapshot_evidence")
    decision = classify(state.accepted, first, second, rewrite_marker=rewrite_marker)
    blocked = state.blocked_reason
    if not blocked and decision.checkpoint_action == "freeze":
        blocked = decision.kind + ":" + decision.reason
    cp = state.checkpoints[-1]
    creates_identity = decision.identity_action in ("new_member", "new_generation")
    if not blocked and creates_identity and cp.position != len(state.accepted.logical):
        blocked = "old_generation_not_drained"
    applied = not blocked and decision.identity_action != "none"
    observation = Observation(observation_id, evidence_hash, decision, applied, blocked, first, second)
    result = replace(state, observations=state.observations + (observation,), blocked_reason=blocked)
    if not applied:
        return result
    member, generation = state.members[-1], state.generations[-1]
    if decision.identity_action == "new_member":
        fields = dict(source_object_id=member.source_object_id, native_branch_key=member.native_branch_key,
                      branch_epoch=derive("migration_epoch", {"from_member_id": member.id, "observation_id": observation_id}))
        member = Member(derive("member", fields), **fields, schema=second.schema, derived_from=member.id)
        result = replace(result, members=result.members + (member,))
    if creates_identity:
        fields = dict(source_member_id=member.id, generation_epoch=observation_id, derived_from=generation.id)
        gid = derive("generation", fields)
        snapshot = _snapshot(gid, second)
        generation = Generation(gid, **fields, root_snapshot_id=snapshot.id)
        result = replace(result, generations=result.generations + (generation,),
                         checkpoints=result.checkpoints[:-1] + (replace(cp, status="closed"), _checkpoint(member, gid)))
    else:
        snapshot = _snapshot(generation.id, second)
    # 编码恢复可能回到已有 snapshot 身份；记录观察，注册项不重复。
    snapshots = result.snapshots if snapshot.id in {s.id for s in result.snapshots} else result.snapshots + (snapshot,)
    return replace(result, snapshots=snapshots, accepted=second, latest_snapshot_id=snapshot.id)
