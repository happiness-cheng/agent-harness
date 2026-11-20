"""一次性迁移：把 ``src/min_agent`` 根目录模块收进子包，并重写导入路径。

2026-11-15 已执行（PKG-LAYOUT-001）。保留本文件供审计与回滚参考：
``MAPPING`` 是旧模块名 → 新子包路径的唯一事实来源，逆映射就是反向字典。
本脚本只做三件事：移动文件、补子包 docstring、重写 ``src/tests/scripts`` 里的
``min_agent.<旧模块>`` 引用；不改任何测试断言，不触碰历史 ``docs/``。
"""

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "min_agent"

MAPPING = {
    # 数据契约与基础设施
    "models": "contracts.models",
    "trace": "contracts.trace",
    "clock": "contracts.clock",
    "hooks": "contracts.hooks",
    # 预算、资源与检查点账本
    "budget": "ledger.budget",
    "budget_persistence": "ledger.budget_persistence",
    "resource_budget": "ledger.resource_budget",
    "checkpoints": "ledger.checkpoints",
    # 执行引擎的辅助模块（loop/stages 等已在 runtime/ 内）
    "progress_guard": "runtime.progress_guard",
    "streaming": "runtime.streaming",
    # 会话、任务与持久化
    "sessions": "session.store",
    "storage": "session.storage",
    "task_state": "session.task_state",
    "operations": "session.operations",
    "artifacts": "session.artifacts",
    "events": "session.events",
    # 上下文与状态投影
    "context": "context.manager",
    "state_bar": "context.state_bar",
    "model_context": "context.model_context",
    "structured_context": "context.structured_context",
    # 压缩与技能披露
    "compaction": "compression.compaction",
    "compression_evidence": "compression.evidence",
    "compression_text": "compression.text",
    "compression_selector": "compression.selector",
    "compression_summary": "compression.summary",
    "skills": "compression.skills",
    "skill_retention": "compression.skill_retention",
    # 记忆
    "memory": "memory.journal",
    "memory_records": "memory.records",
    "memory_state": "memory.state",
    # 公开目标与进展
    "public_feedback": "goals.public_feedback",
    "public_goal_registry": "goals.public_goal_registry",
    "public_goal_adapter": "goals.public_goal_adapter",
    "progress_scoring": "goals.progress_scoring",
    "todo_observation": "goals.todo_observation",
    # 多 Agent 编排
    "manager": "agents.manager",
    "multi_agent_service": "agents.multi_agent_service",
    "agent_messages": "agents.agent_messages",
    "context_policy": "agents.context_policy",
    "subagent": "agents.subagent",
    "subagent_review": "agents.subagent_review",
    "subagent_state": "agents.subagent_state",
    "subagent_collab": "agents.subagent_collab",
    "parallel": "agents.parallel",
    "task_graph": "agents.task_graph",
    # 研究服务
    "research": "research.service",
    "research_observation": "research.observation",
    # 通知与调度
    "notifications": "notify.notifications",
    "desktop_notifications": "notify.desktop_notifications",
    "scheduler": "notify.scheduler",
    # Windows 活动采集
    "activity_identity": "activity.identity",
    "activity_records": "activity.records",
    "activity_store": "activity.store",
    "activity_m2": "activity.m2",
    "activity_m3": "activity.m3",
    "activity_changes": "activity.changes",
    "activity_reader": "activity.reader",
    "activity_connector": "activity.connector",
    "activity_memory_adapter": "activity.memory_adapter",
}

PACKAGE_DOCS = {
    "contracts": "跨模块数据契约与基础设施：消息、trace、时钟、策略钩子。",
    "ledger": "预算、资源与检查点账本；只记账，不执行业务动作。",
    "session": "会话、任务状态、持久化、幂等操作与执行归属。",
    "context": "提示词上下文装配、状态栏投影与模型窗口声明。",
    "compression": "历史压缩、证据选择与技能披露。",
    "memory": "记忆日志、记忆记录与会话状态表。",
    "goals": "公开目标绑定、反馈投影与进展评分。",
    "agents": "多 Agent 编排：管理者、worker、契约与依赖图。",
    "research": "研究服务与它的观察投影。",
    "notify": "通知、桌面通知与定时调度。",
    "activity": "Windows 活动采集：身份、存储、变更与适配。",
}

MODULE_REFERENCE = re.compile(r"min_agent\.([a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)*)")
FROM_PACKAGE = re.compile(r"from min_agent import ([a-z_][a-z0-9_]*)(\s+as\s+[a-z_][a-z0-9_]*)?")


def rewrite_references(text: str) -> str:
    """按最长旧模块名替换一次；替换结果不会被二次扫描。"""

    def replace(match: re.Match) -> str:
        parts = match.group(1).split(".")
        for size in range(len(parts), 0, -1):
            old = ".".join(parts[:size])
            if old in MAPPING:
                suffix = ".".join(parts[size:])
                return "min_agent." + MAPPING[old] + (("." + suffix) if suffix else "")
        return match.group(0)

    def replace_from_package(match: re.Match) -> str:
        """处理 ``from min_agent.activity import reader as activity_reader`` 这种子模块导入形式。"""
        old, alias = match.group(1), match.group(2) or ""
        if old not in MAPPING:
            return match.group(0)
        package, leaf = MAPPING[old].rsplit(".", 1)
        suffix = alias or (f" as {old}" if leaf != old else "")
        return f"from min_agent.{package} import {leaf}{suffix}"

    text = MODULE_REFERENCE.sub(replace, text)
    return FROM_PACKAGE.sub(replace_from_package, text)


def move_modules() -> int:
    migrated = 0
    for old, new in MAPPING.items():
        source = PACKAGE / (old + ".py")
        if not source.exists():
            raise SystemExit(f"缺少待迁移模块：{source}")
        target = PACKAGE.joinpath(*new.split(".")).with_suffix(".py")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise SystemExit(f"目标已存在，拒绝覆盖：{target}")
        shutil.move(str(source), str(target))
        migrated += 1
    return migrated


def write_package_inits() -> None:
    for name, doc in PACKAGE_DOCS.items():
        init = PACKAGE / name / "__init__.py"
        if init.exists():
            continue
        init.write_text(f'"""{doc}"""\n', encoding="utf-8", newline="")


def rewrite_files() -> int:
    touched = 0
    for base in (ROOT / "src", ROOT / "tests", ROOT / "scripts"):
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            original = path.read_text(encoding="utf-8")
            updated = rewrite_references(original)
            if updated != original:
                path.write_text(updated, encoding="utf-8", newline="")
                touched += 1
    return touched


def clear_bytecode() -> None:
    for cache in (ROOT / "src").rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)


def main() -> None:
    migrated = move_modules()
    write_package_inits()
    touched = rewrite_files()
    clear_bytecode()
    remaining = [path for path in (PACKAGE.glob("*.py"))]
    print(f"migrated={migrated} rewritten={touched} "
          f"root_modules={[path.name for path in remaining]}")


if __name__ == "__main__":
    main()
