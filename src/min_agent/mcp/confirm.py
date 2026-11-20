"""MCP confirm 策略的排队协调器：持久登记、定向批准/拒绝、一次性消费。

记录存在会话扩展 ``mcp_confirmations`` 里（进程重启后仍在），按
「工具名 + 参数规范化签名」匹配；批准执行即 consumed，防重放。
协调器只做状态判定，不执行工具，也不从模型消息推断批准。
"""

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

MAX_RECORDS = 50


def _signature(tool: str, arguments: dict) -> str:
    return json.dumps({"tool": tool, "arguments": arguments},
                      sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":"))


class MCPConfirmations:
    def __init__(self, sessions, context) -> None:
        self._sessions = sessions
        self._context = sessions.check_access(context)

    def _records(self) -> dict:
        return self._sessions.extension(self._context, "mcp_confirmations", {})

    def pending(self) -> list[dict]:
        return [record for record in self._records().values()
                if record["status"] == "pending"]

    def request(self, *, server: str, tool: str, prefixed: str, arguments: dict) -> dict:
        """登记一次待确认调用；同签名已有 pending 时复用，不重复排队。"""
        signature = _signature(prefixed, arguments)
        with self._sessions.control(self._context):
            records = self._records()
            for record in records.values():
                if record["signature"] == signature and record["status"] == "pending":
                    return dict(record)
            record = {
                "pending_id": str(uuid4()), "server": server, "tool": tool,
                "prefixed": prefixed, "arguments": arguments, "signature": signature,
                "status": "pending",
                "created_at": datetime.now(UTC).isoformat(),
            }
            records[record["pending_id"]] = record
            if len(records) > MAX_RECORDS:
                removable = [key for key, item in records.items()
                             if item["status"] in {"consumed", "refused"}]
                for key in removable[:len(records) - MAX_RECORDS]:
                    del records[key]
            self._sessions.set_extension(self._context, "mcp_confirmations", records)
            return dict(record)

    def decide(self, pending_id, approve: bool) -> dict:
        """只允许 web 定向入口调用；pending → approved/refused，其余状态拒绝。"""
        if not isinstance(pending_id, UUID):
            raise TypeError("pending_id必须是UUID")
        if type(approve) is not bool:
            raise TypeError("approve必须是布尔值")
        with self._sessions.control(self._context):
            records = self._records()
            record = records.get(str(pending_id))
            if record is None:
                raise PermissionError("确认记录不属于当前会话")
            if record["status"] != "pending":
                raise PermissionError("该确认已处理，不能重复决定")
            record["status"] = "approved" if approve else "refused"
            record["decided_at"] = datetime.now(UTC).isoformat()
            self._sessions.set_extension(self._context, "mcp_confirmations", records)
            return dict(record)

    def check_and_consume(self, prefixed: str, arguments: dict) -> str:
        """执行入口判定：approved 原子消费；refused/pending/none 原样返回。"""
        signature = _signature(prefixed, arguments)
        with self._sessions.control(self._context):
            records = self._records()
            matches = [record for record in records.values()
                       if record["signature"] == signature]
            if not matches:
                return "none"
            record = matches[-1]
            if record["status"] == "approved":
                record["status"] = "consumed"
                record["consumed_at"] = datetime.now(UTC).isoformat()
                self._sessions.set_extension(self._context, "mcp_confirmations", records)
                return "approved"
            return record["status"]