"""R2 State Bar 谓词：只排除 grant，保留其余 WorkState 与 receipt 判据。"""
import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from min_agent.sessions import SessionStore
from min_agent.task_state import GoalSpec, TaskController


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("audit_state_bar_r2", ROOT / "scripts" / "audit_state_bar.py")
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


def goal():
    return GoalSpec(objective="synthetic", completion_criteria=("synthetic",),
                    actions=())


def states():
    store = SessionStore()
    context = store.create(uuid4())
    controller = TaskController(store)
    proposal = controller.propose(context, goal())
    controller.confirm(context, proposal.proposal_id)
    controller.select_mode(context, "build")
    original = controller.snapshot(context)
    task = store.begin(context)
    return store, context, task, original, controller.snapshot(context)


def predicate(original, current, receipts=None, before=None):
    tree = ast.parse((ROOT / "scripts" / "audit_state_bar.py").read_text(encoding="utf-8"))
    expr = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "update":
            for keyword in node.keywords:
                if keyword.arg == "real_progress_unchanged":
                    expr = keyword.value
    assert expr is not None
    if isinstance(expr, ast.Name):
        definitions = [node.value for node in ast.walk(tree)
                       if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == expr.id for target in node.targets)]
        assert len(definitions) == 1
        expr = definitions[0]
    receipts = {} if receipts is None else receipts
    before = receipts if before is None else before
    env = SimpleNamespace(context=None, store=SimpleNamespace(extension=lambda *args: receipts))
    return eval(compile(ast.Expression(expr), str(ROOT / "scripts" / "audit_state_bar.py"), "eval"),
                {"__builtins__": {}},
                dict(current_work=current, original_work=original, env=env,
                     before_receipts=before))


def test_predicate_excludes_only_grant_and_detects_other_changes():
    store, context, task, original, bound = states()
    assert original.grant != bound.grant
    assert original.model_dump(exclude={"grant"}) == bound.model_dump(exclude={"grant"})
    assert predicate(original, bound) is True
    waiting = original.model_copy(update={"status": "waiting_user",
                                          "waiting_reason": "scope_not_confirmed"})
    assert predicate(original, waiting) is False
    reason = waiting.model_copy(update={"waiting_reason": "goal_unconfirmed"})
    assert predicate(waiting, reason) is False
    proposal_changed = original.model_copy(update={
        "proposal": original.proposal.model_copy(update={"proposal_id": uuid4()})})
    assert predicate(original, proposal_changed) is False
    assert predicate(original, original, receipts={"new": {}}, before={}) is False


def test_grant_lifecycle_helper_accepts_pending_to_bound_and_finish():
    store, context, task, original, bound = states()
    assert PROBE.grant_lifecycle_valid(original, bound, task, None) is True
    with store._lock:
        store.finish(context, task.task_id, status="completed")
        closed = store.snapshot(context)
    assert closed.work_state.grant.status == "closed"
    assert PROBE.grant_lifecycle_valid(original, closed.work_state, closed.task, closed.active_budget_id) is True


def test_grant_lifecycle_helper_rejects_identity_logical_budget_reset():
    store, context, task, original, bound = states()
    grant = bound.grant
    for field, value in (
            ("grant_id", uuid4()),
            ("issued_revision", grant.issued_revision + 1),
            ("logical_task_id", uuid4()),
            ("budget_id", uuid4()),
    ):
        changed = bound.model_copy(update={
            "grant": grant.model_copy(update={field: value})})
        assert PROBE.grant_lifecycle_valid(original, changed, task, None) is False
