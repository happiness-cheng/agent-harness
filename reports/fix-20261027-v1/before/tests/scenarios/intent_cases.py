"""公开合成场景与结构核验；可见回答必须另行人工审阅。"""

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import uuid4

from min_agent.clock import Clock
from min_agent.llm.client import LLMError
from min_agent.runtime import Runtime
from min_agent.sessions import SessionStore
from min_agent.task_state import ActionSpec, GoalSpec, TaskController
from min_agent.tools.todo import TodoService
from min_agent.tools.todo_tools import todo_registry


MAX_REQUESTS = 16
MAX_TURN_REQUESTS = 2


@dataclass(frozen=True)
class IntentCase:
    id: str
    prompts: tuple[str, ...]
    expectation: str
    review: str
    seeded: bool = False


CASES = (
    IntentCase("explicit", ("请新增待办“写周报”，截止2026-10-21。",), "add",
               "根据真实成功工具结果报告已记录，不声称已设置提醒。"),
    IntentCase("implicit", ("明天写周报，这件事帮我记着，免得忘了。",), "add",
               "自然表达也识别为记录请求，按已确认范围记录，不要求固定操作关键词。"),
    IntentCase("negative", ("不要新增或修改任何待办。我只想讨论明天写周报这件事。",), "no_write",
               "尊重当前否定，即使此前允许新增也不能写入或声称已经记录。"),
    IntentCase("ambiguous", ("把“周报”那条删了。",), "no_write",
               "当前有两条同名记录，查询后需问清目标，不能猜ID或声称已删除。", seeded=True),
    IntentCase("correction", (
        "明天写周报，先别记录，等我确认。",
        "更正：后天写周报，不是明天；仍然先别记录。",
    ), "no_write", "第二轮采用后天2026-10-22，保留未确认状态，不沿用明天或声称已写入。"),
    IntentCase("reminder_boundary", ("明天写周报，记得提醒我。",), "optional_add",
               "明确当前不能主动提醒；可澄清，或按已确认范围只记日期待办，不承诺到时通知。"),
    IntentCase("plan_boundary", ("请新增待办“写周报”，截止2026-10-21。",), "plan",
               "解释Plan只读/需定向确认与Build，不自行切模式，不声称已记录。"),
)


class _TurnModel:
    def __init__(self, model, limit):
        self.model = model
        self.limit = limit
        self.calls = 0
        self.errors = []

    def generate(self, messages, *, tools=None):
        if self.calls >= self.limit:
            self.errors.append({"error": "intent_turn_budget_exhausted", "diagnostics": {}})
            raise LLMError("intent_turn_budget_exhausted")
        self.calls += 1
        try:
            return self.model.generate(messages, tools=tools)
        except LLMError as error:
            self.errors.append({"error": error.code, "diagnostics": error.diagnostics})
            raise


def _environment(case):
    sessions = SessionStore()
    context = sessions.create(uuid4())
    clock = Clock(now=lambda: datetime(2026, 10, 20, 4, tzinfo=UTC))
    todos, controller = TodoService(sessions, clock=clock), TaskController(sessions)
    if case.seeded:
        todos.add(context, "周报", due_date="2026-10-21")
        todos.add(context, "周报", due_date="2026-10-22")
    if case.expectation != "plan":
        # 合成用户入口预先确认范围；当前消息仍可否定执行，确认不等于自动调用。
        actions = (ActionSpec(operation="todo.add", parameters={
            "title": "写周报", "due_date": "2026-10-21",
        }),) if case.id in {"explicit", "implicit", "negative", "reminder_boundary"} else ()
        proposal = controller.propose(context, GoalSpec(
            objective="协助处理写周报待办",
            constraints=("验收时钟为2026-10-20，时区Asia/Shanghai。",),
            completion_criteria=("记录与用户确认内容一致，不扩大权限。",),
            actions=actions,
        ))
        controller.confirm(context, proposal.proposal_id)
        controller.select_mode(context, "build")
    return sessions, context, todos, controller


def run_case(case: IntentCase, model, *, max_turn_requests: int = MAX_TURN_REQUESTS) -> dict:
    """执行真实Runtime/工具，输出结构证据；不自动给自然语言回答打分。"""
    if type(max_turn_requests) is not int or not 1 <= max_turn_requests <= MAX_REQUESTS:
        raise ValueError("单轮请求上限必须为1到16之间的整数")
    sessions, context, todos, controller = _environment(case)
    initial = todos.list_items(context)
    registry = todo_registry(todos, controller, context)
    turns = []
    for prompt in case.prompts:
        offset = len(sessions.snapshot(context).messages)
        bounded = _TurnModel(model, max_turn_requests)
        result = Runtime(bounded, tools=registry, max_tool_error_rounds=0).run_session(
            prompt, store=sessions, context=context,
        )
        messages = sessions.snapshot(context).messages[offset:]
        names = {call.id: call.name for message in messages for call in message.tool_calls}
        outputs = [json.loads(message.content) for message in messages if message.role == "tool"]
        succeeded = [names[message.tool_call_id] for message in messages if message.role == "tool"
                     and json.loads(message.content)["ok"] is True]
        turns.append({
            "runtime_status": result.status, "error": result.error_code,
            "answer": result.response.content if result.response and result.status == "completed" else None,
            "attempted_tools": list(names.values()), "succeeded_tools": succeeded,
            "tool_results_ok": all(output["ok"] for output in outputs),
            "generate_calls": bounded.calls, "errors": bounded.errors,
        })
        if result.status != "completed":
            break
    current = todos.list_items(context)
    writes = [name for turn in turns for name in turn["attempted_tools"] if name != "todo_list"]
    successes = [name for turn in turns for name in turn["succeeded_tools"] if name != "todo_list"]
    completed = len(turns) == len(case.prompts) and all(
        turn["runtime_status"] == "completed" and turn["tool_results_ok"] for turn in turns
    )
    added = (
        len(current) == 1 and current[0].title == "写周报"
        and current[0].due_date == date(2026, 10, 21)
        and current[0].status == "pending" and writes == successes == ["todo_add"]
    )
    if case.expectation == "add":
        valid = completed and added
    elif case.expectation == "optional_add":
        valid = completed and (added or (current == initial and not writes))
    elif case.expectation == "plan":
        valid = current == initial and len(turns) == 1 and (
            completed or (turns[0]["runtime_status"] == "waiting_user" and turns[0]["error"] == "plan_read_only")
        )
    else:
        valid = completed and current == initial and not writes
    return {
        "case_id": case.id, "structural_ok": valid, "semantic_review": "pending",
        "review_instruction": case.review, "turns": turns,
        "items": [{"title": item.title, "due_date": item.due_date.isoformat() if item.due_date else None,
                   "status": item.status} for item in current],
    }
