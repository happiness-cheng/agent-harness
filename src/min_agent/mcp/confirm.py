"""MCP confirm 策略的排队协调器：任务绑定、TTL、定向决定、一次性消费。

记录存在会话扩展 ``mcp_confirmations`` 里（进程重启后仍在），按
「任务 + 工具名 + 参数规范化签名」匹配。批准只对**同一任务**内的同签名调用
有效，且带 TTL；过期、跨任务、已消费/已拒绝都不会被挪用或误挡。

协调器只做状态判定，不执行工具，也不从模型消息推断批准。
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Callable
from uuid import UUID, uuid4

DEFAULT_TTL_SECONDS = 15 * 60
MAX_PENDING = 20
MAX_RECORDS = 100


class ConfirmationBacklogError(RuntimeError):
    """待确认记录已达上限；本次调用不排队、不执行。"""


def _signature(tool: str, arguments: dict) -> str:
    return json.dumps({"tool": tool, "arguments": arguments},
                      sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":"))


def _moment(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _expired(record: dict, at: datetime) -> bool:
    """没有 expires_at 的旧记录按过期处理；有 TTL 的按时间比较。"""
    expires = _moment(record.get("expires_at"))
    return expires is None or expires <= at


class MCPConfirmations:
    def __init__(self, sessions, context, *, ttl_seconds: int = DEFAULT_TTL_SECONDS,
                 now: Callable[[], datetime] | None = None) -> None:
        if type(ttl_seconds) is not int or ttl_seconds <= 0:
            raise ValueError("确认 TTL 必须是正整数秒")
        self._sessions = sessions
        self._context = sessions.check_access(context)
        self._ttl = timedelta(seconds=ttl_seconds)
        self._now = now or (lambda: datetime.now(UTC))

    def _records(self) -> dict:
        return self._sessions.extension(self._context, "mcp_confirmations", {})

    def _current_logical_task_id(self) -> str | None:
        task = self._sessions.snapshot(self._context).task
        return str(task.logical_task_id or task.task_id) if task is not None else None

    def _prune(self, records: dict, at: datetime) -> tuple[dict, bool]:
        changed = False
        for key, record in list(records.items()):
            if record.get("status") in {"pending", "approved"} and _expired(record, at):
                del records[key]
                changed = True
        if len(records) > MAX_RECORDS:
            finished = sorted(
                (record for record in records.values()
                 if record.get("status") in {"consumed", "refused", "expired"}),
                key=lambda record: str(record.get("created_at", "")),
            )
            for record in finished[:len(records) - MAX_RECORDS]:
                records.pop(record["pending_id"], None)
                changed = True
        return records, changed

    def pending(self) -> list[dict]:
        at = self._now()
        return [record for record in self._records().values()
                if record.get("status") == "pending" and not _expired(record, at)]

    def request(self, *, server: str, tool: str, prefixed: str, arguments: dict,
                logical_task_id: str | None = None) -> dict:
        """登记一次待确认调用；同一逻辑任务同签名已有 pending 时复用。"""
        signature = _signature(prefixed, arguments)
        logical_task_id = logical_task_id or self._current_logical_task_id()
        if logical_task_id is None:
            raise PermissionError("MCP 确认需要活动任务")
        at = self._now()
        with self._sessions.control(self._context):
            records, _ = self._prune(self._records(), at)
            for record in records.values():
                if (record.get("signature") == signature
                        and record.get("logical_task_id") == logical_task_id
                        and record.get("status") == "pending"
                        and not _expired(record, at)):
                    return dict(record)
            pending_count = sum(record.get("status") == "pending"
                                for record in records.values())
            if pending_count >= MAX_PENDING:
                raise ConfirmationBacklogError("待确认的 MCP 调用过多；请先处理已有确认")
            record = {
                "pending_id": str(uuid4()), "server": server, "tool": tool,
                "prefixed": prefixed, "arguments": arguments, "signature": signature,
                "logical_task_id": logical_task_id, "status": "pending",
                "created_at": at.isoformat(),
                "expires_at": (at + self._ttl).isoformat(),
            }
            records[record["pending_id"]] = record
            records, _ = self._prune(records, at)
            self._sessions.set_extension(self._context, "mcp_confirmations", records)
            return dict(record)

    def decide(self, pending_id, approve: bool) -> dict:
        """只允许 web 定向入口调用；pending → approved/refused，过期不可决定。"""
        if not isinstance(pending_id, UUID):
            raise TypeError("pending_id必须是UUID")
        if type(approve) is not bool:
            raise TypeError("approve必须是布尔值")
        at = self._now()
        with self._sessions.control(self._context):
            records = self._records()
            record = records.get(str(pending_id))
            if record is None:
                raise PermissionError("确认记录不属于当前会话")
            if record.get("status") != "pending":
                raise PermissionError("该确认已处理，不能重复决定")
            if _expired(record, at):
                record["status"] = "expired"
                record["decided_at"] = at.isoformat()
                self._sessions.set_extension(self._context, "mcp_confirmations", records)
                raise PermissionError("该确认已过期，需要重新发起调用")
            record["status"] = "approved" if approve else "refused"
            record["decided_at"] = at.isoformat()
            self._sessions.set_extension(self._context, "mcp_confirmations", records)
            return dict(record)

    def check_and_consume(self, prefixed: str, arguments: dict,
                          logical_task_id: str | None = None) -> str:
        """执行入口判定：只消费同一逻辑任务、未过期的 approved。"""
        signature = _signature(prefixed, arguments)
        logical_task_id = logical_task_id or self._current_logical_task_id()
        if logical_task_id is None:
            return "none"
        at = self._now()
        with self._sessions.control(self._context):
            records, changed = self._prune(self._records(), at)
            matches = [record for record in records.values()
                       if record.get("signature") == signature
                       and record.get("logical_task_id") == logical_task_id]
            if not matches:
                if changed:
                    self._sessions.set_extension(self._context, "mcp_confirmations", records)
                return "none"
            record = max(
                (item for item in matches if item.get("status") == "approved"),
                key=lambda item: str(item.get("created_at", "")),
                default=None,
            ) or max(matches, key=lambda item: str(item.get("created_at", "")))
            if record.get("status") == "approved":
                record["status"] = "consumed"
                record["consumed_at"] = at.isoformat()
                self._sessions.set_extension(self._context, "mcp_confirmations", records)
                return "approved"
            if changed:
                self._sessions.set_extension(self._context, "mcp_confirmations", records)
            return str(record.get("status", "none"))