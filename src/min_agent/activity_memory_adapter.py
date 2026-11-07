"""跨项目第一批：仅合成沙盒，报告级 Activity 候选，不写正式 Vault。"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

import yaml

from min_agent.activity_connector import scan, SAFE_TYPES, LIMITS
from min_agent.activity_identity import canonical
from min_agent.activity_reader import is_link


CONTRACT = "adrian-pm-candidate/0.1"
PLATFORMS = ("Codex", "DSH", "ChatGPT", "DeepSeek")
PROFILES = {"codex/rollout": "Codex", "dsh/v4": "DSH", "dsh/old": "DSH"}
MARKER = ".adrian-pm-sandbox.json"


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _stamp(value):
    if type(value) is not str or len(value) > 40:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None or not 2000 <= stamp.year <= 2100:
            return None
        return stamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except ValueError:
        return None


def _id(value, pattern=r"id1_[0-9a-f]{64}"):
    return value if type(value) is str and re.fullmatch(pattern, value) else None


def _number(value):
    return value if type(value) is int and 0 <= value <= 2**53 - 1 else None


def _blocked(item):
    return item.get("sensitivity") not in (None, "synthetic", "public_synthetic") or item.get("health_isolated") is True


def candidate_id(platform, source_id, pointer):
    if platform not in PLATFORMS or not _id(source_id, r"src_[0-9a-f]{64}"):
        raise ValueError("invalid_candidate_identity")
    return "act_" + _sha(canonical(dict(contract=CONTRACT, platform=platform, source_id=source_id, record_pointer=pointer)))


def convert_report(report, *, observed_at):
    """只投影白名单元数据；报告不等于消息正文，不能升格原始证据。"""
    observed_at = _stamp(observed_at)
    if observed_at is None or type(report) is not dict or report.get("format") != "adrian-readonly-activity/1":
        raise ValueError("unsupported_report_or_observation_time")
    records, skipped = [], []
    if _blocked(report):
        skipped.append("isolated_or_sensitive_report")
    else:
        files = report.get("files")
        if type(files) is not list or len(files) > 100:
            raise ValueError("invalid_report_files")
        for fi, file in enumerate(files):
            if type(file) is not dict:
                raise ValueError("invalid_report_file")
            if _blocked(file):
                skipped.append("isolated_or_sensitive_source")
                continue
            profile = file.get("profile")
            platform = PROFILES.get(profile) if type(profile) is str else None
            if platform is None:
                skipped.append("unsupported_platform_adapter")
                continue
            timeline = file.get("timeline", [])
            if type(timeline) is not list or len(timeline) > 2000:
                raise ValueError("invalid_timeline")
            for ri, row in enumerate(timeline):
                if type(row) is not dict:
                    raise ValueError("invalid_record")
                if _blocked(row):
                    skipped.append("isolated_or_sensitive_record")
                    continue
                namespace = row.get("seq_namespace")
                allowed = ("codex.ordinal",) if platform == "Codex" else ("dsh.seq", "dsh.seq0")
                locator = dict(line_index=_number(row.get("line_index")), logical_byte_offset=_number(row.get("logical_byte_offset")),
                    physical_byte_offset=_number(row.get("physical_byte_offset")), seq_namespace=namespace if namespace in allowed else None,
                    seq_value=_number(row.get("seq_value")))
                records.append(dict(platform=platform, report_pointer=f"/files/{fi}/timeline/{ri}",
                    adrian_ids=dict(source_ref=_id(file.get("source_ref"), r"source_[0-9a-f]{20}"), object_id=_id(file.get("object_id")),
                        native_record_id=_id(row.get("native_record_id")), provisional_record_id=_id(row.get("provisional_record_id"))),
                    source_recorded_at=_stamp(row.get("timestamp")), locator=locator,
                    record_type=row.get("record_type") if type(row.get("record_type")) is str and row.get("record_type") in SAFE_TYPES else "other",
                    upstream_stable=file.get("stable") is True, upstream_complete=file.get("complete") is True,
                    identity_status="provisional" if row.get("identity_status") == "provisional" else "unresolved"))
    # Source 身份严格绑定实际输出的元数据摘录字节，不冒充原历史文件 hash。
    source_bytes = canonical(dict(format="adrian-pm-report-excerpt/1", records=records)) + b"\n"
    source_id = "src_" + _sha(source_bytes)
    reported_limits = report.get("known_limits", [])
    upstream_limits = [limit for limit in LIMITS if type(reported_limits) is list and limit in reported_limits]
    candidates, mappings = [], []
    for index, row in enumerate(records):
        key = candidate_id(row["platform"], source_id, row["report_pointer"])
        locator_present = row["adrian_ids"]["source_ref"] is not None and all(
            row["locator"][k] is not None for k in ("line_index", "logical_byte_offset", "seq_namespace", "seq_value"))
        ref = dict(source_id=source_id, evidence_kind="report_metadata", report_pointer=row["report_pointer"],
            line_anchor=None, message_id=None, message_index=None, role=None, quote=None,
            reference_validity="valid_report_reference", original_reference_validity="unverified" if locator_present else "missing_locator",
            locator=row["locator"], card_link=f"Sources/{source_id}#^record-{index:06d}")
        candidates.append(dict(record_type="activity_candidate", activity_id=key, platform=row["platform"],
            title="Agent 活动观察", activity_kind="conversation", activity_subtype="agent_activity_observation",
            status="unknown", event_at=None, event_at_basis="unknown", conversation_at=None,
            source_recorded_at=row["source_recorded_at"], observed_at=observed_at, categories=["agent_observation"],
            project_id=None, confidence="LOW", review_state="draft", verification="unverified",
            identity_status=row["identity_status"], resolution_state="unresolved", eligible_for_import=False,
            compatibility="blocked_missing_message_evidence", is_long_term_memory=False,
            known_limit=["report_metadata_only", "no_user_or_external_evidence", "M5_M6_deferred", "original_locator_not_verified"],
            upstream_known_limits=upstream_limits,
            upstream_stable=row["upstream_stable"], upstream_complete=row["upstream_complete"],
            adrian_ids=row["adrian_ids"], evidence_refs=[ref]))
        mapping = dict(platform=row["platform"], source_id=source_id, activity_id=key, report_pointer=row["report_pointer"],
                       adrian_ids=row["adrian_ids"], relation="observed_in_report_not_identity_equivalence")
        mappings.append(dict(mapping_id="map_" + _sha(canonical(mapping)), **mapping))
    return dict(source_id=source_id, source_bytes=source_bytes, candidates=candidates, mappings=mappings, skipped=skipped)


def create_sandbox():
    root = Path(tempfile.mkdtemp(prefix="adrian-pm-adapter-"))
    metadata = dict(format=CONTRACT, synthetic_only=True, observed_at=datetime.now(timezone.utc).isoformat())
    (root / MARKER).write_bytes(canonical(metadata))
    return root


def _sandbox(root):
    root = Path(root).absolute()
    temp = Path(tempfile.gettempdir()).resolve()
    if root.parent.resolve() != temp or not root.name.startswith("adrian-pm-adapter-"):
        raise ValueError("output_not_adapter_sandbox")
    for parent in (root, *root.parents):
        if is_link(parent):
            raise ValueError("linked_sandbox_refused")
    marker = root / MARKER
    if not marker.is_file() or is_link(marker) or marker.stat().st_size > 4096:
        raise ValueError("missing_sandbox_marker")
    metadata = json.loads(marker.read_bytes())
    if metadata.get("format") != CONTRACT or metadata.get("synthetic_only") is not True or not _stamp(metadata.get("observed_at")):
        raise ValueError("invalid_sandbox_marker")
    return root, metadata


def _note(metadata, body):
    return ("---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False) + "---\n\n" + body).encode("utf-8")


def write_bundle(root, bundle):
    root, _ = _sandbox(root)
    source_id = bundle["source_id"]
    if source_id != "src_" + _sha(bundle["source_bytes"]):
        raise ValueError("source_bytes_identity_mismatch")
    source = json.loads(bundle["source_bytes"])
    card = ["# 报告证据卡（合成隔离）", "", "> 只证明报告内有此元数据，不证明原始消息、现实任务或用户偏好。", "",
            f"元数据摘录：[[Sources/{source_id}.json]]", ""]
    for i, record in enumerate(source["records"]):
        card.extend([f"## 记录 {i}", "", f"平台：{record['platform']}；原始报告位置：`{record['report_pointer']}`。",
                     f"来源记录时间：{record['source_recorded_at'] or '未知'}；原始消息引用未验证。 ^record-{i:06d}", ""])
    planned = {f"Sources/{source_id}.json": bundle["source_bytes"],
        f"Sources/{source_id}.md": _note(dict(record_type="source", source_id=source_id, kind="aux",
            sha256=_sha(bundle["source_bytes"]), source_scope="sanitized_report_excerpt", review_state="draft"), "\n".join(card)),
        "identity-map-" + source_id[4:] + ".json": canonical(bundle["mappings"]) + b"\n"}
    for item in bundle["candidates"]:
        if not _id(item["activity_id"], r"act_[0-9a-f]{64}") or item["review_state"] != "draft":
            raise ValueError("invalid_candidate")
        ref = item["evidence_refs"][0]
        body = f"""# Agent 活动观察 · {item['platform']}

审核状态：**draft**；活动状态：**unknown**；仅供人工审核，不是正式 Memory。

- 现实活动时间：未知（event_at 为空）
- 对话时间：未知（conversation_at 为空）
- 来源记录时间：{item['source_recorded_at'] or '未知'}
- 本批观察时间：{item['observed_at']}

[[{ref['card_link']}|查看报告级来源证据]]

报告内引用有效；原始历史引用状态：`{ref['original_reference_validity']}`。
缺少消息正文和用户/外部证据，不推断任务完成、用户偏好或知识结论。
身份 provisional/unresolved；原 ID 保留在 adrian_ids，映射不等于身份替换。
"""
        planned[f"Candidates/{item['activity_id']}.md"] = _note(item, body)
    statuses, written = {}, 0
    # 先检查所有路径，再写入；任何已有不同笔记均保护，其他证据冲突则拒绝。
    for relative, data in planned.items():
        path = root / "vault" / relative
        for parent in (path, *path.parents):
            if parent == root:
                break
            if os.path.lexists(parent) and is_link(parent):
                raise ValueError("linked_output_refused")
        if path.exists():
            existing = path.read_bytes()
            if existing == data:
                statuses[relative] = "unchanged"
            elif relative.startswith("Candidates/"):
                statuses[relative] = "protected_existing_note"
            else:
                raise ValueError("existing_evidence_conflict")
    for relative, data in planned.items():
        if relative in statuses:
            continue
        path = root / "vault" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as f:
            f.write(data)
        statuses[relative] = "created"
        written += 1
    return dict(sandbox=str(root), written=written, candidate_ids=[c["activity_id"] for c in bundle["candidates"]],
                file_statuses=statuses, skipped=bundle["skipped"])


def demo(root=None):
    root, metadata = _sandbox(create_sandbox() if root is None else root)
    inputs = root / "synthetic-inputs"
    if os.path.lexists(inputs) and is_link(inputs):
        raise ValueError("linked_input_refused")
    inputs.mkdir(exist_ok=True)
    fixtures = {
        "codex.jsonl": [dict(type="session_meta", ordinal=0, payload={"id": "synthetic-adapter-codex"}),
            dict(type="event_msg", ordinal=1, timestamp="2026-01-02T03:04:05Z", payload={"type": "agent_message", "message": "synthetic only"})],
        "dsh.jsonl": [dict(type="session", version=4, id="synthetic-adapter-dsh"),
            dict(type="assistant/message", seq=1, time="2026-01-02T04:05:06Z", data={"text": "synthetic only"})]}
    if any(p.name not in fixtures or is_link(p) for p in inputs.iterdir()):
        raise ValueError("unexpected_synthetic_input")
    for name, rows in fixtures.items():
        path = inputs / name
        data = b"".join(canonical(row) + b"\n" for row in rows)
        if os.path.lexists(path):
            if is_link(path) or path.read_bytes() != data:
                raise ValueError("synthetic_input_changed")
        else:
            with path.open("xb") as f:
                f.write(data)
    report = scan(inputs)
    # 实际读取现有只读报告序列化结果，不读取任何真实历史。
    report_path = root / "synthetic-report.json"
    report_bytes = canonical(report)
    if os.path.lexists(report_path):
        if is_link(report_path) or report_path.read_bytes() != report_bytes:
            raise ValueError("synthetic_report_changed")
    else:
        with report_path.open("xb") as f:
            f.write(report_bytes)
    bundle = convert_report(json.loads(report_path.read_bytes()), observed_at=metadata["observed_at"])
    return write_bundle(root, bundle)


def main(argv=None):
    parser = argparse.ArgumentParser(description="仅合成 Adapter 演示；无私人历史或正式 Vault 输入参数")
    parser.add_argument("--sandbox", type=Path, help="仅复用本命令生成的临时沙盒，重复运行验证幂等")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(demo(args.sandbox), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError):
        print('{"error":"invalid_synthetic_sandbox_or_input"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
