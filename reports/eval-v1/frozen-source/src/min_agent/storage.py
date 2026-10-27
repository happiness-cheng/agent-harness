"""显式保存/加载会话快照；业务服务的自动接入另行实施。"""

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, model_validator

from min_agent.sessions import ExecutionContext, SessionSnapshot, SessionStore
from min_agent.budget import check_budget_successor
from min_agent.tools.todo import TodoItem


_APPLICATION_ID = 0x41485231
_SCHEMA_VERSION = 1
_SCHEMA = {
    "sessions": """CREATE TABLE sessions (
        session_id TEXT PRIMARY KEY NOT NULL,
        owner_id TEXT NOT NULL,
        snapshot_json TEXT NOT NULL
    )""",
    "todos": """CREATE TABLE todos (
        session_id TEXT NOT NULL,
        todo_id TEXT NOT NULL,
        position INTEGER NOT NULL CHECK (position >= 0),
        item_json TEXT NOT NULL,
        PRIMARY KEY (session_id, todo_id),
        UNIQUE (session_id, position),
        FOREIGN KEY (session_id) REFERENCES sessions(session_id)
    )""",
}


class StoredSession(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    snapshot: SessionSnapshot
    todos: list[TodoItem]

    @model_validator(mode="after")
    def check_consistency(self) -> Self:
        ids = [item.id for item in self.todos]
        if len(set(ids)) != len(ids):
            raise ValueError("todo ID不能重复")
        if any(item.session_id != self.snapshot.session_id for item in self.todos):
            raise ValueError("todo不属于快照会话")
        # 复用既有会话协议检查，避免落盘或恢复时放宽工具配对约束。
        pending = SessionStore._pending(self.snapshot.messages)
        if pending and (self.snapshot.task is None or self.snapshot.task.status == "completed"):
            raise ValueError("未配对工具请求不能作为已完成会话保存")
        return self


def _checked_context(context: ExecutionContext) -> ExecutionContext:
    if not isinstance(context, ExecutionContext):
        raise TypeError("context必须是ExecutionContext")
    return ExecutionContext.model_validate(context.model_dump(warnings=False))


def _check_budget_write(previous, candidate, expected):
    old = previous.budgets if previous is not None else {}
    active = previous.active_budget_id if previous is not None else None
    if expected is None:
        if old != candidate.budgets or active != candidate.active_budget_id:
            raise ValueError("普通快照保存不能更改预算")
        return
    if previous is None:
        raise ValueError("预算必须属于已有会话")
    budget_id, revision = expected
    if not isinstance(budget_id, UUID) or (revision is not None and (type(revision) is not int or revision < 0)):
        raise ValueError("预算预期版本无效")
    before, after = old.get(budget_id), candidate.budgets.get(budget_id)
    if after is None or (before is None) != (revision is None):
        raise ValueError("预算创建或更新版本冲突")
    if before is None:
        if after.revision != 0:
            raise ValueError("初始预算版本无效")
    else:
        # 即使候选值恰好相同，也必须拒绝旧实例重复消费。
        if before.revision != revision:
            raise ValueError("预算版本已过期")
        check_budget_successor(before, after)
    if ({key: value for key, value in old.items() if key != budget_id}
            != {key: value for key, value in candidate.budgets.items() if key != budget_id}):
        raise ValueError("不能更改其他任务预算")
    if candidate.active_budget_id not in {active, budget_id}:
        raise ValueError("活动预算切换无效")


class SQLiteStorage:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._lock = RLock()
        self._closed = False

    @classmethod
    def _connect(cls, path: Path) -> Self:
        connection = sqlite3.connect(
            path.as_uri() + "?mode=rw", uri=True, autocommit=True, check_same_thread=False,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            return cls(connection)
        except BaseException:
            connection.close()
            raise

    @classmethod
    def create_new(cls, path: str | Path) -> Self:
        target = Path(path).resolve()
        # 排他创建，已有文件（包括空文件）也不覆盖；失败不擅自删除残留文件。
        with target.open("xb"):
            pass
        storage = cls._connect(target)
        try:
            with storage._transaction(write=True) as connection:
                for statement in _SCHEMA.values():
                    connection.execute(statement)
                connection.execute("PRAGMA application_id = 0x41485231")
                connection.execute("PRAGMA user_version = 1")
            return storage
        except BaseException:
            storage.close()
            raise

    @classmethod
    def open(cls, path: str | Path) -> Self:
        storage = cls._connect(Path(path).resolve())
        try:
            with storage._transaction() as connection:
                if (
                    connection.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID
                    or connection.execute("PRAGMA user_version").fetchone()[0] != _SCHEMA_VERSION
                ):
                    raise ValueError("数据库格式或版本不受支持")
                normalize = lambda sql: " ".join(sql.split()).lower().rstrip(";")
                for name, expected in _SCHEMA.items():
                    row = connection.execute(
                        "SELECT sql FROM sqlite_schema WHERE type = 'table' AND name = ?", (name,),
                    ).fetchone()
                    if row is None or normalize(row[0]) != normalize(expected):
                        raise ValueError("数据库表结构不受支持")
            return storage
        except BaseException:
            storage.close()
            raise

    @contextmanager
    def _transaction(self, *, write: bool = False):
        with self._lock:
            if self._closed:
                raise RuntimeError("存储连接已关闭")
            connection = self._connection
            nested = connection.in_transaction
            savepoint = 'memory_' + uuid4().hex if nested else None
            connection.execute('SAVEPOINT '+savepoint if nested else "BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield connection
                connection.execute('RELEASE SAVEPOINT '+savepoint if nested else "COMMIT")
            except BaseException:
                if nested and connection.in_transaction:
                    connection.execute('ROLLBACK TO SAVEPOINT '+savepoint)
                    connection.execute('RELEASE SAVEPOINT '+savepoint)
                elif connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def save_many(self, bundles):
        """无schema迁移的多快照原子提交；复用每份快照原有的归属与预算校验。"""
        with self._transaction(write=True):
            for context,snapshot,todos in bundles:
                self.save(context,snapshot,todos)

    @staticmethod
    def _read(connection: sqlite3.Connection, row: sqlite3.Row) -> StoredSession:
        snapshot = SessionSnapshot.model_validate_json(row["snapshot_json"])
        if str(snapshot.session_id) != row["session_id"] or str(snapshot.owner_id) != row["owner_id"]:
            raise ValueError("快照归属与数据库索引不一致")
        rows = connection.execute(
            "SELECT todo_id, position, item_json FROM todos WHERE session_id = ? ORDER BY position",
            (row["session_id"],),
        ).fetchall()
        items = []
        for position, item_row in enumerate(rows):
            item = TodoItem.model_validate_json(item_row["item_json"])
            if str(item.id) != item_row["todo_id"] or item_row["position"] != position:
                raise ValueError("todo标识或顺序与数据库索引不一致")
            items.append(item)
        return StoredSession(snapshot=snapshot, todos=items)

    def save(self, context: ExecutionContext, snapshot: SessionSnapshot, todos: list[TodoItem], *, budget_expected=None) -> None:
        checked = _checked_context(context)
        session_id, owner_id = str(checked.session_id), str(checked.owner_id)
        with self._transaction(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,),
            ).fetchone()
            if row is not None and row["owner_id"] != owner_id:
                raise PermissionError("会话不存在或不属于当前用户")
            if not isinstance(snapshot, SessionSnapshot) or not isinstance(todos, list):
                raise TypeError("必须提供SessionSnapshot和todo列表")
            if any(not isinstance(item, TodoItem) for item in todos):
                raise TypeError("todo列表必须由TodoItem组成")
            bundle = StoredSession(
                snapshot=SessionSnapshot.model_validate(snapshot.model_dump(warnings=False)),
                todos=[TodoItem.model_validate(item.model_dump(warnings=False)) for item in todos],
            )
            if bundle.snapshot.session_id != checked.session_id or bundle.snapshot.owner_id != checked.owner_id:
                raise PermissionError("快照不属于当前会话")
            previous = self._read(connection, row) if row is not None else None
            _check_budget_write(previous.snapshot if previous is not None else None, bundle.snapshot, budget_expected)
            if previous is not None:
                previous_ids = [item.id for item in previous.todos]
                if [item.id for item in bundle.todos[:len(previous_ids)]] != previous_ids:
                    raise ValueError("必须完整保留已有todo及顺序")
                for old, new in zip(previous.todos, bundle.todos):
                    if old.deleted and old != new:
                        raise ValueError("不能恢复或修改已删除todo")
                    if old.status == "completed" and new.status != "completed":
                        raise ValueError("不能重新打开已完成todo")
            todo_rows = [
                (session_id, str(item.id), position, item.model_dump_json())
                for position, item in enumerate(bundle.todos)
            ]
            connection.execute(
                "INSERT INTO sessions (session_id, owner_id, snapshot_json) VALUES (?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET snapshot_json = excluded.snapshot_json",
                (session_id, owner_id, bundle.snapshot.model_dump_json()),
            )
            connection.executemany(
                "INSERT INTO todos (session_id, todo_id, position, item_json) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(session_id, todo_id) DO UPDATE SET item_json = excluded.item_json",
                todo_rows,
            )

    def load(self, context: ExecutionContext) -> StoredSession:
        checked = _checked_context(context)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_id = ? AND owner_id = ?",
                (str(checked.session_id), str(checked.owner_id)),
            ).fetchone()
            if row is None:
                raise PermissionError("会话不存在或不属于当前用户")
            return self._read(connection, row)

    def load_all(self) -> list[StoredSession]:
        """可信应用启动恢复入口；不向模型或普通业务请求暴露。"""
        with self._transaction() as connection:
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError("数据库存在外键不一致")
            rows = connection.execute("SELECT * FROM sessions ORDER BY session_id").fetchall()
            return [self._read(connection, row) for row in rows]

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._connection.close()
                self._closed = True

    def __enter__(self) -> Self:
        with self._lock:
            if self._closed:
                raise RuntimeError("存储连接已关闭")
            return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
