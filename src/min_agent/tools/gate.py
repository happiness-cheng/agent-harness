"""执行前分级门（sidecar）：规则快速失败 → 小模型审批 → 人工确认。

串行、fail-closed、带熔断。每次调用先由 ``effects.classify_call`` 判档位：
- low：放行；
- medium：小模型审批（不可用/解析失败/超时 → 升级人工；连续失败熔断）；
- high：人工确认（``GateConfirmations`` 排队，``AuthorizationRequired`` 停批）。

禁令名单（``generic._blocked_command``）只做快速失败，不当边界：解析失败、
凭据名、工作区外路径直接拒；破坏性命令（rm 族）不在此硬拒，而是判 high 走人工。
拒绝理由写回工具消息与 ``gate_audit`` 会话扩展（脱敏 + sha256），可审计。

明确不保证：小模型审批是减速带不是边界；分类是近似 shell 语义；真边界是
沙箱（S7）与人工确认。本门只在 generic 模式按策略挂载，默认专用模式不受影响。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Callable
from uuid import UUID, uuid4

from min_agent.session.task_state import AuthorizationRequired
from min_agent.tools.effects import classify_call
from min_agent.tools.generic import _blocked_command

DEFAULT_TTL_SECONDS = 15 * 60
MAX_PENDING = 20
MAX_RECORDS = 100
AUDIT_CAP = 200
COMMAND_AUDIT_LIMIT = 2048
BREAKER_THRESHOLD = 3


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
    expires = _moment(record.get("expires_at"))
    return expires is None or expires <= at


class GateConfirmations:
    """高危调用的排队协调器；与 MCPConfirmations 同形，存 ``gate_confirmations``。

    按「逻辑任务 + 工具名 + 参数签名」匹配；批准只对同一任务同签名有效，带 TTL，
    一次性消费。协调器只判状态，不执行工具，也不从模型消息推断批准。
    """

    def __init__(self, sessions, context, *, ttl_seconds: int = DEFAULT_TTL_SECONDS,
                 now: Callable[[], datetime] | None = None) -> None:
        if type(ttl_seconds) is not int or ttl_seconds <= 0:
            raise ValueError("确认 TTL 必须是正整数秒")
        self._sessions = sessions
        self._context = sessions.check_access(context)
        self._ttl = timedelta(seconds=ttl_seconds)
        self._now = now or (lambda: datetime.now(UTC))

    def _records(self) -> dict:
        return self._sessions.extension(self._context, "gate_confirmations", {})

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
                (r for r in records.values()
                 if r.get("status") in {"consumed", "refused", "expired"}),
                key=lambda r: str(r.get("created_at", "")))
            for record in finished[:len(records) - MAX_RECORDS]:
                records.pop(record["pending_id"], None)
                changed = True
        return records, changed

    def pending(self) -> list[dict]:
        at = self._now()
        return [r for r in self._records().values()
                if r.get("status") == "pending" and not _expired(r, at)]

    def request(self, *, tool: str, arguments: dict, level: str, reason: str,
                logical_task_id: str | None = None) -> dict:
        signature = _signature(tool, arguments)
        logical_task_id = logical_task_id or self._current_logical_task_id()
        if logical_task_id is None:
            raise PermissionError("门禁确认需要活动任务")
        at = self._now()
        with self._sessions.control(self._context):
            records, _ = self._prune(self._records(), at)
            for record in records.values():
                if (record.get("signature") == signature
                        and record.get("logical_task_id") == logical_task_id
                        and record.get("status") == "pending"
                        and not _expired(record, at)):
                    return dict(record)
            pending_count = sum(r.get("status") == "pending" for r in records.values())
            if pending_count >= MAX_PENDING:
                raise PermissionError("待确认的门禁调用过多；请先处理已有确认")
            record = {
                "pending_id": str(uuid4()), "tool": tool, "arguments": arguments,
                "signature": signature, "logical_task_id": logical_task_id,
                "level": level, "reason": reason, "status": "pending",
                "created_at": at.isoformat(),
                "expires_at": (at + self._ttl).isoformat(),
            }
            records[record["pending_id"]] = record
            records, _ = self._prune(records, at)
            self._sessions.set_extension(self._context, "gate_confirmations", records)
            return dict(record)

    def decide(self, pending_id, approve: bool) -> dict:
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
                self._sessions.set_extension(self._context, "gate_confirmations", records)
                raise PermissionError("该确认已过期，需要重新发起调用")
            record["status"] = "approved" if approve else "refused"
            record["decided_at"] = at.isoformat()
            self._sessions.set_extension(self._context, "gate_confirmations", records)
            return dict(record)

    def check_and_consume(self, tool: str, arguments: dict,
                          logical_task_id: str | None = None) -> str:
        signature = _signature(tool, arguments)
        logical_task_id = logical_task_id or self._current_logical_task_id()
        if logical_task_id is None:
            return "none"
        at = self._now()
        with self._sessions.control(self._context):
            records, changed = self._prune(self._records(), at)
            matches = [r for r in records.values()
                       if r.get("signature") == signature
                       and r.get("logical_task_id") == logical_task_id]
            if not matches:
                if changed:
                    self._sessions.set_extension(self._context, "gate_confirmations", records)
                return "none"
            record = max(
                (r for r in matches if r.get("status") == "approved"),
                key=lambda r: str(r.get("created_at", "")), default=None,
            ) or max(matches, key=lambda r: str(r.get("created_at", "")))
            if record.get("status") == "approved":
                record["status"] = "consumed"
                record["consumed_at"] = at.isoformat()
                self._sessions.set_extension(self._context, "gate_confirmations", records)
                return "approved"
            if changed:
                self._sessions.set_extension(self._context, "gate_confirmations", records)
            return str(record.get("status", "none"))


_REDACT_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|secret|password|credential)\s*[=:]\s*\S+"),
    re.compile(r"\b(sk|ghp|gho|xox[baprs]|AKIA)[A-Za-z0-9_\-]{8,}"),
)


def redact_command(command: str) -> str:
    """脱敏 + 截断：掩掉凭据样式赋值与长 token，截到 2KiB。审计用，不还原。"""
    text = command
    for pattern in _REDACT_PATTERNS:
        text = pattern.sub(lambda m: m.group(0).split("=")[0].split(":")[0] + "=***"
                           if ("=" in m.group(0) or ":" in m.group(0)) else "***", text)
    if len(text) > COMMAND_AUDIT_LIMIT:
        text = text[:COMMAND_AUDIT_LIMIT] + "...[truncated]"
    return text


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    level: str
    decider: str  # rule / small_model / user / preauth / off
    reason: str
    clear_destructive: bool = False


class ToolGate:
    """串行执行前分级门。check(call) 返回 GateDecision；高危待确认时抛
    AuthorizationRequired（由 ToolBatch 停批），审计在抛出前已写。"""

    def __init__(self, sessions, context, workspace_root, *, policy: str = "interactive",
                 approval_model=None, workspace=None, confirmations=None,
                 now: Callable[[], datetime] | None = None) -> None:
        if policy not in {"interactive", "unattended", "off"}:
            raise ValueError("gate_policy 只能是 interactive/unattended/off")
        self._sessions = sessions
        self._context = sessions.check_access(context)
        self._root = Path(workspace_root)
        self._policy = policy
        self._approval_model = approval_model
        self._workspace = workspace
        self._confirmations = confirmations or GateConfirmations(sessions, context, now=now)
        self._now = now or (lambda: datetime.now(UTC))
        self._model_failures = 0
        self._breaker_open = False

    @property
    def policy(self) -> str:
        return self._policy

    def pending(self) -> list[dict]:
        return self._confirmations.pending()

    def _audit(self, call, effect, decider: str, allowed: bool, reason: str) -> None:
        command = call.arguments.get("command") if call.name == "bash" else None
        record = {
            "ts": self._now().isoformat(),
            "tool": call.name,
            "call_ref": sha256(str(call.id).encode("utf-8")).hexdigest(),
            "level": effect.level,
            "decider": decider,
            "allowed": allowed,
            "reason": reason[:500],
            "args_sha256": sha256(
                json.dumps(call.arguments, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest(),
        }
        if command is not None:
            record["command_sha256"] = sha256(command.encode("utf-8")).hexdigest()
            record["command_redacted"] = redact_command(command)
        with self._sessions.control(self._context):
            audit = self._sessions.extension(self._context, "gate_audit", [])
            audit.append(record)
            self._sessions.set_extension(self._context, "gate_audit", audit[-AUDIT_CAP:])

    def _allow(self, call, effect, decider, reason, *, clear_destructive=False) -> GateDecision:
        self._audit(call, effect, decider, True, reason)
        if clear_destructive and self._workspace is not None and call.name == "bash":
            self._workspace.cleared_commands.add(call.arguments.get("command", ""))
        return GateDecision(True, effect.level, decider, reason, clear_destructive)

    def _deny(self, call, effect, decider, reason) -> GateDecision:
        self._audit(call, effect, decider, False, reason)
        return GateDecision(False, effect.level, decider, reason)

    def check(self, call) -> GateDecision:
        effect = classify_call(call.name, call.arguments, self._root)
        # 规则快速失败（保留 gate-v7）：解析失败/凭据/工作区外路径直接拒。
        # 破坏性命令不在此硬拒（allow_destructive=True），交给分级走人工。
        if call.name == "bash":
            rule = _blocked_command(call.arguments.get("command", ""), self._root,
                                    allow_destructive=True)
            if rule is not None:
                return self._deny(call, effect, "rule", rule)
        if self._policy == "off":
            return self._allow(call, effect, "off", "门禁关闭")
        if effect.level == "low":
            return self._allow(call, effect, "rule", "低风险只读，直接放行")
        if effect.level == "medium":
            return self._medium(call, effect)
        return self._high(call, effect)

    def _medium(self, call, effect) -> GateDecision:
        if self._policy == "unattended":
            return self._deny(call, effect, "rule",
                              "无人值守：中风险默认拒绝（需预授权，见 S8）")
        if self._breaker_open or self._approval_model is None:
            return self._high(call, effect, escalated=True)
        decision, reason = self._ask_model(call, effect)
        if decision == "allow":
            self._model_failures = 0
            return self._allow(call, effect, "small_model", reason or "小模型批准")
        if decision == "deny":
            self._model_failures = 0
            return self._deny(call, effect, "small_model", reason or "小模型拒绝")
        # escalate / 失败 → 升级人工。
        return self._high(call, effect, escalated=True)

    def _ask_model(self, call, effect) -> tuple[str, str]:
        """调小模型审批；返回 (decision, reason)。失败计入熔断。"""
        command = call.arguments.get("command") if call.name == "bash" else json.dumps(
            call.arguments, ensure_ascii=False)
        system = ("你是命令执行审批器。根据命令与效果档位判断是否放行。"
                  "只输出一行 JSON：{\"decision\":\"allow|deny|escalate\",\"reason\":\"简短理由\"}。"
                  "不确定就 escalate。不要输出别的。")
        user = (f"工具：{call.name}\n效果档位：{effect.level}（{effect.reason}）\n"
                f"工作区根：{self._root}\n内容：{command[:4000]}")
        from min_agent.contracts.models import Message
        try:
            response = self._approval_model.generate(
                [Message(role="system", content=system), Message(role="user", content=user)],
                tools=None)
            content = response.content or ""
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if match is None:
                raise ValueError("审批输出没有 JSON")
            parsed = json.loads(match.group(0))
            decision = str(parsed.get("decision", "")).lower()
            if decision not in {"allow", "deny", "escalate"}:
                raise ValueError("decision 无效")
            self._model_failures = 0
            return decision, str(parsed.get("reason", ""))[:300]
        except Exception as error:  # fail-closed：任何失败都升级人工
            self._model_failures += 1
            if self._model_failures >= BREAKER_THRESHOLD:
                self._breaker_open = True
            return "escalate", f"小模型审批失败（{type(error).__name__}），升级人工"

    def _high(self, call, effect, *, escalated: bool = False) -> GateDecision:
        if self._policy == "unattended":
            return self._deny(call, effect, "rule",
                              "无人值守：高风险默认拒绝（需预授权，见 S8）")
        try:
            status = self._confirmations.check_and_consume(call.name, call.arguments)
        except PermissionError as error:
            return self._deny(call, effect, "rule", str(error))
        if status == "approved":
            return self._allow(call, effect, "user",
                               "用户已确认高危调用", clear_destructive=effect.destructive)
        if status == "refused":
            return self._deny(call, effect, "user", "用户已拒绝该高危调用；不要重试同一调用")
        if status == "consumed":
            return self._deny(call, effect, "user", "该高危调用的批准已消费；不要重复执行")
        # none / pending → 登记待确认并停批。
        reason = ("中风险升级人工确认" if escalated else "高风险需用户确认") + f"：{effect.reason}"
        try:
            self._confirmations.request(tool=call.name, arguments=call.arguments,
                                        level=effect.level, reason=reason)
        except PermissionError as error:
            return self._deny(call, effect, "rule", str(error))
        self._audit(call, effect, "user", False, reason + "（等待用户确认）")
        raise AuthorizationRequired("waiting_user")