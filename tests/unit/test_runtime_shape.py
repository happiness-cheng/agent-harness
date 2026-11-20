"""Runtime 的结构门禁：主循环只允许编排，错误处理必须留在组件里。

这些断言不检查实现细节，只检查「主循环保持薄」这条架构约束和公开契约，
防止后续改动把异常处理、工具逻辑重新塞回 ``_run``。
"""

import ast
import inspect
import textwrap

import pytest

from min_agent.runtime import Model, RunResult, Runtime


def _function_source(function) -> str:
    return textwrap.dedent(inspect.getsource(function))


def _function_ast(function):
    return ast.parse(_function_source(function)).body[0]


def test_main_loop_has_no_error_handling_or_dispatch():
    function = _function_ast(Runtime._run)

    assert not [node for node in ast.walk(function) if isinstance(node, ast.Try)]
    assert not [node for node in ast.walk(function) if isinstance(node, ast.Match)]
    assert not [node for node in ast.walk(function) if isinstance(node, ast.ExceptHandler)]
    assert len([node for node in ast.walk(function) if isinstance(node, ast.Return)]) <= 2
    assert len([node for node in ast.walk(function) if isinstance(node, ast.If)]) <= 2


def test_main_loop_stays_short():
    assert len(_function_source(Runtime._run).splitlines()) <= 35


@pytest.mark.parametrize(("method", "expected"), [
    (Runtime.__init__, [
        "self", "model", "tools", "max_tool_error_rounds", "max_rounds", "trace", "budget",
        "checkpoint_policy", "control", "progress_guard", "recover_unknown_writes",
        "before_execute", "system_prompt",
    ]),
    (Runtime.run, ["self", "text", "trace_context", "store", "execution_context"]),
    (Runtime.run_session, [
        "self", "text", "store", "context", "resume_checkpoint_id", "resume_task_id",
    ]),
    (Runtime.resume_session, ["self", "checkpoint_id", "store", "context", "text"]),
])
def test_public_signatures_are_frozen(method, expected):
    assert list(inspect.signature(method).parameters) == expected


def test_facade_keeps_documented_exports():
    import min_agent.runtime as package

    assert package.__all__ == ["Runtime", "RunResult", "Model"]
    assert package.Runtime is Runtime
    assert package.RunResult is RunResult
    assert package.Model is Model
    assert Runtime.__module__ == "min_agent.runtime.loop"
