"""保存本地工具绑定，并导出不含处理函数的模型可见元数据。"""

import json
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from min_agent.contracts.models import ToolCall
from min_agent.session.store import ExecutionContext, SessionStore
from min_agent.tools.schema import validate_arguments


class InvalidToolResultError(TypeError):
    """handler 已返回，但结果不符合当前文本契约。"""


class ToolRefusedError(Exception):
    """工具因用户/宿主原因未执行；reason 决定错误码与是否标记用户拒绝。"""

    def __init__(self, message: str, *, reason: str = "user_refused") -> None:
        super().__init__(message)
        self.reason = reason


class ToolExecutionFailedError(Exception):
    """工具已尝试执行但外部系统报错；结果必须是 ok:false，且不得伪装成拒绝。"""


_REFUSAL_ERRORS = {
    "user_refused": ("tool_request_failed", "do_not_retry_refused_call", True),
    "already_consumed": ("tool_already_consumed", "do_not_replay_completed_call", False),
    "confirmation_unavailable": ("confirmation_unavailable", "configure_confirmation_entry", False),
    "backlog_full": ("confirmation_backlog_full", "resolve_pending_confirmations", False),
}


def _request_error(code: str, message: str, **details: object) -> dict[str, object]:
    return {
        "ok": False,
        "error": {
            "code": code, "message": message,
            "execution_state": "not_started", "recovery": "correct_request_with_new_id",
            **details,
        },
    }


def flatten_outcome(outcome: dict[str, object]) -> dict[str, object]:
    """把双层 ok 的内部结果压成单层 {ok, error_code, message, data}。

    外层 ok=false（registry 级错误：unknown_tool/invalid_arguments/refused/gate_denied）
    直接取外层 error；外层 ok=true 时拆 handler 内层 JSON，**以内层业务 ok 为准**
    （这正是双层 ok 的痛点：工具执行了但业务失败，模型该看到失败）。
    """
    if not outcome.get("ok"):
        error = outcome.get("error") or {}
        error = error if isinstance(error, dict) else {}
        data = {k: v for k, v in error.items() if k not in ("code", "message")}
        return {"ok": False, "error_code": error.get("code", "tool_error"),
                "message": error.get("message", ""), "data": data}
    raw = outcome.get("result")
    inner = None
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            inner = parsed if isinstance(parsed, dict) else None
        except ValueError:
            inner = None
    if isinstance(inner, dict):
        if inner.get("ok"):
            data = {k: v for k, v in inner.items() if k != "ok"}
            return {"ok": True, "error_code": None, "message": "", "data": data}
        error = inner.get("error") or {}
        error = error if isinstance(error, dict) else {}
        data = {k: v for k, v in error.items() if k not in ("code", "message")}
        return {"ok": False, "error_code": error.get("code", "tool_error"),
                "message": error.get("message", ""), "data": data}
    return {"ok": True, "error_code": None, "message": "", "data": {"result": raw}}


@dataclass(frozen=True)
class ToolDefinition:
    """由可信应用代码创建的工具配置，字段赋值后不可直接替换。"""

    name: str
    description: str
    parameters: type[BaseModel]
    handler: Callable[..., object]
    managed_execution: bool = False
    raw_schema: dict[str, object] | None = None
    prompt_only: bool = False

    def __post_init__(self) -> None:
        if type(self.managed_execution) is not bool:
            raise TypeError("managed_execution 必须是bool")
        if type(self.prompt_only) is not bool:
            raise TypeError("prompt_only 必须是bool")
        if self.raw_schema is not None:
            if not isinstance(self.raw_schema, dict):
                raise TypeError("raw_schema 必须是字典")
            if self.raw_schema.get("type", "object") != "object":
                raise ValueError("raw_schema 顶层必须是对象")


class ToolRegistry:
    """保存工具；只有显式 execute 执行 handler。"""

    def __init__(self, *, sessions: SessionStore | None = None, context: ExecutionContext | None = None,
                 envelope: str = "nested") -> None:
        if (sessions is None) != (context is None):
            raise ValueError("会话绑定必须同时提供sessions和context")
        if envelope not in {"nested", "flat"}:
            raise ValueError("envelope 只能是 nested 或 flat")
        self._tools: dict[str, ToolDefinition] = {}
        self._sessions = sessions
        self._context = sessions.check_access(context) if sessions is not None else None
        self._long_memory_builder = None
        # GOV-001-S4：nested=双层 ok（默认，专用模式）；flat=单层
        # {ok,error_code,message,data}（generic 四工具）。只改序列化给模型的结果，
        # execute 内部仍返回 nested 形状，ToolBatch/progress_guard 逻辑不变。
        self.envelope = envelope

    def serialize(self, outcome: dict[str, object]) -> str:
        """把 execute 的内部结果序列化成给模型的工具消息。"""
        if self.envelope == "flat":
            return json.dumps(flatten_outcome(outcome), ensure_ascii=False, allow_nan=False)
        return json.dumps(outcome, ensure_ascii=False, allow_nan=False)

    @property
    def is_bound(self) -> bool:
        return self._context is not None

    def check_context(self, context: ExecutionContext | None, store: SessionStore | None = None) -> None:
        if self.is_bound and (context != self._context or store is not self._sessions):
            raise PermissionError("工具集不属于当前会话或存储")

    def work_state(self):
        if not self.is_bound:
            return None
        return self._sessions.work_state(self._context)

    def state_bar(self, messages, budget=None):
        if not self.is_bound:
            return None
        from min_agent.context.state_bar import StatusBuilder
        return StatusBuilder(self._sessions, self._context, budget).build(messages)

    def memory_view(self,messages):
        return self._long_memory_builder(messages) if self._long_memory_builder is not None else None

    def register(self, tool: ToolDefinition) -> None:
        if not isinstance(tool.name, str) or not tool.name.strip():
            raise ValueError("工具名称必须是非空白字符串")
        if tool.name in self._tools:
            raise ValueError(f"工具名称重复：{tool.name}")
        if not isinstance(tool.description, str) or not tool.description.strip():
            raise ValueError("工具描述必须是非空白字符串")
        if (
            not isinstance(tool.parameters, type)
            or not issubclass(tool.parameters, BaseModel)
            or tool.parameters is BaseModel
        ):
            raise TypeError("parameters 必须是具体的 Pydantic 模型类")
        if not callable(tool.handler):
            raise TypeError("handler 必须可调用")
        if tool.parameters.model_json_schema().get("type") != "object":
            raise ValueError("工具参数 Schema 顶层必须是对象")

        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition:
        return self._tools[name]

    def execute(self, call: ToolCall, *, operation_id=None) -> dict[str, object]:
        if operation_id is not None and self.is_bound and call.name.startswith(("todo_", "reminder_")) and call.name not in {"todo_list", "reminder_list"}:
            from min_agent.session.operations import execute_once
            # 只对实际取出且显式受审计的内置写定义发放本调用资格；
            # 未注册/自定义/替换 handler 一律没有资格。
            definition = self._tools.get(call.name)
            authority = (object() if definition is not None
                         and definition.managed_execution is True else None)
            return execute_once(self._sessions, self._context, call, operation_id, self._execute,
                                cleanup_authority=authority)
        return self._execute(call)

    def _execute(self, call: ToolCall) -> dict[str, object]:
        """请求错误可修正；执行异常传播，交由 Runtime 停止。"""
        try:
            tool = self.get(call.name)
        except KeyError:
            return _request_error(
                "unknown_tool", "工具未注册，请选择可用工具。",
                available_tools=list(self._tools),
            )
        if tool.raw_schema is not None:
            # MCP 参数模型是 extra="allow"，只保证「是对象」；这里补本地能判定的
            # required/简单类型，其余仍由服务器校验。
            issues = validate_arguments(tool.raw_schema, call.arguments)
            if issues:
                return _request_error(
                    "invalid_arguments", "参数校验失败，请按 Schema 修正。",
                    issues=issues, expected_schema=tool.raw_schema,
                )
        try:
            arguments = tool.parameters.model_validate(call.arguments)
        except ValidationError as error:
            # 原始 input、ctx、msg 和额外字段名都可能夹带私密输入。
            issues = []
            for issue in error.errors(include_input=False, include_context=False, include_url=False):
                location = issue["loc"]
                field = location[0] if location and location[0] in tool.parameters.model_fields else "<extra>"
                issues.append({"field": field, "reason": "不符合参数 Schema"})
            return _request_error(
                "invalid_arguments", "参数校验失败，请按 Schema 修正。",
                issues=issues, expected_schema=tool.parameters.model_json_schema(),
            )
        try:
            output = tool.handler(**arguments.model_dump())
        except ToolRefusedError as error:
            code, recovery, refused_by_user = _REFUSAL_ERRORS.get(
                getattr(error, "reason", "user_refused"),
                ("tool_request_failed", "do_not_retry_refused_call", False),
            )
            return _request_error(
                code, str(error),
                refused_by_user=refused_by_user, recovery=recovery,
            )
        except ToolExecutionFailedError as error:
            return _request_error(
                "tool_execution_failed", str(error),
                execution_state="unknown", recovery="verify_actual_state_before_retry",
            )
        if not isinstance(output, str):
            raise InvalidToolResultError("工具结果必须是字符串")
        return {"ok": True, "result": output}

    def schemas(self) -> list[dict[str, object]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": (
                    tool.raw_schema if tool.raw_schema is not None
                    else tool.parameters.model_json_schema()
                ),
            }
            for tool in self._tools.values() if not tool.prompt_only
        ]
