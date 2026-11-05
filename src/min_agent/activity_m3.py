"""M3 合成记录存储：复用 M2 SQL 事务，追加记录/映射/持久冲突。"""
from contextlib import closing, contextmanager
import sqlite3

from min_agent.activity_identity import canonical, derive, digest
from min_agent.activity_records import make_record, profile_spec
from min_agent.activity_store import M1Store, IntegrityError, StoreConflict, SCHEMA, _parse_json, _require, _state_event
from min_agent.activity_m2 import M2Store, EVENT_SCHEMA, _validate, _operational, _records


M3_SCHEMA = """
CREATE TABLE record_profile(singleton INTEGER PRIMARY KEY CHECK(singleton=1), body TEXT NOT NULL);
CREATE TABLE record_log(seq INTEGER PRIMARY KEY, body TEXT NOT NULL, hash TEXT NOT NULL);
"""


def _base(bundle):
    return {k: ("adrian-m2/1" if k == "format" else v) for k, v in bundle.items()
            if k not in ("record_profile", "record_log")}


def _append(bundle, kind, payload):
    log = bundle["record_log"]
    body = dict(kind=kind, payload=payload, parent_hash=log[-1]["hash"] if log else None)
    log.append(dict(seq=len(log) + 1, body=body, hash=digest(body)))


def _from_transport(event, model):
    envelope = _parse_json(bytes.fromhex(event["body"]["content_hex"]))
    profile = model["record_profile"]
    if envelope["profile"] != profile["profile"] or envelope["seq_namespace"] != profile["seq_namespace"]:
        raise ValueError("record_stream_profile_mismatch")
    return make_record(envelope, source_member_id=model["member"]["id"],
                       source_generation_id=event["body"]["source_generation_id"])


def _candidates(records, source):
    return frozenset(r["id"] for r in records.values() if not r["identity_provisional"]
        and all(r[k] == source[k] for k in ("source_member_id", "source_stream_id", "source_generation_id", "record_fingerprint")))


def _evidence_ok(evidence, source, target):
    return (type(evidence) is dict and set(evidence) == {"kind", "source_id", "target_id", "reference"}
            and evidence["kind"] in ("native_id", "lineage", "phase0b_confirmation")
            and evidence["source_id"] == source and evidence["target_id"] == target
            and type(evidence["reference"]) is str and bool(evidence["reference"]))


class M3Store(M2Store):
    application_id = 0x414D3301
    schema_version = 3

    @classmethod
    def create(cls, path, *, profile, seq_namespace, **kwargs):
        profile_spec(profile, seq_namespace)
        # 仅转换刚刚独占创建的合成初始文件；已有路径由 M1 拒绝。
        initial = M1Store.create(path, seq_namespace=seq_namespace, **kwargs)
        bundle = initial.export_bundle()
        bundle.update(format="adrian-m3/1", events=[], record_profile=dict(profile=profile, seq_namespace=seq_namespace), record_log=[])
        for cp in bundle["checkpoints"]:
            cp["body"]["backfill"] = True
            cp["hash"] = digest(cp["body"])
        _state_event(bundle, initial.summary()["generation_id"], "generation_status", "active", "synthetic-m3-initial")
        _operational(cls._validate_bundle(bundle))
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(EVENT_SCHEMA)
            for statement in M3_SCHEMA.split(";"):
                if statement.strip():
                    conn.execute(statement)
            M1Store._persist(conn, bundle, initial.export_bundle())
            conn.execute("INSERT INTO record_profile VALUES(1,?)", (canonical(bundle["record_profile"]).decode(),))
            conn.execute(f"PRAGMA application_id={cls.application_id}")
            conn.execute("PRAGMA user_version=3")
        return cls.open(path)

    @classmethod
    def rebuild(cls, path, bundle):
        bundle = _parse_json(canonical(bundle))
        _operational(cls._validate_bundle(bundle))
        store = cls(path)
        with store.path.open("xb"):
            pass
        with closing(sqlite3.connect(path)) as conn:
            conn.executescript(SCHEMA + ";" + EVENT_SCHEMA + ";" + M3_SCHEMA)
            with conn:
                conn.execute(f"PRAGMA application_id={cls.application_id}")
                conn.execute("PRAGMA user_version=3")
                M1Store._persist(conn, bundle)
                for row in bundle["events"]:
                    conn.execute("INSERT INTO synthetic_events VALUES(?,?,?)", (row["id"], canonical(row["body"]).decode(), row["hash"]))
                conn.execute("INSERT INTO record_profile VALUES(1,?)", (canonical(bundle["record_profile"]).decode(),))
                for row in bundle["record_log"]:
                    conn.execute("INSERT INTO record_log VALUES(?,?,?)", (row["seq"], canonical(row["body"]).decode(), row["hash"]))
        return cls.open(path)

    @staticmethod
    def _load(conn):
        bundle = M2Store._load(conn)
        row = conn.execute("SELECT body FROM record_profile WHERE singleton=1").fetchone()
        _require(row is not None, "missing_record_profile")
        bundle.update(format="adrian-m3/1", record_profile=_parse_json(row[0]), record_log=[
            dict(seq=s, body=_parse_json(b), hash=h) for s, b, h in conn.execute("SELECT seq,body,hash FROM record_log ORDER BY seq")])
        return bundle

    @staticmethod
    def _validate_bundle(bundle):
        try:
            _require(bundle["format"] == "adrian-m3/1", "m3_format")
            profile = bundle["record_profile"]
            _require(set(profile) == {"profile", "seq_namespace"}, "record_profile_fields")
            profile_spec(profile["profile"], profile["seq_namespace"])
            model = _validate(_base(bundle), profile["seq_namespace"])
            model.update(record_profile=profile, records={}, mappings={}, conflicts=[])
            previous = None
            for seq, row in enumerate(bundle["record_log"], 1):
                body = row["body"]
                _require(set(row) == {"seq", "body", "hash"} and type(row["seq"]) is int and row["seq"] == seq
                         and set(body) == {"kind", "payload", "parent_hash"} and body["parent_hash"] == previous
                         and row["hash"] == digest(body), "record_log_chain")
                kind, payload = body["kind"], body["payload"]
                if kind == "record":
                    expected = make_record(payload["envelope"], source_member_id=model["member"]["id"], source_generation_id=payload["source_generation_id"])
                    _require(payload == expected and payload["id"] not in model["records"]
                             and payload["source_generation_id"] in model["generations"]
                             and payload["envelope"]["profile"] == profile["profile"]
                             and payload["source_stream_id"] == model["stream"]["id"], "record_identity")
                    model["records"][payload["id"]] = payload
                elif kind == "record_conflict":
                    _require(set(payload) == {"existing", "incoming"}, "record_conflict_fields")
                    a, b = payload["existing"], payload["incoming"]
                    for r in (a, b):
                        _require(r == make_record(r["envelope"], source_member_id=model["member"]["id"], source_generation_id=r["source_generation_id"])
                                 and r["source_generation_id"] in model["generations"]
                                 and r["source_stream_id"] == model["stream"]["id"], "conflict_evidence")
                    _require(a["id"] == b["id"] and a["record_fingerprint"] != b["record_fingerprint"], "conflict_evidence_mismatch")
                    model["conflicts"].append(payload)
                elif kind == "IdentityTransition":
                    source = model["records"][payload["source_id"]]
                    _require(source["identity_provisional"] and _candidates(model["records"], source) == {payload["target_id"]}
                             and _evidence_ok(payload["evidence"], source["id"], payload["target_id"])
                             and set(payload) == {"source_id", "target_id", "evidence", "transition_id"}
                             and payload["transition_id"] == derive("identity_transition", {k: v for k, v in payload.items() if k != "transition_id"})
                             and source["id"] not in model["mappings"], "invalid_identity_mapping")
                    model["mappings"][source["id"]] = payload
                else:
                    raise IntegrityError("unknown_record_log_kind")
                previous = row["hash"]
            covered = set()
            for event in bundle["events"]:
                r = _from_transport(event, model)
                _require(r["id"] in model["records"] and model["records"][r["id"]]["record_fingerprint"] == r["record_fingerprint"], "transport_record_not_committed")
                covered.add(r["id"])
            _require(covered == set(model["records"]), "record_without_transport")
            return model
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise IntegrityError("invalid_m3_structure") from exc

    def _persist_m2(self, conn, bundle, previous, operation):
        super()._persist_m2(conn, bundle, previous, operation)
        for row in bundle["record_log"][len(previous["record_log"]):]:
            conn.execute("INSERT INTO record_log VALUES(?,?,?)", (row["seq"], canonical(row["body"]).decode(), row["hash"]))
            self._hit("record_log.after_" + row["body"]["kind"])

    @contextmanager
    def _transaction(self, write=False, **kwargs):
        with super()._transaction(write, **kwargs) as (bundle, model):
            if write and model["conflicts"]:
                raise StoreConflict("record_conflict: persistent checkpoint freeze")
            yield bundle, model

    def recover(self):
        # 冲突库允许只读检查，任何写入口仍检查持久证据。
        with self._transaction(recovery=True) as (_, model):
            conflicts = bool(model["conflicts"])
            if conflicts:
                _operational(model)
        if not conflicts:
            super().recover()

    def records(self):
        with self._transaction() as (_, model):
            rows = list(model["records"].values())
            return [{**r, "is_same_payload": any(other["id"] != r["id"] and other["record_fingerprint"]["payload_hash"] == r["record_fingerprint"]["payload_hash"] for other in rows)} for r in rows]

    def mappings(self):
        with self._transaction() as (_, model):
            return list(model["mappings"].values())

    def persist_events(self, records):
        conflict = None
        with self._transaction(write=True, operation="events") as (bundle, model):
            valid = {r["id"]: r for r in _records(model["snapshots"][-1], model["stream"]["id"])}
            known = dict(model["records"])
            additions, transport = [], []
            existing_events = set(model["persisted"])
            for event in records:
                if event != valid.get(event.get("id")):
                    raise StoreConflict("event_key_or_payload_conflict")
                record = _from_transport(event, model)
                old = known.get(record["id"])
                if old is not None and old["record_fingerprint"] != record["record_fingerprint"]:
                    conflict = dict(existing=old, incoming=record)
                    break
                if old is None:
                    known[record["id"]] = record
                    additions.append(record)
                if event["id"] not in existing_events:
                    transport.append(event)
                    existing_events.add(event["id"])
            if conflict:
                # 批次记录及传输事件全部放弃；同一事务只提交冻结证据。
                _append(bundle, "record_conflict", conflict)
            else:
                bundle["events"].extend(transport)
                for record in additions:
                    _append(bundle, "record", record)
        if conflict:
            raise StoreConflict("record_conflict: persistent checkpoint freeze")

    def map_identity(self, provisional_id, *, evidence=None):
        with self._transaction(write=True, operation="mapping") as (bundle, model):
            source = model["records"].get(provisional_id)
            if source is None or not source["identity_provisional"]:
                raise ValueError("not_a_provisional_record")
            candidates = _candidates(model["records"], source)
            if len(candidates) > 1:
                return dict(status="identity_mapping_ambiguous", candidates=candidates)
            if not candidates:
                return dict(status="unresolved", candidates=candidates)
            target = next(iter(candidates))
            if not _evidence_ok(evidence, provisional_id, target):
                return dict(status="evidence_required", candidates=candidates)
            fields = dict(source_id=provisional_id, target_id=target, evidence=evidence)
            transition = dict(**fields, transition_id=derive("identity_transition", fields))
            previous = model["mappings"].get(provisional_id)
            if previous is not None and previous != transition:
                raise StoreConflict("identity_mapping_conflict")
            if previous is None:
                _append(bundle, "IdentityTransition", transition)
            return dict(status="mapped", candidates=candidates, transition=transition)
