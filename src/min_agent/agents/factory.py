"""WIRING-003：把生产 Model 接成可信的 MultiAgentService 工厂。

本模块只做**装配**：模型档位、worker 角色、验收规格与资源上限都在这里固定，
模型只能通过 ``delegate_task(objective)`` 提交一个有界目标，碰不到 owner /
session / budget / 工具白名单。工厂本身不注册工具，注册仍由
``HarnessApplication._registry`` 在 ``enable_multi_agent=True`` 时完成。

明确不做：

* 不新建会话控制循环，不读写 ``SessionEvents`` 的 steer/stop；
* 不调用外部网络，不做模型选择——``model`` 由每次运行的 ``raw_model`` 传入；
* **不保证子 Agent 的结论正确**：calculator worker 的 PASS 只证明宿主收到了
  ``calculator`` 工具回执且结果结构合法，不核对回执数值与摘要是否一致
  （``semantic_review`` 恒为 UNKNOWN）；不能把 PASS 当作算术正确性证明。
"""

from __future__ import annotations

from typing import Callable

from min_agent.agents.manager import ModelProfile, WorkerSpec
from min_agent.agents.multi_agent_service import (
    MultiAgentConfig,
    MultiAgentService,
    ProductionModelTransport,
)
from min_agent.agents.subagent_review import ReviewSpec
from min_agent.ledger.resource_budget import ResourceLimits

__all__ = ["build_multi_agent_factory", "build_multi_agent_config"]

MANAGER_MAX_TOKENS = 1024
WORKER_MAX_TOKENS = 768
_MAX_SUBTASKS = 3
_DELEGATE_MODEL_CALLS = 16
_DELEGATE_TOOL_CALLS = 9
_WALL_SECONDS = 900.0


def _model_identity(model) -> tuple[str, str]:
    """从生产适配器读出 (vendor, model_id)；读不到就用类名，不猜具体模型。"""
    settings = getattr(model, "_settings", None)
    model_id = (getattr(settings, "model", None)
                or getattr(model, "_model", None)
                or type(model).__name__)
    vendor = getattr(model, "_provider", None) or "local"
    return str(vendor)[:64], str(model_id)[:128] or "model"


def _worker_specs() -> dict[str, WorkerSpec]:
    # WIRING-003：只接线**可机械验证**的 worker 角色。calculator 有宿主生成的
    # 工具回执，PASS 不依赖模型自报；纯文本角色没有确定性判据，本批不接入，
    # 避免用「包含某句话」这类格式标记冒充语义验收（宁可不支持也不假装）。
    calculator = WorkerSpec(
        role="calculator",
        role_label="计算员",
        scope="只做算术计算：必须调用 calculator 工具核对结果，再给出简短答复。",
        out_of_scope=("不讨论与计算无关的内容", "不调用 calculator 以外的工具",
                      "不代替用户做最终决定"),
        review_spec=ReviewSpec(
            objective="产生 calculator 工具回执并给出结论",
            required_checks=("calculator",),
            min_receipts=1,
            max_rounds=1,
        ),
        access="read_only",
        allowed_tools=("calculator",),
        max_model_calls=4,
        max_tool_calls=3,
        deadline_seconds=120.0,
    )
    return {calculator.role: calculator}


def build_multi_agent_config(model) -> MultiAgentConfig:
    """为一次委派构造固定配置；模型身份只影响标注，不影响权限。"""
    vendor, model_id = _model_identity(model)
    manager_profile = ModelProfile(
        key="manager", tier="strong", model_id=model_id, max_tokens=MANAGER_MAX_TOKENS)
    worker_profile = ModelProfile(
        key="worker", tier="weak", model_id=model_id, max_tokens=WORKER_MAX_TOKENS)

    def manager_transport() -> ProductionModelTransport:
        return ProductionModelTransport(
            model, vendor=vendor, model_id=model_id,
            max_output_tokens=MANAGER_MAX_TOKENS)

    def worker_transport(_key: str) -> ProductionModelTransport:
        return ProductionModelTransport(
            model, vendor=vendor, model_id=model_id,
            max_output_tokens=WORKER_MAX_TOKENS)

    return MultiAgentConfig(
        manager_profile=manager_profile,
        manager_transport=manager_transport(),
        worker_profiles={worker_profile.key: worker_profile},
        worker_specs=_worker_specs(),
        worker_transport_factory=worker_transport,
        resource_limits=ResourceLimits(wall_seconds=_WALL_SECONDS),
        max_workers=1,
        max_subtasks=_MAX_SUBTASKS,
        delegate_model_calls=_DELEGATE_MODEL_CALLS,
        delegate_tool_calls=_DELEGATE_TOOL_CALLS,
    )


def build_multi_agent_factory() -> Callable[..., MultiAgentService]:
    """返回 ``HarnessApplication`` 可调用的可信工厂。

    工厂每次调用新建一个 Service；``model`` 必须由应用从当前运行的
    ``raw_model`` 传入，不能来自模型参数或会话扩展。
    """

    def factory(*, sessions, context, parent_budget, registry, control, model):
        config = build_multi_agent_config(model)
        return MultiAgentService(
            config, sessions=sessions, context=context,
            parent_budget=parent_budget, registry=registry, control=control)

    return factory