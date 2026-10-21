"""保存本地工具绑定，并导出不含处理函数的模型可见元数据。"""

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from min_agent.models import ToolCall
from min_agent.sessions import ExecutionContext, SessionStore


class InvalidToolResultError(TypeError):
    """handler 已返回，但结果不符合当前文本契约。"""


def _request_error(code: str, message: str, **details: object) -> dict[str, object]:
    return {
        "ok": False,
        "error": {
            "code": code, "message": message,
            "execution_state": "not_started", "recovery": "correct_request_with_new_id",
            **details,
        },
    }


@dataclass(frozen=True)
class ToolDefinition:
    """由可信应用代码创建的工具配置，字段赋值后不可直接替换。"""

    name: str
    description: str
    parameters: type[BaseModel]
    handler: Callable[..., object]


class ToolRegistry:
    """保存工具；只有显式 execute 执行 handler。"""

    def __init__(self, *, sessions: SessionStore | None = None, context: ExecutionContext | None = None) -> None:
        if (sessions is None) != (context is None):
            raise ValueError("会话绑定必须同时提供sessions和context")
        self._tools: dict[str, ToolDefinition] = {}
        self._sessions = sessions
        self._context = sessions.check_access(context) if sessions is not None else None

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
            from min_agent.operations import execute_once
            return execute_once(self._sessions, self._context, call, operation_id, self._execute)
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
        output = tool.handler(**arguments.model_dump())
        if not isinstance(output, str):
            raise InvalidToolResultError("工具结果必须是字符串")
        return {"ok": True, "result": output}

    def schemas(self) -> list[dict[str, object]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters.model_json_schema(),
            }
            for tool in self._tools.values()
        ]
