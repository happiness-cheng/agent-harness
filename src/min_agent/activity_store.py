"""Phase 0B M1：独立实验库，三层逻辑存储；不接入生产 Runtime。

只支持一个 source object/member/初代 generation 和未压缩 append。
所有可变状态在 Observation；Manifest 只有不可变注册项及链根。
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from min_agent.activity_identity import canonical, derive, digest, object_identity


class StoreConflict(RuntimeError):
    """调用冲突，操作不提交。"""


class IntegrityError(StoreConflict):
    """持久内容或链验证失败。"""


class StateConflict(StoreConflict):
    """状态终态冲突，当前写入者冻结。"""


APP_ID = 0x414D3101
STATE_VALUES = {
    "identity_status": ("provisional", "definitive"),
    "checkpoint_status": ("active", "closed"),
    "root_status": ("root_unresolved", "root_resolved"),
}
SCHEMA = """
CREATE TABLE manifest_revisions(seq INTEGER PRIMARY KEY, body TEXT NOT NULL, hash TEXT NOT NULL);
CREATE TABLE manifest_head(singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    manifest_id TEXT NOT NULL, seq INTEGER NOT NULL, hash TEXT NOT NULL);
CREATE TABLE observations(seq INTEGER PRIMARY KEY, body TEXT NOT NULL, hash TEXT NOT NULL);
CREATE TABLE checkpoints(id TEXT PRIMARY KEY, body TEXT NOT NULL, hash TEXT NOT NULL);
"""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _parse_json(raw):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise IntegrityError("duplicate_json_key")
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=unique_pairs)


def _entry(kind, fields, **extra):
    return {"kind": kind, "id": derive(kind, fields), "fields": fields, **extra}


def _row(seq, body):
    return {"seq": seq, "body": body, "hash": digest(body)}


def _snapshot(generation, content):
    if type(content) is not bytes:
        raise ValueError("content_must_be_bytes")
    sha = hashlib.sha256(content).hexdigest()
    fields = dict(source_generation_id=generation, file_sha256=sha, content_sha256=sha,
                  compression="none", physical_byte_length=len(content), decompressed_content_length=len(content))
    return {"id": derive("snapshot", fields), "fields": fields, "content_hex": content.hex()}


def _observe(bundle, kind, payload):
    observations = bundle["observations"]
    body = dict(manifest_id=bundle["head"]["manifest_id"], observation_revision=len(observations) + 1,
                parent_observation_hash=observations[-1]["hash"] if observations else None,
                kind=kind, payload=payload)
    observations.append(_row(len(observations) + 1, body))


def _state_event(bundle, target, dimension, value, evidence):
    events = [r["body"]["payload"] for r in bundle["observations"] if r["body"]["kind"] == "state"]
    root = bundle["manifest"][0]["body"]["state_event_log_root_id"]
    body = dict(state_event_log_root_id=root, state_event_seq=len(events) + 1,
                target_id=target, state_dimension=dimension, state_value=value,
                parent_event_hash=events[-1]["event_hash"] if events else None,
                evidence_ref=evidence, observed_at=_now())
    _observe(bundle, "state", {**body, "event_hash": digest(body)})


def _fold(state, payload):
    key = (payload["target_id"], payload["state_dimension"])
    values = STATE_VALUES.get(key[1])
    if values is None or payload["state_value"] not in values:
        raise StateConflict("state_fold_conflict: unsupported state")
    if state.get(key) == values[1] and payload["state_value"] != values[1]:
        raise StateConflict("state_fold_conflict: terminal reversal")
    state[key] = payload["state_value"]


def _require(condition, reason):
    if not condition:
        raise IntegrityError(reason)


def _validate(bundle):
    """校验恢复包与数据库共用同一路径，不能绕过哈希或语义约束。"""
    try:
        return _validate_inner(bundle)
    except (IntegrityError, StateConflict):
        raise
    except (KeyError, TypeError, ValueError, IndexError, UnicodeError) as exc:
        raise IntegrityError("invalid_store_structure") from exc


def _validate_inner(bundle):
    _require(set(bundle) == {"format", "head", "manifest", "observations", "checkpoints"}, "bundle_fields")
    _require(bundle["format"] == "adrian-m1/1", "bundle_format")
    canonical(bundle)
    revisions = bundle["manifest"]
    _require(bool(revisions), "missing_manifest")
    first = revisions[0]["body"]
    binding = {k: first[k] for k in ("collector_namespace", "source_object_id", "manifest_epoch")}
    manifest_id = derive("manifest", binding)
    root = derive("state_event_log", {"manifest_id": manifest_id})
    registry = {}
    previous_hash = None
    body_keys = {"manifest_id", "collector_namespace", "source_object_id", "manifest_epoch",
                 "state_event_log_root_id", "revision_no", "parent_revision_no", "parent_manifest_hash",
                 "registrations", "change_set_ref", "committed_at", "writer_id"}
    for seq, row in enumerate(revisions, 1):
        body = row["body"]
        _require(set(row) == {"seq", "body", "hash"} and set(body) == body_keys, "manifest_fields")
        _require(type(row["seq"]) is int and type(body["revision_no"]) is int
                 and row["seq"] == body["revision_no"] == seq, "manifest_revision")
        _require(body["manifest_id"] == manifest_id == bundle["head"]["manifest_id"], "manifest_binding")
        _require(all(body[k] == v for k, v in binding.items()), "manifest_binding")
        _require(body["state_event_log_root_id"] == root, "state_root_binding")
        _require(body["parent_revision_no"] == (seq - 1 if seq > 1 else None), "parent_revision")
        _require(seq == 1 or type(body["parent_revision_no"]) is int, "parent_revision_type")
        _require(body["parent_manifest_hash"] == previous_hash, "parent_manifest_hash")
        _require(row["hash"] == digest(body), "manifest_hash")
        _require(bool(body["registrations"]), "empty_registration_revision")
        for entry in body["registrations"]:
            kind, fields, identity = entry["kind"], entry["fields"], entry["id"]
            expected_keys = {"kind", "id", "fields"}
            if kind == "generation":
                expected_keys |= {"snapshot_chain_root_id", "first_snapshot_content_sha256"}
            _require(set(entry) == expected_keys, "registration_fields")
            _require(identity == derive(kind, fields) and identity not in registry, "registration_identity")
            registry[identity] = entry
        previous_hash = row["hash"]
    _require(type(bundle["head"]["seq"]) is int and bundle["head"] == {
        "manifest_id": manifest_id, "seq": len(revisions), "hash": previous_hash}, "manifest_head")

    by_kind = {kind: [e for e in registry.values() if e["kind"] == kind]
               for kind in ("object", "member", "stream", "generation")}
    _require(sum(map(len, by_kind.values())) == len(registry), "unknown_registration")
    _require(all(len(by_kind[k]) == 1 for k in ("object", "member", "generation")) and bool(by_kind["stream"]), "m1_profile")
    obj, member, generation = (by_kind[k][0] for k in ("object", "member", "generation"))
    _require(obj == {"kind": "object", **object_identity(**obj["fields"])}, "object_fields")
    _require(obj["id"] == binding["source_object_id"], "object_binding")
    _require(member["fields"] == dict(source_object_id=obj["id"], native_branch_key=member["fields"]["native_branch_key"], branch_epoch="ROOT_EPOCH"), "member_fields")
    _require(type(member["fields"]["native_branch_key"]) is str and bool(member["fields"]["native_branch_key"]), "missing_branch")
    _require(generation["fields"] == dict(source_member_id=member["id"], generation_epoch="INITIAL"), "generation_fields")
    for stream in by_kind["stream"]:
        _require(stream["fields"] == dict(source_member_id=member["id"], seq_namespace=stream["fields"]["seq_namespace"]), "stream_fields")
        _require(type(stream["fields"]["seq_namespace"]) is str and bool(stream["fields"]["seq_namespace"]), "stream_namespace")
    stream = by_kind["stream"][0]
    checkpoint_fields = dict(source_member_id=member["id"], source_stream_id=stream["id"], source_generation_id=generation["id"])
    checkpoint_id = derive("checkpoint", checkpoint_fields)

    snapshots, state, events = [], {}, []
    previous_hash = None
    for seq, row in enumerate(bundle["observations"], 1):
        body = row["body"]
        _require(set(row) == {"seq", "body", "hash"} and set(body) == {"manifest_id", "observation_revision", "parent_observation_hash", "kind", "payload"}, "observation_fields")
        _require(type(row["seq"]) is int and type(body["observation_revision"]) is int
                 and row["seq"] == body["observation_revision"] == seq, "observation_revision")
        _require(body["manifest_id"] == manifest_id and body["parent_observation_hash"] == previous_hash, "observation_chain")
        _require(row["hash"] == digest(body), "observation_hash")
        payload = body["payload"]
        if body["kind"] == "snapshot":
            content = bytes.fromhex(payload["content_hex"])
            _require(payload == _snapshot(generation["id"], content), "snapshot_identity")
            if snapshots:
                prior = bytes.fromhex(snapshots[-1]["content_hex"])
                _require(len(content) > len(prior) and content.startswith(prior), "snapshot_append")
            snapshots.append(payload)
        elif body["kind"] == "state":
            _require(set(payload) == {"state_event_log_root_id", "state_event_seq", "target_id", "state_dimension", "state_value", "parent_event_hash", "evidence_ref", "observed_at", "event_hash"}, "state_fields")
            _require(type(payload["state_event_seq"]) is int and payload["state_event_log_root_id"] == root
                     and payload["state_event_seq"] == len(events) + 1, "state_sequence")
            _require(payload["parent_event_hash"] == (events[-1]["event_hash"] if events else None), "state_parent")
            _require(payload["event_hash"] == digest({k: v for k, v in payload.items() if k != "event_hash"}), "state_hash")
            target = checkpoint_id if payload["state_dimension"] == "checkpoint_status" else member["id"]
            _require(payload["target_id"] == target and type(payload["evidence_ref"]) is str and bool(payload["evidence_ref"]), "state_target_or_evidence")
            _fold(state, payload)
            events.append(payload)
        else:
            raise IntegrityError("unknown_observation_kind")
        previous_hash = row["hash"]
    _require(bool(snapshots), "missing_snapshot_root")
    _require(generation["snapshot_chain_root_id"] == snapshots[0]["id"], "snapshot_root")
    _require(generation["first_snapshot_content_sha256"] == snapshots[0]["fields"]["content_sha256"], "snapshot_root_hash")
    _require(len(bundle["checkpoints"]) == 1, "checkpoint_count")
    cp = bundle["checkpoints"][0]
    _require(set(cp) == {"id", "body", "hash"}, "checkpoint_fields")
    cp_body = cp["body"]
    _require(set(cp_body) == {"manifest_id", "key", "revision", "position", "snapshot_id"}, "checkpoint_body_fields")
    _require(cp["id"] == checkpoint_id and cp_body["key"] == checkpoint_fields and cp_body["manifest_id"] == manifest_id, "checkpoint_identity")
    _require(cp["hash"] == digest(cp_body), "checkpoint_hash")
    cp_snapshot = next((s for s in snapshots if s["id"] == cp_body["snapshot_id"]), None)
    _require(cp_snapshot is not None, "checkpoint_snapshot")
    _require(type(cp_body["revision"]) is int and cp_body["revision"] >= 1, "checkpoint_revision")
    _require(type(cp_body["position"]) is int and 0 <= cp_body["position"] <= cp_snapshot["fields"]["decompressed_content_length"], "checkpoint_position")
    _require(state.get((checkpoint_id, "checkpoint_status")) in ("active", "closed"), "checkpoint_uninitialized")
    return dict(registry=registry, object=obj, member=member, generation=generation, stream=stream,
                snapshots=snapshots, state=state, checkpoint=cp)


class M1Store:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self._frozen = False

    @classmethod
    def open(cls, path):
        store = cls(path)
        store.summary()
        return store

    @classmethod
    def create(cls, path, *, collector_namespace, manifest_epoch, platform, object_namespace,
               logical_id, object_role, native_branch_key, root_evidence, first_content):
        if root_evidence not in {"native_branch_key", "platform_explicit"}:
            raise ValueError("root_unresolved: definitive initialization refused")
        if not all(type(s) is str and s for s in (collector_namespace, manifest_epoch, native_branch_key)):
            raise ValueError("missing_identity_input")
        obj = {"kind": "object", **object_identity(platform, object_namespace, logical_id, object_role)}
        member = _entry("member", dict(source_object_id=obj["id"], native_branch_key=native_branch_key, branch_epoch="ROOT_EPOCH"))
        stream = _entry("stream", dict(source_member_id=member["id"], seq_namespace="synthetic.ordinal"))
        gen = _entry("generation", dict(source_member_id=member["id"], generation_epoch="INITIAL"))
        snapshot = _snapshot(gen["id"], first_content)
        gen.update(snapshot_chain_root_id=snapshot["id"], first_snapshot_content_sha256=snapshot["fields"]["content_sha256"])
        binding = dict(collector_namespace=collector_namespace, source_object_id=obj["id"], manifest_epoch=manifest_epoch)
        manifest_id = derive("manifest", binding)
        body = dict(manifest_id=manifest_id, **binding, state_event_log_root_id=derive("state_event_log", {"manifest_id": manifest_id}),
                    revision_no=1, parent_revision_no=None, parent_manifest_hash=None,
                    registrations=[obj, member, stream, gen], change_set_ref="synthetic-initial-registration",
                    committed_at=_now(), writer_id="m1-local")
        bundle = dict(format="adrian-m1/1", head=dict(manifest_id=manifest_id, seq=1, hash=digest(body)),
                      manifest=[_row(1, body)], observations=[], checkpoints=[])
        _observe(bundle, "snapshot", snapshot)
        key = dict(source_member_id=member["id"], source_stream_id=stream["id"], source_generation_id=gen["id"])
        cp_body = dict(manifest_id=manifest_id, key=key, revision=1, position=0, snapshot_id=snapshot["id"])
        cp_id = derive("checkpoint", key)
        bundle["checkpoints"].append(dict(id=cp_id, body=cp_body, hash=digest(cp_body)))
        _state_event(bundle, cp_id, "checkpoint_status", "active", "synthetic-initial-registration")
        _state_event(bundle, member["id"], "root_status", "root_resolved", root_evidence)
        return cls.rebuild(path, bundle)

    @classmethod
    def rebuild(cls, path, bundle):
        # 先验证完整恢复输入，再独占创建新文件；不覆盖已有数据库。
        bundle = json.loads(canonical(bundle))
        _validate(bundle)
        store = cls(path)
        with store.path.open("xb"):
            pass
        conn = sqlite3.connect(store.path)
        try:
            conn.executescript(SCHEMA)
            conn.execute(f"PRAGMA application_id={APP_ID}")
            conn.execute("PRAGMA user_version=1")
            with conn:
                store._persist(conn, bundle)
        finally:
            conn.close()
        return cls.open(path)

    @staticmethod
    def _load(conn):
        head = conn.execute("SELECT manifest_id,seq,hash FROM manifest_head WHERE singleton=1").fetchone()
        if head is None:
            raise IntegrityError("missing_manifest_head")
        def rows(table):
            return [dict(seq=s, body=_parse_json(b), hash=h) for s, b, h in conn.execute(f"SELECT seq,body,hash FROM {table} ORDER BY seq")]
        return dict(format="adrian-m1/1", head=dict(zip(("manifest_id", "seq", "hash"), head)),
                    manifest=rows("manifest_revisions"), observations=rows("observations"),
                    checkpoints=[dict(id=i, body=_parse_json(b), hash=h) for i, b, h in conn.execute("SELECT id,body,hash FROM checkpoints ORDER BY id")])

    @staticmethod
    def _persist(conn, bundle, previous=None):
        for table, key in (("manifest_revisions", "manifest"), ("observations", "observations")):
            start = len(previous[key]) if previous else 0
            for row in bundle[key][start:]:
                conn.execute(f"INSERT INTO {table} VALUES(?,?,?)", (row["seq"], canonical(row["body"]).decode(), row["hash"]))
        head = bundle["head"]
        conn.execute("INSERT INTO manifest_head VALUES(1,?,?,?) ON CONFLICT(singleton) DO UPDATE SET manifest_id=excluded.manifest_id,seq=excluded.seq,hash=excluded.hash",
                     (head["manifest_id"], head["seq"], head["hash"]))
        for cp in bundle["checkpoints"]:
            conn.execute("INSERT INTO checkpoints VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body,hash=excluded.hash",
                         (cp["id"], canonical(cp["body"]).decode(), cp["hash"]))

    @contextmanager
    def _transaction(self, write=False):
        if write and self._frozen:
            raise StoreConflict("store_frozen")
        conn = None
        try:
            conn = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=5)
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            _require(conn.execute("PRAGMA application_id").fetchone()[0] == APP_ID, "not_m1_database")
            _require(conn.execute("PRAGMA user_version").fetchone()[0] == 1, "schema_version")
            bundle = self._load(conn)
            model = _validate(bundle)
            before = json.loads(canonical(bundle)) if write else None
            yield bundle, model
            if write:
                _validate(bundle)
                self._persist(conn, bundle, before)
            conn.commit()
        except (IntegrityError, StateConflict):
            self._frozen = True
            raise
        except (sqlite3.DatabaseError, json.JSONDecodeError) as exc:
            self._frozen = True
            raise IntegrityError("store_read_or_commit_failed") from exc
        finally:
            if conn is not None:
                conn.close()

    def export_bundle(self):
        with self._transaction() as (bundle, _):
            return bundle

    def summary(self):
        with self._transaction() as (bundle, model):
            return dict(manifest_id=bundle["head"]["manifest_id"], manifest_revision=bundle["head"]["seq"],
                        manifest_hash=bundle["head"]["hash"], object_id=model["object"]["id"],
                        member_id=model["member"]["id"], stream_id=model["stream"]["id"],
                        generation_id=model["generation"]["id"], checkpoint_id=model["checkpoint"]["id"],
                        state_event_log_root_id=bundle["manifest"][0]["body"]["state_event_log_root_id"],
                        snapshot_chain_root_id=model["generation"]["snapshot_chain_root_id"],
                        latest_snapshot_id=model["snapshots"][-1]["id"])

    def identity_inventory(self):
        with self._transaction() as (bundle, model):
            return sorted([bundle["head"]["manifest_id"], bundle["manifest"][0]["body"]["state_event_log_root_id"],
                           *model["registry"], *[s["id"] for s in model["snapshots"]], model["checkpoint"]["id"]])

    def register_stream(self, seq_namespace):
        if type(seq_namespace) is not str or not seq_namespace:
            raise ValueError("invalid_namespace")
        with self._transaction(write=True) as (bundle, model):
            entry = _entry("stream", dict(source_member_id=model["member"]["id"], seq_namespace=seq_namespace))
            if entry["id"] not in model["registry"]:
                previous = bundle["manifest"][-1]
                body = {**previous["body"], "revision_no": previous["seq"] + 1,
                        "parent_revision_no": previous["seq"], "parent_manifest_hash": previous["hash"],
                        "registrations": [entry], "change_set_ref": "register-stream", "committed_at": _now()}
                row = _row(body["revision_no"], body)
                bundle["manifest"].append(row)
                bundle["head"].update(seq=row["seq"], hash=row["hash"])
            return entry["id"]

    def append_snapshot(self, content):
        with self._transaction(write=True) as (bundle, model):
            snapshot = _snapshot(model["generation"]["id"], content)
            previous = model["snapshots"][-1]
            if snapshot["id"] == previous["id"]:
                return snapshot["id"]
            old = bytes.fromhex(previous["content_hex"])
            if not content.startswith(old) or len(content) <= len(old):
                raise ValueError("m1_append_only")
            _observe(bundle, "snapshot", snapshot)
            return snapshot["id"]

    def append_state(self, target_id, dimension, value, *, evidence_ref):
        with self._transaction(write=True) as (bundle, model):
            expected = model["checkpoint"]["id"] if dimension == "checkpoint_status" else model["member"]["id"]
            if target_id != expected or type(evidence_ref) is not str or not evidence_ref:
                raise ValueError("invalid_state_target_or_evidence")
            _state_event(bundle, target_id, dimension, value, evidence_ref)
            # 在事务提交前 fold；冲突回滚并冻结当前写入者。
            _fold(model["state"], bundle["observations"][-1]["body"]["payload"])

    def fold_state(self):
        with self._transaction() as (_, model):
            return model["state"]

    def checkpoint(self):
        with self._transaction() as (_, model):
            cp = model["checkpoint"]
            return {"id": cp["id"], "hash": cp["hash"], **cp["body"]}

    def advance_checkpoint(self, position, *, expected_revision, expected_hash):
        with self._transaction(write=True) as (_, model):
            cp = model["checkpoint"]
            if cp["body"]["revision"] != expected_revision or cp["hash"] != expected_hash:
                raise StoreConflict("checkpoint_cas")
            if model["state"][(cp["id"], "checkpoint_status")] != "active":
                raise StoreConflict("checkpoint_closed")
            latest = model["snapshots"][-1]
            if type(position) is not int or not cp["body"]["position"] <= position <= latest["fields"]["decompressed_content_length"]:
                raise ValueError("invalid_checkpoint_position")
            cp["body"].update(revision=cp["body"]["revision"] + 1, position=position, snapshot_id=latest["id"])
            cp["hash"] = digest(cp["body"])
            return {"id": cp["id"], "hash": cp["hash"], **cp["body"]}
