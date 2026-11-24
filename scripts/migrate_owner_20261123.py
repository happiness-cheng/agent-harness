"""把本地 SQLite 的 owner_id 从旧路径迁移到新路径。

背景：HarnessApplication 的本地 owner_id 由数据库绝对路径派生；数据库
从 ``<repo>/data`` 移到 ``%LOCALAPPDATA%/agent-harness/data`` 后，新进程
会认为所有旧会话属于“另一个 owner”，表现为会话列表为空。

默认只更新“旧路径派生 owner”对应的会话；``--all-owners`` 会把同一库内
其他 owner 也并到新 owner（会合并本机不同 owner 的会话，需显式选择）。
每个库更新前复制一份 ``.bak-owner-<timestamp>``，更新后跑 integrity_check。

用法：
    python scripts/migrate_owner_20261123.py --dry-run
    python scripts/migrate_owner_20261123.py
    python scripts/migrate_owner_20261123.py --all-owners
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from min_agent.session.paths import data_dir, owner_id_for_database

DATABASES = (
    "workspace.sqlite3",
    "adrian-qianwen.sqlite3",
    "interview-demo.sqlite3",
    "ollama-test.sqlite3",
    "ui-preview-20261020.sqlite3",
    "agent-harness.sqlite3",
)


def migrate(database: Path, old_database: Path, *, all_owners: bool, dry_run: bool) -> dict:
    if not database.is_file():
        return {"database": str(database), "status": "missing"}
    old_owner = str(owner_id_for_database(old_database))
    new_owner = str(owner_id_for_database(database))
    connection = sqlite3.connect(database)
    try:
        before = connection.execute(
            "SELECT owner_id, COUNT(*) FROM sessions GROUP BY owner_id ORDER BY owner_id"
        ).fetchall()
        rows = connection.execute(
            "SELECT session_id, owner_id, snapshot_json FROM sessions"
        ).fetchall()
        updates: list[tuple[str, str, str]] = []
        for session_id, row_owner, raw in rows:
            snapshot = json.loads(raw)
            def contains_old_owner(value) -> bool:
                if isinstance(value, dict):
                    for key, item in value.items():
                        if key == "owner_id" and item == old_owner:
                            return True
                        if contains_old_owner(item):
                            return True
                elif isinstance(value, list):
                    return any(contains_old_owner(item) for item in value)
                return False

            if all_owners:
                selected = True
            else:
                selected = row_owner == old_owner or contains_old_owner(snapshot)
            if not selected:
                continue

            def replace_owner(value):
                if isinstance(value, dict):
                    for key, item in list(value.items()):
                        if key == "owner_id" and isinstance(item, str) \
                                and (all_owners or item == old_owner):
                            value[key] = new_owner
                        else:
                            replace_owner(item)
                elif isinstance(value, list):
                    for item in value:
                        replace_owner(item)

            snapshot["owner_id"] = new_owner
            replace_owner(snapshot)
            snapshot["owner_id"] = new_owner
            snapshot["revision"] = int(snapshot.get("revision", 0)) + 1
            updates.append((new_owner, json.dumps(snapshot, ensure_ascii=False), session_id))

        result = {
            "database": str(database),
            "old_owner": old_owner,
            "new_owner": new_owner,
            "before": [{"owner_id": owner, "sessions": count} for owner, count in before],
            "updated": len(updates),
            "all_owners": all_owners,
        }
        if dry_run or not updates:
            result["status"] = "dry_run" if dry_run else "no_match"
            return result
        backup = database.with_name(
            database.name + ".bak-owner-" + datetime.now().strftime("%Y%m%d%H%M%S"))
        shutil.copy2(database, backup)
        with connection:
            connection.executemany(
                "UPDATE sessions SET owner_id=?, snapshot_json=? WHERE session_id=?",
                updates)
        result["integrity"] = connection.execute("PRAGMA integrity_check").fetchone()[0]
        result["backup"] = str(backup)
        result["status"] = "migrated"
        return result
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-root", type=Path, default=Path(__file__).resolve().parents[1],
                        help="旧数据库所在项目根（默认仓库根）")
    parser.add_argument("--data-dir", type=Path, default=data_dir(),
                        help="新数据目录（默认用户数据目录）")
    parser.add_argument("--all-owners", action="store_true",
                        help="把库内所有 owner 合并到新 owner；会合并本机不同 owner 的会话")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.all_owners and not args.dry_run:
        print("WARNING: --all-owners 会合并本机不同 owner 的会话；已要求显式选择。",
              flush=True)

    results = []
    for name in DATABASES:
        results.append(migrate(
            args.data_dir / name,
            args.old_root / "data" / name,
            all_owners=args.all_owners,
            dry_run=args.dry_run,
        ))
    print(json.dumps({"data_dir": str(args.data_dir), "results": results},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())