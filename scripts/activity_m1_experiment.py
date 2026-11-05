"""M1 合成实验；只创建自动清理的独立临时库，可选保存公开 JSON 结果。"""

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
from contextlib import closing
import subprocess
import tempfile

from min_agent.activity_store import IntegrityError, M1Store, StateConflict


def run():
    with tempfile.TemporaryDirectory(prefix="adrian-m1-") as temp:
        root = Path(temp)
        store = M1Store.create(
            root / "original.db", collector_namespace="synthetic-m1", manifest_epoch="fixture-1",
            platform="synthetic", object_namespace="thread", logical_id="M1-Example",
            object_role="history", native_branch_key="root", root_evidence="native_branch_key",
            first_content=b"first\n",
        )
        initial = store.summary()
        cp = store.checkpoint()
        for content in (b"first\nsecond\n", b"first\nsecond\nthird\n"):
            store.append_snapshot(content)
            cp = store.advance_checkpoint(len(content), expected_revision=cp["revision"], expected_hash=cp["hash"])
        for state in ("provisional", "definitive"):
            store.append_state(initial["member_id"], "identity_status", state, evidence_ref="synthetic-evidence")
        after = store.summary()
        stable = all(initial[k] == after[k] for k in (
            "manifest_id", "manifest_revision", "manifest_hash", "object_id", "member_id", "generation_id", "checkpoint_id"))
        separate = initial["snapshot_chain_root_id"] == after["snapshot_chain_root_id"] != after["latest_snapshot_id"]
        state_ok = store.fold_state()[(initial["member_id"], "identity_status")] == "definitive"

        # 实际保存恢复 JSON，然后在全新数据库中重放持久层。
        bundle_path = root / "durable.json"
        bundle_path.write_text(json.dumps(store.export_bundle()), encoding="utf-8")
        restored = M1Store.rebuild(root / "rebuilt.db", json.loads(bundle_path.read_text(encoding="utf-8")))
        rebuilt = restored.summary()
        original_ids, rebuilt_ids = store.identity_inventory(), restored.identity_inventory()
        same = after == rebuilt and original_ids == rebuilt_ids and store.checkpoint() == restored.checkpoint() and store.fold_state() == restored.fold_state()
        corruption = []
        operations = {
            "revision": "UPDATE manifest_revisions SET body=json_set(body,'$.revision_no',99) WHERE seq=1",
            "hash": "UPDATE manifest_revisions SET hash='corrupt' WHERE seq=1",
            "parent": "UPDATE manifest_revisions SET body=json_set(body,'$.parent_manifest_hash','corrupt') WHERE seq=1",
            "manifest_binding": "UPDATE manifest_head SET manifest_id='other'",
        }
        for label, sql in operations.items():
            damaged = M1Store.rebuild(root / f"{label}.db", store.export_bundle())
            with closing(sqlite3.connect(damaged.path)) as conn, conn:
                conn.execute(sql)
            before_bytes = damaged.path.read_bytes()
            error = None
            try:
                damaged.append_snapshot(b"first\nsecond\nthird\nfourth\n")
            except IntegrityError as exc:
                error = str(exc)
            corruption.append({"case": label, "error": error, "no_write": damaged.path.read_bytes() == before_bytes})
        before_conflict = store.export_bundle()
        state_error = None
        try:
            store.append_state(initial["member_id"], "identity_status", "provisional", evidence_ref="synthetic-conflict")
        except StateConflict as exc:
            state_error = str(exc)
        state_ok = state_ok and state_error is not None and M1Store.open(store.path).export_bundle() == before_conflict
        cases = {"A_identity_stable": stable, "B_root_latest_separate": separate,
                 "C_fold_and_conflict": state_ok, "D_rebuild": same,
                 "E_corruption_rejected": all(c["error"] and c["no_write"] for c in corruption)}
        repo = Path(__file__).resolve().parents[1]
        sources = ["src/min_agent/activity_identity.py", "src/min_agent/activity_store.py", "scripts/activity_m1_experiment.py",
                   "tests/unit/test_activity_identity.py", "tests/unit/test_activity_store.py"]
        return {
            "scope": "offline synthetic M1; not Connector, M2/M3, production or model quality",
            "baseline": "v0.2.4.2-final (144814), confirmed by user 2026-11-05",
            "git_head_at_run": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(),
            "source_sha256": {p: hashlib.sha256((repo / p).read_bytes()).hexdigest() for p in sources},
            "cases": cases, "passed": all(cases.values()),
            "initial": initial, "before_rebuild": after, "after_rebuild": rebuilt,
            "identity_comparison": [{"before": a, "after": b, "equal": a == b} for a, b in zip(original_ids, rebuilt_ids, strict=True)],
            "checkpoint_after_rebuild": restored.checkpoint(),
            "state_conflict_error": state_error, "corruption": corruption,
            "known_limits": ["one object/member/initial generation", "uncompressed append only", "complete recovery bundle required",
                             "integer/string JCS subset", "hash chains are not signed anti-rollback protection"],
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="新报告路径；拒绝覆盖已有文件")
    args = parser.parse_args()
    if args.report is not None and args.report.exists():
        parser.error("报告路径已存在，使用新的文件名")
    report = run()
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report is not None:
        with args.report.open("x", encoding="utf-8") as handle:
            handle.write(output + "\n")
    print(output)
    raise SystemExit(0 if report["passed"] else 1)
