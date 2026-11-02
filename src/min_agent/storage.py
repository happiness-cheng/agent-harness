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
from min_agent.task_state import WorkStateTransition
from min_agent.tools.todo import TodoItem


_APPLICATION_ID = 0x41485231
_SCHEMA_VERSION = 2
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

_REVISION_TRIGGERS = {
    "session_revision_insert": """CREATE TRIGGER session_revision_insert BEFORE INSERT ON sessions
        WHEN json_type(NEW.snapshot_json, '$.revision') IS NOT 'integer'
             OR json_extract(NEW.snapshot_json, '$.revision') != 0
        BEGIN SELECT RAISE(ABORT, 'invalid initial session revision'); END""",
    "session_revision_update": """CREATE TRIGGER session_revision_update BEFORE UPDATE ON sessions
        WHEN json_type(NEW.snapshot_json, '$.revision') IS NOT 'integer'
             OR json_type(OLD.snapshot_json, '$.revision') IS NOT 'integer'
             OR json_extract(NEW.snapshot_json, '$.revision') != json_extract(OLD.snapshot_json, '$.revision') + 1
        BEGIN SELECT RAISE(ABORT, 'invalid session revision transition'); END""",
}


class VersionConflict(ValueError):
    """持久会话已发生变化；旧缓存不得继续用于授权或提交。"""


# 仅供可信应用内部的 Session 提交使用；不是不可信 Python 沙箱。
_SESSION_COMMIT = object()


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


def _check_confirmation_transition(previous, candidate):
    """确认只能针对当前提案，不能由一次快照替换连同旧提案一并复活。"""
    old = previous.work_state if previous is not None else None
    new = candidate.work_state
    history = list(previous.proposal_history) if previous is not None else []
    if len(history) != len(set(history)):
        raise ValueError("提案历史记录不一致")
    prior_id = str(old.proposal.proposal_id) if old and old.proposal else None
    next_id = str(new.proposal.proposal_id) if new.proposal else None
    if next_id != prior_id and next_id is not None:
        if next_id in history:
            raise VersionConflict("已替代的提案不能重新启用")
        history.append(next_id)
    candidate.proposal_history = history
    if old and old.proposal and new.proposal and old.proposal.proposal_id == new.proposal.proposal_id:
        if old.proposal != new.proposal:
            raise VersionConflict("不可更改现有提案的内容")
    if new.confirmed_goal_id is not None:
        if old is None or old.proposal is None or old.proposal != new.proposal:
            raise VersionConflict("确认必须针对持久化当前提案")


def _check_grant_shape(candidate):
    """每次提交都核对持久 grant 与当前 task/预算/提案的基本一致性。"""
    grant = candidate.work_state.grant
    if grant is None:
        return
    if grant.owner_id != candidate.owner_id or grant.session_id != candidate.session_id:
        raise VersionConflict("grant 不属于当前会话")
    work = candidate.work_state
    if (work.proposal is None or work.confirmed_goal_id is None
            or grant.proposal_id != work.proposal.proposal_id
            or work.confirmed_goal_id != work.proposal.proposal_id):
        raise VersionConflict("grant 与当前确认提案不一致")
    if grant.status == "pending_first_start":
        if candidate.task is not None and candidate.task.status != "completed":
            raise VersionConflict("pending grant 只能对应无任务或已完成任务")
        if (grant.logical_task_id, grant.attempt_id, grant.budget_id) != (None, None, None):
            raise VersionConflict("pending grant 不能携带绑定值")
    elif grant.status == "bound":
        task = candidate.task
        if (task is None or task.status != "running"
                or task.logical_task_id != grant.logical_task_id
                or task.task_id != grant.attempt_id):
            raise VersionConflict("bound grant 必须匹配当前 running attempt")
        if candidate.active_budget_id != grant.budget_id:
            raise VersionConflict("bound grant 预算必须精确匹配活动预算")
    else:
        if grant.logical_task_id is None or grant.attempt_id is None:
            raise VersionConflict("paused/closed grant 必须有逻辑任务与 attempt")
        if grant.budget_id is not None and grant.budget_id not in candidate.budgets:
            raise VersionConflict("grant 预算记录不存在")


def _transition_grant_matches(transition, grant):
    if (transition.grant_id, transition.logical_task_id, transition.attempt_id,
            transition.budget_id) != (grant.grant_id, grant.logical_task_id,
                                      grant.attempt_id, grant.budget_id):
        raise VersionConflict("授权转换字段与 grant 不一致")


def _require_no_grant_fields(transition):
    if (transition.grant_id, transition.logical_task_id, transition.attempt_id,
            transition.budget_id) != (None, None, None, None):
        raise VersionConflict("该授权转换不能携带 grant 字段")


def _check_work_state_transition(previous, candidate, transition, next_revision):
    """由 storage 对照 database previous 与 candidate 验证类型化转换。"""
    old, new = previous.work_state, candidate.work_state
    old_task, new_task = previous.task, candidate.task
    kind = transition.kind
    if kind == "propose":
        _require_no_grant_fields(transition)
        if new.proposal is None or (old.proposal is not None
                                    and new.proposal.proposal_id == old.proposal.proposal_id):
            raise VersionConflict("propose 必须产生新的目标提案")
        if new.confirmed_goal_id is not None or new.grant is not None:
            raise VersionConflict("propose 不能同时确认或携带 grant")
        if new_task != old_task:
            raise VersionConflict("propose 不能改变 task")
        return
    if kind == "select_mode":
        _require_no_grant_fields(transition)
        if (new.proposal != old.proposal or new.confirmed_goal_id != old.confirmed_goal_id
                or new.grant != old.grant):
            raise VersionConflict("select_mode 不能改变目标或授权")
        if new_task != old_task:
            raise VersionConflict("select_mode 不能改变 task")
        return
    if kind == "scope_waiting":
        _require_no_grant_fields(transition)
        if (new.proposal != old.proposal or new.confirmed_goal_id != old.confirmed_goal_id
                or new.grant != old.grant):
            raise VersionConflict("scope_waiting 不能改变目标或授权")
        if new_task != old_task:
            raise VersionConflict("scope_waiting 不能改变 task")
        if new.status != "waiting_user" or new.waiting_reason != "scope_not_confirmed":
            raise VersionConflict("scope_waiting 必须进入 scope 等待状态")
        return
    if kind == "confirm":
        grant = new.grant
        if grant is None or new.proposal is None:
            raise VersionConflict("confirm 必须携带 grant")
        if new.proposal != old.proposal or new.confirmed_goal_id != new.proposal.proposal_id:
            raise VersionConflict("confirm 必须针对持久化当前提案")
        if grant.issued_revision != next_revision:
            raise VersionConflict("grant.issued_revision 必须是本次提交版本")
        if old.grant is not None and grant.grant_id == old.grant.grant_id:
            raise VersionConflict("新确认必须生成新的 grant_id")
        _transition_grant_matches(transition, grant)
        if old_task is None:
            if new_task is not None:
                raise VersionConflict("confirm 不能凭空创建 task")
        elif new_task is None or (new_task.task_id, new_task.status) != (old_task.task_id, old_task.status):
            raise VersionConflict("confirm 不能改变 task 身份或状态")
        if old_task is not None and old_task.logical_task_id is not None \
                and new_task.logical_task_id != old_task.logical_task_id:
            raise VersionConflict("已有逻辑任务身份不能改写")
        if grant.status == "pending_first_start":
            if new_task != old_task or new_task is not None and new_task.status != "completed":
                raise VersionConflict("pending grant 只能对应原无任务或已完成任务")
        elif grant.status == "bound":
            if (new_task is None or new_task.status != "running"
                    or new_task.logical_task_id != grant.logical_task_id
                    or new_task.task_id != grant.attempt_id):
                raise VersionConflict("bound grant 必须匹配当前 running attempt")
            if candidate.active_budget_id != grant.budget_id:
                raise VersionConflict("bound grant 预算必须精确匹配活动预算")
        elif grant.status == "paused":
            if (new_task is None or new_task.status in {"running", "completed"}
                    or new_task.logical_task_id != grant.logical_task_id
                    or new_task.task_id != grant.attempt_id):
                raise VersionConflict("paused grant 必须匹配当前暂停 task")
            if candidate.active_budget_id != grant.budget_id:
                raise VersionConflict("paused grant 预算必须精确匹配活动预算")
        else:
            raise VersionConflict("confirm 不能产生 closed grant")
        return
    if kind == "bind_first_start":
        old_grant, grant = old.grant, new.grant
        if old_grant is None or old_grant.status != "pending_first_start":
            raise VersionConflict("首次绑定需要 pending grant")
        if (grant is None or (grant.grant_id, grant.proposal_id, grant.issued_revision)
                != (old_grant.grant_id, old_grant.proposal_id, old_grant.issued_revision)
                or grant.status != "bound"):
            raise VersionConflict("首次绑定必须保留 grant 身份并转为 bound")
        if (new.proposal, new.confirmed_goal_id) != (old.proposal, old.confirmed_goal_id):
            raise VersionConflict("首次绑定不能改变目标")
        if new_task is None or new_task.status != "running":
            raise VersionConflict("首次绑定必须有 running task")
        if old_task is not None and old_task.status != "completed":
            raise VersionConflict("首次绑定只能从无任务或已完成任务开始")
        if (new_task.logical_task_id != grant.logical_task_id
                or new_task.task_id != grant.attempt_id):
            raise VersionConflict("首次绑定 attempt/logical 不一致")
        if grant.budget_id != candidate.active_budget_id:
            raise VersionConflict("首次绑定预算必须精确匹配活动预算")
        _transition_grant_matches(transition, grant)
        return
    if kind == "resume":
        old_grant, grant = old.grant, new.grant
        if old_grant is None or old_grant.status != "paused":
            raise VersionConflict("resume 需要 paused grant")
        if (grant is None or (grant.grant_id, grant.proposal_id, grant.issued_revision)
                != (old_grant.grant_id, old_grant.proposal_id, old_grant.issued_revision)
                or grant.status != "bound"):
            raise VersionConflict("resume 必须保留 grant 身份并转为 bound")
        if (new.proposal, new.confirmed_goal_id) != (old.proposal, old.confirmed_goal_id):
            raise VersionConflict("resume 不能改变目标")
        if (old_task is None or old_task.status in {"running", "completed"}
                or new_task is None or new_task.status != "running"):
            raise VersionConflict("resume 必须从暂停 task 到新 running attempt")
        if (grant.logical_task_id != old_grant.logical_task_id
                or grant.budget_id != old_grant.budget_id
                or grant.logical_task_id != new_task.logical_task_id
                or grant.attempt_id != new_task.task_id):
            raise VersionConflict("resume 不能改变 logical/budget，且必须产生新 attempt")
        if candidate.active_budget_id != grant.budget_id:
            raise VersionConflict("resume 预算必须精确匹配活动预算")
        _transition_grant_matches(transition, grant)
        return
    if kind == "finish":
        old_grant, grant = old.grant, new.grant
        if old_task is None or old_task.status != "running":
            raise VersionConflict("finish 必须结束 running task")
        if (new_task is None or new_task.task_id != old_task.task_id
                or new_task.logical_task_id != old_task.logical_task_id
                or new_task.status not in {"completed", "waiting_user", "partial", "failed", "cancelled"}):
            raise VersionConflict("finish 不能改变 task 身份或 logical")
        if old_grant is None:
            raise VersionConflict("无 grant 的 finish 不应携带 finish 转换")
        if (old_grant.status != "bound" or old_grant.logical_task_id != old_task.logical_task_id
                or old_grant.attempt_id != old_task.task_id
                or old_grant.budget_id != candidate.active_budget_id):
            raise VersionConflict("finish 只能关闭属于当前 attempt 的 bound grant")
        expected = "closed" if new_task.status == "completed" else "paused"
        if (grant is None or (grant.grant_id, grant.proposal_id, grant.issued_revision,
                              grant.logical_task_id, grant.attempt_id, grant.budget_id)
                != (old_grant.grant_id, old_grant.proposal_id, old_grant.issued_revision,
                    old_grant.logical_task_id, old_grant.attempt_id, old_grant.budget_id)
                or grant.status != expected):
            raise VersionConflict("finish 只能把同一 bound grant 转为 paused/closed")
        _transition_grant_matches(transition, grant)
        return
    raise VersionConflict("未知的授权转换类型")


class SQLiteStorage:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._lock = RLock()
        self._closed = False
        self._after_commit = []

    def has_open_transaction(self) -> bool:
        """供 SessionStore 拒绝可能提前发布缓存的外层事务。"""
        with self._lock:
            if self._closed:
                raise RuntimeError("存储连接已关闭")
            return self._connection.in_transaction

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
                for statement in _REVISION_TRIGGERS.values():
                    connection.execute(statement)
                connection.execute("PRAGMA application_id = 0x41485231")
                connection.execute("PRAGMA user_version = 2")
            return storage
        except BaseException:
            storage.close()
            raise

    @classmethod
    def open(cls, path: str | Path) -> Self:
        storage = cls._connect(Path(path).resolve())
        try:
            with storage._transaction(write=True) as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if (connection.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID
                        or version not in {1, _SCHEMA_VERSION}):
                    raise ValueError("数据库格式或版本不受支持")
                normalize = lambda sql: " ".join(sql.split()).lower().rstrip(";")
                for name, expected in _SCHEMA.items():
                    row = connection.execute(
                        "SELECT sql FROM sqlite_schema WHERE type = 'table' AND name = ?", (name,),
                    ).fetchone()
                    if row is None or normalize(row[0]) != normalize(expected):
                        raise ValueError("数据库表结构不受支持")
                if version == 1:
                    # 单事务迁移；旧快照不允许自带看似可信的版本。
                    rows = connection.execute("SELECT session_id, snapshot_json FROM sessions").fetchall()
                    for row in rows:
                        import json
                        raw = json.loads(row['snapshot_json'])
                        if 'revision' in raw or 'proposal_history' in raw:
                            raise ValueError("旧版快照不能含新版本字段")
                        raw['revision'] = 0
                        legacy = SessionSnapshot.model_validate_json(json.dumps(raw, ensure_ascii=False))
                        if legacy.work_state.proposal is not None:
                            raw['proposal_history'] = [str(legacy.work_state.proposal.proposal_id)]
                        connection.execute("UPDATE sessions SET snapshot_json = ? WHERE session_id = ?",
                                           (json.dumps(raw, ensure_ascii=False), row['session_id']))
                    for statement in _REVISION_TRIGGERS.values():
                        connection.execute(statement)
                    connection.execute("PRAGMA user_version = 2")
                for name, expected in _REVISION_TRIGGERS.items():
                    row = connection.execute("SELECT sql FROM sqlite_schema WHERE type = 'trigger' AND name = ?", (name,)).fetchone()
                    if row is None or normalize(row[0]) != normalize(expected):
                        raise ValueError("会话版本保护不完整")
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
            callback_start = len(self._after_commit)
            savepoint = 'memory_' + uuid4().hex if nested else None
            connection.execute('SAVEPOINT '+savepoint if nested else "BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield connection
                connection.execute('RELEASE SAVEPOINT '+savepoint if nested else "COMMIT")
            except BaseException:
                del self._after_commit[callback_start:]
                if nested and connection.in_transaction:
                    connection.execute('ROLLBACK TO SAVEPOINT '+savepoint)
                    connection.execute('RELEASE SAVEPOINT '+savepoint)
                elif connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            if not nested:
                callbacks, self._after_commit = self._after_commit, []
                for callback in callbacks:
                    callback()

    def save_many(self, bundles, *, _session_commit=None):
        """原子提交多快照；失败时不修改调用方持有的版本。"""
        originals = list(bundles)
        prepared = [(context, snapshot.model_copy(deep=True), todos) for context, snapshot, todos in originals]
        with self._transaction(write=True):
            for context,snapshot,todos in prepared:
                self.save(context,snapshot,todos,_session_commit=_session_commit)
            def publish_batch():
                for (_, original, _), (_, committed, _) in zip(originals, prepared):
                    original.revision = committed.revision
                    original.proposal_history = list(committed.proposal_history)
            self._after_commit.append(publish_batch)

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

    def save(self, context: ExecutionContext, snapshot: SessionSnapshot, todos: list[TodoItem], *, budget_expected=None,
             _work_state_transition=None, _session_commit=None) -> None:
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
            if previous is not None and _session_commit is not _SESSION_COMMIT:
                raise PermissionError("已有会话只能通过受信 SessionStore 更新")
            if previous is not None and previous.snapshot.revision != bundle.snapshot.revision:
                raise VersionConflict("会话持久版本已变化")
            if previous is None and bundle.snapshot.revision != 0:
                raise VersionConflict("新会话必须从版本零开始")
            next_revision = 0 if previous is None else previous.snapshot.revision + 1
            if previous is not None and previous.snapshot.work_state != bundle.snapshot.work_state:
                if not isinstance(_work_state_transition, WorkStateTransition):
                    raise PermissionError("普通会话保存不能修改授权状态")
                _check_work_state_transition(
                    previous.snapshot, bundle.snapshot, _work_state_transition, next_revision)
            elif _work_state_transition is not None and not isinstance(_work_state_transition, WorkStateTransition):
                raise PermissionError("授权转换类型无效")
            _check_grant_shape(bundle.snapshot)
            _check_confirmation_transition(previous.snapshot if previous is not None else None, bundle.snapshot)
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
            next_revision = 0 if previous is None else previous.snapshot.revision + 1
            bundle.snapshot.revision = next_revision
            if previous is None:
                connection.execute("INSERT INTO sessions (session_id, owner_id, snapshot_json) VALUES (?, ?, ?)",
                                   (session_id, owner_id, bundle.snapshot.model_dump_json()))
            else:
                updated = connection.execute(
                    "UPDATE sessions SET snapshot_json = ? WHERE session_id = ? AND owner_id = ? "
                    "AND json_extract(snapshot_json, '$.revision') = ?",
                    (bundle.snapshot.model_dump_json(), session_id, owner_id, previous.snapshot.revision))
                if updated.rowcount != 1:
                    raise VersionConflict("会话持久版本已变化")
            connection.executemany(
                "INSERT INTO todos (session_id, todo_id, position, item_json) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(session_id, todo_id) DO UPDATE SET item_json = excluded.item_json",
                todo_rows,
            )
            # 内层 savepoint 成功也不能提前发布，等待最外层事务提交。
            def publish():
                snapshot.revision = next_revision
                snapshot.proposal_history = list(bundle.snapshot.proposal_history)
            self._after_commit.append(publish)

    def load(self, context: ExecutionContext) -> StoredSession:
        result = self.load_optional(context)
        if result is None:
            raise PermissionError("会话不存在或不属于当前用户")
        return result

    def load_optional(self, context: ExecutionContext) -> StoredSession | None:
        """可信内部读取可缺省文档；读取失败必须传播，不能冒充不存在。"""
        checked = _checked_context(context)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_id = ? AND owner_id = ?",
                (str(checked.session_id), str(checked.owner_id)),
            ).fetchone()
            if row is None:
                return None
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
