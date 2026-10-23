"""只读预先登记的Markdown技能；不执行脚本或变更权限。"""

from pathlib import Path
from hashlib import sha256
import json
import re

from min_agent.models import Message


class SkillCatalog:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.entries = {path.stem: path for path in self.root.glob("*.md")
                        if not path.is_symlink() and path.resolve().parent == self.root
                        and re.fullmatch(r"[A-Za-z0-9_-]+", path.stem)}

    def summaries(self):
        return [{"name": name, "description": self.read(name).splitlines()[0][:200]}
                for name in self.entries]

    def read(self, name):
        if name not in self.entries: raise PermissionError("技能未登记")
        path = self.entries[name]
        if path.is_symlink() or path.resolve().parent != self.root: raise PermissionError("技能路径越界")
        if path.stat().st_size > 20000: raise ValueError("技能正文过大")
        return path.read_text(encoding="utf-8")


def project_skill_results(messages: list[Message]) -> list[Message]:
    """只压缩当前可见的重复正文；不修改原历史，不把摘要视为已加载全文。"""
    calls = {}
    loaded = {}
    projected = []
    for message in messages:
        calls.update((call.id, call) for call in message.tool_calls)
        call = calls.get(message.tool_call_id) if message.role == "tool" else None
        candidate = message
        if call is not None and call.name == "read_skill":
            try:
                result = json.loads(message.content)
            except json.JSONDecodeError:
                result = None
            name = call.arguments.get("name")
            if (isinstance(result, dict) and result.get("ok") is True
                    and isinstance(result.get("result"), str) and result["result"]
                    and result.get("skill_status") != "already_loaded" and isinstance(name, str)):
                digest = sha256(result["result"].encode()).hexdigest()
                key = (name, digest)
                if key in loaded:
                    note = {"ok": True, "result": "相同Skill正文已在当前上下文中加载，无需重复追加。",
                            "skill_status": "already_loaded", "skill_name": name,
                            "body_sha256": digest, "loaded_by_tool_call_id": loaded[key]}
                    candidate = message.model_copy(update={"content": json.dumps(note, ensure_ascii=False)}, deep=True)
                else:
                    loaded[key] = message.tool_call_id
        projected.append(candidate.model_copy(deep=True) if candidate is message else candidate)
    return projected
