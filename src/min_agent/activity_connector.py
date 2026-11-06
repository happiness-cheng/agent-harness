"""最小只读 Activity CLI；不接 Event Store、Checkpoint 或 Memory。"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

from min_agent.activity_identity import derive, object_identity
from min_agent.activity_records import fingerprint, make_record
from min_agent.activity_reader import ReadFailure, is_link, lines


CODEX_TYPES = frozenset(("response_item", "event_msg", "turn_context", "world_state", "token_usage_record"))
SAFE_TYPES = CODEX_TYPES | frozenset(("session_meta", "session",
    "approval/policy", "permission/preset", "sandbox/mode", "agent/inbox/spliced", "assistant/chunk",
    "assistant/message", "llm/retry", "llm/retry-started", "reasoning-chunks", "request/context",
    "request/header", "session/end-seed", "session/title", "session/title-llm-request", "step/end",
    "step/start", "text-chunks", "turn/end", "turn/start", "user/message", "tool/call", "tool/result"))
LIMITS = ["KNOWN_LIMIT: read-only; no Event Store, Memory or shared Checkpoint writes",
    "KNOWN_LIMIT: M5/M6 deferred; root/generation/fork unresolved; all record identities provisional",
    "KNOWN_LIMIT: physical copies counted separately, never merged",
    "KNOWN_LIMIT: double-read fingerprints detect observed changes, not an OS-locked snapshot",
    "KNOWN_LIMIT: only observed Codex rollout and DSH session versions 4/0; unknown schemas rejected",
    "KNOWN_LIMIT: timeline/errors bounded; unknown type values redacted; no task/model-intent inference",
    "KNOWN_LIMIT: existing fingerprint canonicalizer does not support floats; flagged without inventing a hash"]


def _timestamp(value):
    try:
        if type(value) in (int, float):
            dt = datetime.fromtimestamp(value / 1000 if value > 100_000_000_000 else value, timezone.utc)
        elif type(value) is str and len(value) <= 40:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                return None
        else:
            return None
        if not 2000 <= dt.year <= 2100:
            return None
        return dt.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    except (ValueError, OverflowError, OSError):
        return None


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_key")
            result[key] = value
        return result
    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite")))


def _header(row):
    if row.get("type") == "session_meta" and type(row.get("payload")) is dict:
        return "codex/rollout", row["payload"].get("id"), row["payload"].get("timestamp", row.get("timestamp"))
    if row.get("type") == "session" and type(row.get("version")) is int and row["version"] in (0, 4):
        return ("dsh/v4" if row["version"] == 4 else "dsh/old"), row.get("id"), row.get("createdAt")
    raise ValueError("unsupported_format")


def _file(path, *, timeline_limit, max_line, max_bytes, max_records):
    source_ref = "source_" + hashlib.sha256(os.fsencode(str(path.absolute()))).hexdigest()[:20]
    result = dict(source_ref=source_ref, profile=None, object_id=None, parsed_records=0, failed_records=0,
        provisional_records=0, unresolved_records=0, timeline=[], errors=[], by_type={}, by_namespace={},
        stable=False, complete=False, time_range=None, timeline_omitted=0, errors_omitted=0,
        identity_status="unresolved", change_classification="unresolved_no_persistent_baseline")
    meta, types, namespaces = {}, Counter(), Counter()
    times = [None, None]
    header_seen = False
    identities_blocked = False

    def error(code, line=None, offset=None):
        item = dict(code=code, line_index=line, logical_byte_offset=offset,
                    physical_byte_offset=offset if meta.get("compression") == "none" else None)
        if len(result["errors"]) < 100:
            result["errors"].append(item)
        else:
            result["errors_omitted"] += 1

    def add_time(value):
        value = _timestamp(value)
        if value:
            times[0] = min(times[0], value) if times[0] else value
            times[1] = max(times[1], value) if times[1] else value
        return value

    try:
        if is_link(path):
            raise ReadFailure("symlink_or_reparse_point_skipped")
        for line_no, offset, raw, status in lines(path, meta, max_line=max_line, max_bytes=max_bytes):
            if line_no >= max_records:
                raise ReadFailure("record_count_limit")
            if status != "ok":
                result["failed_records"] += 1
                error(status, line_no, offset)
                continue
            try:
                row = _json(raw)
                if type(row) is not dict or type(row.get("type")) is not str:
                    raise ValueError("invalid_record_shape")
            except (ValueError, UnicodeError, RecursionError):
                result["failed_records"] += 1
                error("invalid_json_or_record_shape", line_no, offset)
                continue
            if not header_seen:
                try:
                    profile, native_id, stamp = _header(row)
                except ValueError:
                    result["failed_records"] += 1
                    error("unsupported_format", line_no, offset)
                    identities_blocked = True
                    break
                header_seen = True
                result["profile"] = profile
                if type(native_id) is str and native_id and len(native_id) <= 256:
                    result["object_id"] = object_identity(profile.split('/')[0], "session", native_id, "history")["id"]
                else:
                    error("missing_session_id", line_no, offset)
                result["parsed_records"] += 1
                types[row["type"]] += 1
                add_time(stamp)
                continue
            if row["type"] in ("session", "session_meta"):
                result["failed_records"] += 1
                error("unresolved_multiple_session_headers", line_no, offset)
                identities_blocked = True
                # 后续行不再借用首个 session 的来源身份。
                break
            profile = result["profile"]
            if profile == "codex/rollout":
                if row["type"] not in CODEX_TYPES or type(row.get("payload")) is not dict:
                    result["failed_records"] += 1
                    error("unsupported_record_schema", line_no, offset)
                    continue
                namespace, seq_field, time_field, payload = "codex.ordinal", "ordinal", "timestamp", row["payload"]
            else:
                if type(row.get("data")) is not dict or profile == "dsh/v4" and "seq0" in row:
                    result["failed_records"] += 1
                    error("unsupported_record_schema", line_no, offset)
                    continue
                if "seq" in row and "seq0" in row:
                    result["failed_records"] += 1
                    error("ambiguous_sequence_namespace", line_no, offset)
                    continue
                if profile == "dsh/old" and "seq" not in row and "seq0" not in row:
                    result["failed_records"] += 1
                    result["unresolved_records"] += 1
                    error("missing_sequence_namespace", line_no, offset)
                    continue
                namespace = "dsh.seq0" if profile == "dsh/old" and "seq0" in row else "dsh.seq"
                seq_field, time_field = ("seq0", "time0") if namespace == "dsh.seq0" else ("seq", "time")
                payload = row["data"]
            locator = dict(line_index=line_no, byte_offset_in_decompressed_stream=offset)
            if seq_field in row:
                locator[seq_field] = row[seq_field]
            run = derive("readonly_observation", {"source_ref": source_ref})
            member = derive("provisional_member", {"object_id": result["object_id"], "observation_run_id": run})
            generation = derive("provisional_generation", {"source_member_id": member, "observation_run_id": run})
            try:
                # 只复用定位规范；未知 root/generation 时丢弃 definitive 候选，绝不输出为事实。
                normalized = make_record(dict(profile=profile, seq_namespace=namespace, locator=locator,
                    structure={}, payload={}, observation_run_id=run, observation_ordinal=line_no),
                    source_member_id=member, source_generation_id=generation)
            except (ValueError, TypeError):
                result["failed_records"] += 1
                error("invalid_locator_fields", line_no, offset)
                continue
            if normalized["identity_provisional"]:
                error("missing_required_locator", line_no, offset)
            record_type = row["type"] if row["type"] in SAFE_TYPES else "other"
            stamp = add_time(row.get(time_field))
            if row.get(time_field) is not None and stamp is None:
                error("invalid_timestamp", line_no, offset)
            try:
                fp = fingerprint({"type": row["type"]}, payload)
                fp_status = "available"
            except ValueError:
                fp, fp_status = None, "unsupported_canonical_value"
            provisional_id = derive("provisional_record", dict(source_member_id=member,
                source_stream_id=normalized["source_stream_id"], source_generation_id=generation,
                observation_run_id=run, observation_ordinal=line_no))
            item = dict(record_type=record_type, timestamp=stamp, seq_namespace=namespace,
                seq_value=normalized["locator_fields"]["seq_value"], line_index=line_no,
                logical_byte_offset=offset, physical_byte_offset=offset if meta["compression"] == "none" else None,
                parse_status="parsed", identity_status="provisional", native_record_id=None,
                provisional_record_id=provisional_id, locator_confidence=normalized["locator_confidence"],
                fingerprint_status=fp_status)
            # 指纹仅在内存计算；报告不导出正文或正文哈希。
            del fp
            if len(result["timeline"]) < timeline_limit:
                result["timeline"].append(item)
            else:
                result["timeline_omitted"] += 1
            types[record_type] += 1
            namespaces[namespace] += 1
            result["parsed_records"] += 1
            result["provisional_records"] += 1
            result["unresolved_records"] += 1
    except ReadFailure as exc:
        error(str(exc))
    except (OSError, ValueError, RecursionError):
        error("read_error")
    result.update(meta)
    if not header_seen and not result["errors"]:
        error("unsupported_format")
    if not meta.get("stable", False):
        error("unstable_or_unverified_read")
    result["complete"] = bool(meta.get("complete")) and result["failed_records"] == 0 and not identities_blocked
    if identities_blocked:
        result["object_id"] = None
    result["by_type"], result["by_namespace"] = dict(sorted(types.items())), dict(sorted(namespaces.items()))
    result["time_range"] = dict(start=times[0], end=times[1]) if times[0] else None
    result["status"] = "ok" if result["complete"] and result["stable"] and not result["errors"] else "partial"
    return result


def scan(input_path, *, max_files=100, timeline_limit=500, max_line=4*1024*1024,
         max_bytes=256*1024*1024, max_records=100000):
    if any(type(x) is not int or x <= 0 for x in (max_files, timeline_limit, max_line, max_bytes, max_records)):
        raise ValueError("invalid_limits")
    root = Path(input_path)
    files, scan_errors = [], []
    try:
        if is_link(root):
            scan_errors.append("symlink_or_reparse_point_skipped")
        elif root.is_file():
            files.append(root)
        elif root.is_dir():
            stack = [root]
            while stack and len(files) <= max_files:
                current = stack.pop()
                with os.scandir(current) as entries:
                    for entry in entries:
                        p = Path(entry.path)
                        if is_link(p):
                            scan_errors.append("symlink_or_reparse_point_skipped")
                        elif entry.is_dir(follow_symlinks=False):
                            stack.append(p)
                        elif p.suffix.lower() in (".jsonl", ".zst", ".zstd"):
                            files.append(p)
                            if len(files) > max_files:
                                break
            if len(files) > max_files or stack:
                scan_errors.append("file_count_limit")
        else:
            scan_errors.append("unsupported_path")
    except OSError:
        scan_errors.append("input_access_error")
    reports = [_file(p, timeline_limit=timeline_limit, max_line=max_line, max_bytes=max_bytes, max_records=max_records)
               for p in sorted(files, key=lambda p: str(p))[:max_files]]
    by_source = Counter()
    by_type = Counter()
    for r in reports:
        by_source[r["profile"] or "unsupported_format"] += r["parsed_records"]
        by_type.update(r["by_type"])
        related = [other["source_ref"] for other in reports if r["object_id"] is not None
                   and other["object_id"] == r["object_id"] and other is not r]
        if related:
            r["related_source_refs"] = related
            r["relationship"] = "unresolved_same_object_multiple_files"
    ranges = [r["time_range"] for r in reports if r["time_range"]]
    return dict(format="adrian-readonly-activity/1", files_scanned=len(reports),
        files_with_errors=sum(r["status"] != "ok" for r in reports),
        parsed_records=sum(r["parsed_records"] for r in reports), failed_records=sum(r["failed_records"] for r in reports),
        provisional_records=sum(r["provisional_records"] for r in reports), unresolved_records=sum(r["unresolved_records"] for r in reports),
        by_source=dict(sorted(by_source.items())), by_type=dict(sorted(by_type.items())),
        time_range=dict(start=min(r["start"] for r in ranges), end=max(r["end"] for r in ranges)) if ranges else None,
        files=reports, scan_errors=sorted(set(scan_errors)), known_limits=LIMITS)


def main(argv=None):
    parser = argparse.ArgumentParser(description="只读 Activity 元数据报告；不输出历史正文")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, help="仅创建新报告，禁止放在扫描目录内部")
    parser.add_argument("--max-files", type=int, default=100)
    parser.add_argument("--timeline-limit", type=int, default=500)
    args = parser.parse_args(argv)
    try:
        if args.output:
            target, source = args.output.resolve(), args.input.resolve()
            if target == source or args.input.is_dir() and target.is_relative_to(source):
                raise ValueError("output_overlaps_input")
        result = scan(args.input, max_files=args.max_files, timeline_limit=args.timeline_limit)
        data = (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if args.output:
            with args.output.open("xb") as f:
                f.write(data)
        else:
            print(data.decode("utf-8"), end="")
        return 0 if not result["scan_errors"] and all(f["status"] == "ok" for f in result["files"]) else 2
    except (OSError, ValueError):
        print('{"error":"invalid_arguments_or_report_output_refused"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
