"""M2 合成持久化：先事件后位点，单 SQLite 事务转换 generation。

独立实验格式，不自动迁移 M1/生产库；fault 仅用于本地故障注入。
"""

from contextlib import closing, contextmanager
import json
import sqlite3

from min_agent.activity_identity import canonical, derive, digest, object_identity
from min_agent.activity_store import (
    M1Store, IntegrityError, StateConflict, StoreConflict, SCHEMA, _entry, _fold,
    _now, _observe, _parse_json, _require, _row, _snapshot, _state_event, _validate_manifest,
)


M2_APP_ID = 0x414D3201
EVENT_SCHEMA = "CREATE TABLE synthetic_events(id TEXT PRIMARY KEY, body TEXT NOT NULL, hash TEXT NOT NULL)"


def _checkpoint_id(member, stream, generation):
    return derive("checkpoint", dict(source_member_id=member, source_stream_id=stream,
                                     source_generation_id=generation))


def _records(snapshot, stream):
    """仅处理完整换行记录，固定 ordinal 键按 generation 隔离。"""
    offset = 0
    result = []
    for ordinal, line in enumerate(bytes.fromhex(snapshot["content_hex"]).split(b"\n")[:-1], 1):
        content = line + b"\n"
        identity = dict(source_generation_id=snapshot["fields"]["source_generation_id"],
                        source_stream_id=stream, event_key=str(ordinal))
        body = dict(**identity, start=offset, end=offset + len(content), content_hex=content.hex())
        result.append(dict(id=derive("m2_event", identity), body=body, hash=digest(body)))
        offset += len(content)
    return result


def _new_checkpoint(manifest_id, member, stream, generation, snapshot):
    key = dict(source_member_id=member, source_stream_id=stream, source_generation_id=generation)
    body = dict(manifest_id=manifest_id, key=key, revision=1, position=0,
                snapshot_id=snapshot, backfill=True)
    return dict(id=derive("checkpoint", key), body=body, hash=digest(body))


def _register(bundle, entry):
    previous = bundle["manifest"][-1]
    body = {**previous["body"], "revision_no": previous["seq"] + 1,
            "parent_revision_no": previous["seq"], "parent_manifest_hash": previous["hash"],
            "registrations": [entry], "change_set_ref": "generation-transition", "committed_at": _now()}
    row = _row(body["revision_no"], body)
    bundle["manifest"].append(row)
    bundle["head"].update(seq=row["seq"], hash=row["hash"])


def _fold_m2(state, payload):
    dimension, value = payload["state_dimension"], payload["state_value"]
    key = (payload["target_id"], dimension)
    old = state.get(key)
    if dimension == "generation_status":
        if value not in ("active", "closed") or old == "closed" and value != old:
            raise StateConflict("state_fold_conflict: generation terminal reversal")
        state[key] = value
    elif dimension == "transition_status":
        if value not in ("pending", "committed", "aborted") or (
            old is None and value != "pending" or old in ("committed", "aborted") and value != old
        ):
            raise StateConflict("state_fold_conflict: transition")
        state[key] = value
    else:
        _fold(state, payload)


def _validate(bundle):
    try:
        return _validate_inner(bundle)
    except (IntegrityError, StateConflict):
        raise
    except (KeyError, TypeError, ValueError, IndexError, UnicodeError) as exc:
        raise IntegrityError("invalid_m2_structure") from exc


def _validate_inner(bundle):
    _require(set(bundle) == {"format", "head", "manifest", "observations", "checkpoints", "events"}, "m2_fields")
    _require(bundle["format"] == "adrian-m2/1", "m2_format")
    canonical(bundle)
    _, manifest_id, state_root, registry = _validate_manifest(bundle)
    kinds = {k: [e for e in registry.values() if e["kind"] == k] for k in ("object", "member", "stream", "generation")}
    _require(sum(map(len, kinds.values())) == len(registry), "unknown_registration")
    _require(all(len(kinds[k]) == 1 for k in ("object", "member", "stream")) and bool(kinds["generation"]), "m2_profile")
    obj, member, stream = (kinds[k][0] for k in ("object", "member", "stream"))
    _require(obj == {"kind": "object", **object_identity(**obj["fields"])}, "object_fields")
    _require(obj["id"] == bundle["manifest"][0]["body"]["source_object_id"], "object_binding")
    _require(member["fields"] == dict(source_object_id=obj["id"], native_branch_key=member["fields"]["native_branch_key"], branch_epoch="ROOT_EPOCH"), "member_fields")
    _require(stream["fields"] == dict(source_member_id=member["id"], seq_namespace="synthetic.ordinal"), "stream_fields")
    generations = {g["id"]: g for g in kinds["generation"]}
    initial = kinds["generation"][0]
    _require(initial["fields"] == dict(source_member_id=member["id"], generation_epoch="INITIAL"), "initial_generation")
    seen = {initial["id"]}
    for generation in kinds["generation"][1:]:
        fields = generation["fields"]
        _require(set(fields) == {"source_member_id", "generation_epoch", "derived_from"}
                 and fields["source_member_id"] == member["id"] and fields["derived_from"] in seen
                 and type(fields["generation_epoch"]) is str and bool(fields["generation_epoch"]), "generation_lineage")
        seen.add(generation["id"])
    cp_ids = {_checkpoint_id(member["id"], stream["id"], g): g for g in generations}
    snapshots = {g: [] for g in generations}
    transitions, state, state_events = {}, {}, []
    previous_hash = None
    for seq, row in enumerate(bundle["observations"], 1):
        body = row["body"]
        _require(set(row) == {"seq", "body", "hash"} and set(body) == {"manifest_id", "observation_revision", "parent_observation_hash", "kind", "payload"}, "observation_fields")
        _require(type(row["seq"]) is int and type(body["observation_revision"]) is int and row["seq"] == body["observation_revision"] == seq, "observation_revision")
        _require(body["manifest_id"] == manifest_id and body["parent_observation_hash"] == previous_hash and row["hash"] == digest(body), "observation_chain")
        payload = body["payload"]
        if body["kind"] == "snapshot":
            generation = payload["fields"]["source_generation_id"]
            _require(generation in generations, "snapshot_generation")
            content = bytes.fromhex(payload["content_hex"])
            _require(payload == _snapshot(generation, content), "snapshot_identity")
            chain = snapshots[generation]
            if chain:
                old = bytes.fromhex(chain[-1]["content_hex"])
                _require(content.startswith(old) and len(content) > len(old), "snapshot_append")
            chain.append(payload)
        elif body["kind"] == "transition":
            _require(set(payload) == {"transition_id", "from_generation", "from_checkpoint_id", "expected_revision", "expected_hash",
                                      "to_generation", "to_checkpoint_id", "first_content_hex", "migration_plan_ref", "initial_position"}, "transition_fields")
            tid = payload["transition_id"]
            _require(type(tid) is str and bool(tid) and tid not in transitions, "transition_identity")
            _require(payload["from_generation"] in generations, "transition_from")
            target = derive("generation", dict(source_member_id=member["id"], generation_epoch=tid, derived_from=payload["from_generation"]))
            _require(payload["to_generation"] == target and type(payload["initial_position"]) is int and payload["initial_position"] == 0, "transition_to")
            _require(payload["from_checkpoint_id"] == _checkpoint_id(member["id"], stream["id"], payload["from_generation"])
                     and payload["to_checkpoint_id"] == _checkpoint_id(member["id"], stream["id"], target), "transition_checkpoint")
            _require(type(payload["expected_revision"]) is int and payload["expected_revision"] >= 1
                     and type(payload["expected_hash"]) is str and len(payload["expected_hash"]) == 64
                     and type(payload["migration_plan_ref"]) is str and bool(payload["migration_plan_ref"]), "transition_evidence")
            bytes.fromhex(payload["first_content_hex"])
            transitions[tid] = payload
        elif body["kind"] == "state":
            _require(set(payload) == {"state_event_log_root_id", "state_event_seq", "target_id", "state_dimension", "state_value", "parent_event_hash", "evidence_ref", "observed_at", "event_hash"}, "state_fields")
            _require(type(payload["state_event_seq"]) is int and payload["state_event_seq"] == len(state_events) + 1
                     and payload["state_event_log_root_id"] == state_root, "state_sequence")
            _require(payload["parent_event_hash"] == (state_events[-1]["event_hash"] if state_events else None)
                     and payload["event_hash"] == digest({k: v for k, v in payload.items() if k != "event_hash"}), "state_chain")
            targets = {"checkpoint_status": cp_ids, "generation_status": generations, "transition_status": transitions,
                       "identity_status": {member["id"]}, "root_status": {member["id"]}}
            _require(payload["state_dimension"] in targets and payload["target_id"] in targets[payload["state_dimension"]]
                     and type(payload["evidence_ref"]) is str and bool(payload["evidence_ref"]), "state_target")
            _fold_m2(state, payload)
            state_events.append(payload)
        else:
            raise IntegrityError("unknown_observation")
        previous_hash = row["hash"]
    expected_events = {}
    for gid, generation in generations.items():
        chain = snapshots[gid]
        _require(bool(chain) and generation["snapshot_chain_root_id"] == chain[0]["id"]
                 and generation["first_snapshot_content_sha256"] == chain[0]["fields"]["content_sha256"], "snapshot_root")
        expected_events.update({r["id"]: r for r in _records(chain[-1], stream["id"])})
    persisted = {}
    for event in bundle["events"]:
        _require(event["id"] not in persisted and event == expected_events.get(event["id"]), "event_identity_or_content")
        persisted[event["id"]] = event
    checkpoints = {}
    for cp in bundle["checkpoints"]:
        _require(set(cp) == {"id", "body", "hash"} and cp["id"] in cp_ids and cp["id"] not in checkpoints, "checkpoint_identity")
        body, gid = cp["body"], cp_ids[cp["id"]]
        _require(set(body) == {"manifest_id", "key", "revision", "position", "snapshot_id", "backfill"}, "checkpoint_fields")
        _require(body["manifest_id"] == manifest_id and body["key"] == dict(source_member_id=member["id"], source_stream_id=stream["id"], source_generation_id=gid), "checkpoint_binding")
        _require(cp["hash"] == digest(body) and type(body["revision"]) is int and body["revision"] >= 1
                 and type(body["position"]) is int and body["position"] >= 0 and type(body["backfill"]) is bool, "checkpoint_hash_or_type")
        snapshot = next((s for s in snapshots[gid] if s["id"] == body["snapshot_id"]), None)
        _require(snapshot is not None, "checkpoint_snapshot")
        records = _records(snapshot, stream["id"])
        _require(body["position"] in {0, *(r["body"]["end"] for r in records)}, "checkpoint_boundary")
        _require(all(r["id"] in persisted for r in records if r["body"]["end"] <= body["position"]), "checkpoint_ahead_of_events")
        checkpoints[cp["id"]] = cp
    active = [cp for cp in checkpoints.values() if state.get((cp["id"], "checkpoint_status")) == "active"]
    for tid in transitions:
        _require((tid, "transition_status") in state, "transition_missing_status")
    return dict(registry=registry, object=obj, member=member, stream=stream, generations=generations,
                initial=initial, snapshots_by_generation=snapshots, state=state, transitions=transitions,
                checkpoints=checkpoints, active=active, persisted=persisted)


def _operational(model):
    active, state = model["active"], model["state"]
    if len(active) > 1:
        raise StoreConflict("multiple_active_checkpoints: frozen")
    if not active:
        raise StoreConflict("checkpoint_uninitialized_or_transition_incomplete: frozen")
    expected = model["initial"]["id"]
    committed_generations = {expected}
    for tid, transition in model["transitions"].items():
        status = state[(tid, "transition_status")]
        if status == "pending":
            raise StoreConflict("transition_incomplete: frozen")
        if status == "committed":
            if transition["from_generation"] != expected:
                raise StoreConflict("transition_lineage_conflict: frozen")
            old_cp = model["checkpoints"].get(transition["from_checkpoint_id"])
            if (old_cp is None or old_cp["body"]["revision"] != transition["expected_revision"]
                    or old_cp["hash"] != transition["expected_hash"]
                    or state.get((old_cp["id"], "checkpoint_status")) != "closed"
                    or state.get((expected, "generation_status")) != "closed"):
                raise StoreConflict("transition_committed_inconsistent: frozen")
            expected = transition["to_generation"]
            committed_generations.add(expected)
            chain = model["snapshots_by_generation"].get(expected)
            if not chain or chain[0]["content_hex"] != transition["first_content_hex"]:
                raise StoreConflict("transition_root_inconsistent: frozen")
        elif transition["to_generation"] in model["generations"]:
            raise StoreConflict("aborted_transition_has_generation: frozen")
    if set(model["generations"]) != committed_generations or active[0]["body"]["key"]["source_generation_id"] != expected:
        raise StoreConflict("transition_active_mismatch: frozen")
    if state.get((expected, "generation_status")) != "active":
        raise StoreConflict("generation_state_mismatch: frozen")
    model.update(generation=model["generations"][expected], checkpoint=active[0], snapshots=model["snapshots_by_generation"][expected])
    return model


class M2Store(M1Store):
    def __init__(self, path, *, fault=None):
        super().__init__(path)
        self.fault = fault

    def _hit(self, point):
        if self.fault is not None:
            self.fault(point)

    @classmethod
    def create(cls, path, **kwargs):
        # 仅对本次独占新建的空实验库增加 M2 表；已有路径由 M1 拒绝。
        initial = M1Store.create(path, **kwargs)
        bundle = initial.export_bundle()
        bundle.update(format="adrian-m2/1", events=[])
        for cp in bundle["checkpoints"]:
            cp["body"]["backfill"] = True
            cp["hash"] = digest(cp["body"])
        gid = initial.summary()["generation_id"]
        _state_event(bundle, gid, "generation_status", "active", "synthetic-initial-registration")
        _operational(_validate(bundle))
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(EVENT_SCHEMA)
            M1Store._persist(conn, bundle, initial.export_bundle())
            conn.execute(f"PRAGMA application_id={M2_APP_ID}")
            conn.execute("PRAGMA user_version=2")
        return cls.open(path)

    @classmethod
    def open(cls, path):
        store = cls(path)
        store.recover()
        return store

    @classmethod
    def rebuild(cls, path, bundle):
        bundle = _parse_json(canonical(bundle))
        _operational(_validate(bundle))
        store = cls(path)
        with store.path.open("xb"):
            pass
        with closing(sqlite3.connect(store.path)) as conn:
            conn.executescript(SCHEMA + ";" + EVENT_SCHEMA + ";")
            with conn:
                conn.execute(f"PRAGMA application_id={M2_APP_ID}")
                conn.execute("PRAGMA user_version=2")
                M1Store._persist(conn, bundle)
                for row in bundle["events"]:
                    conn.execute("INSERT INTO synthetic_events VALUES(?,?,?)", (row["id"], canonical(row["body"]).decode(), row["hash"]))
        return cls.open(path)

    @staticmethod
    def _load(conn):
        bundle = M1Store._load(conn)
        bundle.update(format="adrian-m2/1", events=[dict(id=i, body=_parse_json(b), hash=h)
            for i, b, h in conn.execute("SELECT id,body,hash FROM synthetic_events ORDER BY id")])
        return bundle

    def _persist_m2(self, conn, bundle, previous, operation):
        for row in bundle["manifest"][len(previous["manifest"]):]:
            conn.execute("INSERT INTO manifest_revisions VALUES(?,?,?)", (row["seq"], canonical(row["body"]).decode(), row["hash"]))
            self._hit(operation + ".after_manifest")
        head = bundle["head"]
        if head != previous["head"]:
            conn.execute("UPDATE manifest_head SET seq=?,hash=? WHERE singleton=1", (head["seq"], head["hash"]))
        for row in bundle["observations"][len(previous["observations"]):]:
            conn.execute("INSERT INTO observations VALUES(?,?,?)", (row["seq"], canonical(row["body"]).decode(), row["hash"]))
            payload = row["body"]["payload"]
            if row["body"]["kind"] == "state":
                dim, value = payload["state_dimension"], payload["state_value"]
                label = {("transition_status", "pending"): "after_pending",
                         ("checkpoint_status", "closed"): "after_old_closed",
                         ("checkpoint_status", "active"): "after_new_active"}.get((dim, value))
                if label:
                    self._hit(operation + "." + label)
        old_events = {r["id"] for r in previous["events"]}
        for row in bundle["events"]:
            if row["id"] not in old_events:
                conn.execute("INSERT INTO synthetic_events VALUES(?,?,?)", (row["id"], canonical(row["body"]).decode(), row["hash"]))
                self._hit(operation + ".after_insert")
        old_cps = {cp["id"]: cp for cp in previous["checkpoints"]}
        for cp in bundle["checkpoints"]:
            if cp != old_cps.get(cp["id"]):
                conn.execute("INSERT INTO checkpoints VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body,hash=excluded.hash",
                             (cp["id"], canonical(cp["body"]).decode(), cp["hash"]))
                self._hit(operation + (".after_checkpoint" if operation == "transition" else ".after_write"))

    @contextmanager
    def _transaction(self, write=False, *, operation="write", recovery=False):
        if write and self._frozen:
            raise StoreConflict("store_frozen")
        conn = None
        try:
            conn = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=5)
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            _require(conn.execute("PRAGMA application_id").fetchone()[0] == M2_APP_ID
                     and conn.execute("PRAGMA user_version").fetchone()[0] == 2, "not_m2_database")
            bundle = self._load(conn)
            model = _validate(bundle)
            if not recovery:
                _operational(model)
            previous = _parse_json(canonical(bundle)) if write else None
            yield bundle, model
            if write:
                _operational(_validate(bundle))
                self._persist_m2(conn, bundle, previous, operation)
                self._hit(operation + ".before_commit")
            conn.commit()
            if write:
                self._hit(operation + ".after_commit")
        except (IntegrityError, StateConflict):
            self._frozen = True
            raise
        except StoreConflict as exc:
            if "frozen" in str(exc):
                self._frozen = True
            raise
        except (sqlite3.DatabaseError, json.JSONDecodeError) as exc:
            self._frozen = True
            raise IntegrityError("m2_storage_error") from exc
        finally:
            if conn is not None:
                conn.close()

    def recover(self):
        with self._transaction(write=True, operation="recovery", recovery=True) as (bundle, model):
            if len(model["active"]) > 1:
                raise StoreConflict("multiple_active_checkpoints: frozen")
            for tid, transition in model["transitions"].items():
                if model["state"][(tid, "transition_status")] == "pending":
                    old = model["checkpoints"].get(transition["from_checkpoint_id"])
                    untouched = (len(model["active"]) == 1 and old == model["active"][0]
                                 and old["body"]["revision"] == transition["expected_revision"]
                                 and old["hash"] == transition["expected_hash"]
                                 and transition["to_generation"] not in model["generations"]
                                 and model["state"].get((transition["from_generation"], "generation_status")) == "active")
                    if not untouched:
                        raise StoreConflict("transition_incomplete: frozen")
                    _state_event(bundle, tid, "transition_status", "aborted", "recovery:transition_incomplete")
            if not model["active"]:
                if model["transitions"] or model["checkpoints"] or len(model["generations"]) != 1:
                    raise StoreConflict("transition_incomplete: zero active; frozen")
                gid = model["initial"]["id"]
                cp = _new_checkpoint(bundle["head"]["manifest_id"], model["member"]["id"], model["stream"]["id"],
                                     gid, model["generations"][gid]["snapshot_chain_root_id"])
                if (cp["id"], "checkpoint_status") in model["state"]:
                    raise StoreConflict("checkpoint_history_without_row: frozen")
                bundle["checkpoints"].append(cp)
                _state_event(bundle, cp["id"], "checkpoint_status", "active", "recovery:checkpoint_uninitialized")
                if (gid, "generation_status") not in model["state"]:
                    _state_event(bundle, gid, "generation_status", "active", "recovery:checkpoint_uninitialized")

    def generations(self):
        with self._transaction() as (_, model):
            return list(model["generations"].values())

    def events(self):
        with self._transaction() as (bundle, _):
            return bundle["events"]

    def identity_inventory(self):
        with self._transaction() as (bundle, model):
            return sorted([bundle["head"]["manifest_id"], *model["registry"], *model["checkpoints"],
                           *[s["id"] for chain in model["snapshots_by_generation"].values() for s in chain]])

    def read_events(self):
        with self._transaction() as (_, model):
            return [row for row in _records(model["snapshots"][-1], model["stream"]["id"])
                    if row["body"]["end"] > model["checkpoint"]["body"]["position"]]

    def persist_events(self, records):
        with self._transaction(write=True, operation="events") as (bundle, model):
            valid = {r["id"]: r for r in _records(model["snapshots"][-1], model["stream"]["id"])}
            existing = dict(model["persisted"])
            for record in records:
                if record != valid.get(record.get("id")):
                    raise StoreConflict("event_key_or_payload_conflict")
                if record["id"] not in existing:
                    bundle["events"].append(record)
                    existing[record["id"]] = record

    def advance_checkpoint(self, position, *, expected_revision, expected_hash):
        with self._transaction(write=True, operation="checkpoint") as (_, model):
            cp = model["checkpoint"]
            if type(expected_revision) is not int or cp["body"]["revision"] != expected_revision or cp["hash"] != expected_hash:
                raise StoreConflict("checkpoint_cas")
            records = _records(model["snapshots"][-1], model["stream"]["id"])
            if type(position) is not int or position < cp["body"]["position"] or position not in {0, *(r["body"]["end"] for r in records)}:
                raise ValueError("invalid_checkpoint_boundary")
            if any(r["id"] not in model["persisted"] for r in records if r["body"]["end"] <= position):
                raise StoreConflict("events_not_committed")
            if position != cp["body"]["position"]:
                cp["body"].update(revision=cp["body"]["revision"] + 1, position=position, snapshot_id=model["snapshots"][-1]["id"])
                cp["hash"] = digest(cp["body"])
            return {"id": cp["id"], "hash": cp["hash"], **cp["body"]}

    def consume(self):
        # 读与写阶段之间都可能中断；CAS 和 generation 键保护重试/迟到结果。
        cp = self.checkpoint()
        records = self.read_events()
        if not records:
            return cp
        self.persist_events(records)
        self._hit("consume.after_events")
        return self.advance_checkpoint(records[-1]["body"]["end"], expected_revision=cp["revision"], expected_hash=cp["hash"])

    def transition(self, *, transition_id, from_generation, expected_revision, expected_hash,
                   first_content, migration_plan_ref):
        if type(transition_id) is not str or not transition_id or type(first_content) is not bytes or not migration_plan_ref:
            raise ValueError("invalid_transition_input")
        with self._transaction(write=True, operation="transition") as (bundle, model):
            gid = derive("generation", dict(source_member_id=model["member"]["id"], generation_epoch=transition_id, derived_from=from_generation))
            proposal = dict(transition_id=transition_id, from_generation=from_generation,
                            from_checkpoint_id=_checkpoint_id(model["member"]["id"], model["stream"]["id"], from_generation),
                            expected_revision=expected_revision, expected_hash=expected_hash, to_generation=gid,
                            to_checkpoint_id=_checkpoint_id(model["member"]["id"], model["stream"]["id"], gid),
                            first_content_hex=first_content.hex(), migration_plan_ref=migration_plan_ref, initial_position=0)
            if transition_id in model["transitions"]:
                if proposal != model["transitions"][transition_id]:
                    raise StoreConflict("transition_id_conflict")
                if model["state"][(transition_id, "transition_status")] != "committed":
                    raise StoreConflict("transition_aborted")
                return gid
            cp = model["checkpoint"]
            if type(expected_revision) is not int or cp["body"]["key"]["source_generation_id"] != from_generation or cp["body"]["revision"] != expected_revision or cp["hash"] != expected_hash:
                raise StoreConflict("checkpoint_cas")
            if cp["body"]["position"] != model["snapshots"][-1]["fields"]["decompressed_content_length"]:
                raise StoreConflict("generation_not_drained")
            snapshot = _snapshot(gid, first_content)
            entry = _entry("generation", dict(source_member_id=model["member"]["id"], generation_epoch=transition_id, derived_from=from_generation),
                           snapshot_chain_root_id=snapshot["id"], first_snapshot_content_sha256=snapshot["fields"]["content_sha256"])
            _register(bundle, entry)
            _observe(bundle, "snapshot", snapshot)
            _observe(bundle, "transition", proposal)
            _state_event(bundle, transition_id, "transition_status", "pending", migration_plan_ref)
            _state_event(bundle, from_generation, "generation_status", "closed", transition_id)
            _state_event(bundle, cp["id"], "checkpoint_status", "closed", transition_id)
            new_cp = _new_checkpoint(bundle["head"]["manifest_id"], model["member"]["id"], model["stream"]["id"], gid, snapshot["id"])
            bundle["checkpoints"].append(new_cp)
            _state_event(bundle, gid, "generation_status", "active", transition_id)
            _state_event(bundle, new_cp["id"], "checkpoint_status", "active", transition_id)
            _state_event(bundle, transition_id, "transition_status", "committed", migration_plan_ref)
            return gid
