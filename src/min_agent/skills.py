"""只读预先登记的Markdown技能；不执行脚本或变更权限。"""

from pathlib import Path
import re


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
