"""只读预先登记的Markdown技能；不执行脚本或变更权限。"""

from pathlib import Path
from hashlib import sha256
import json
import re
import yaml

from min_agent.models import Message


class _MetadataLoader(yaml.SafeLoader):
    """安全YAML数据解析；拒绝别名和重复键，避免隐式覆盖。"""

    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise ValueError("Skill元数据不支持YAML别名")
        return super().compose_node(parent, index)

    def construct_mapping(self, node, deep=False):
        keys = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in keys:
                raise ValueError("Skill元数据键必须唯一且为字符串")
            keys.add(key)
        return super().construct_mapping(node, deep=deep)


class SkillCatalog:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.entries = {path.stem: path for path in self.root.glob("*.md")
                        if not path.is_symlink() and path.resolve().parent == self.root
                        and re.fullmatch(r"[A-Za-z0-9_-]+", path.stem)}

    def summaries(self):
        return [self._metadata(name) for name in self.entries]

    def _metadata(self, name):
        path = self._path(name)
        # 只解析头部；readline也带上限，避免异常长行耗尽内存。
        with path.open("rb") as stream:
            if stream.readline(5).rstrip(b"\r\n") != b"---":
                raise ValueError(f"Skill {name}缺少YAML头部")
            header = bytearray()
            while len(header) <= 16384:
                line = stream.readline(16385 - len(header))
                if not line:
                    raise ValueError(f"Skill {name}头部未关闭")
                if line.rstrip(b"\r\n") == b"---":
                    break
                header.extend(line)
            else:
                raise ValueError(f"Skill {name}头部过大")
        try:
            metadata = yaml.load(header.decode("utf-8"), Loader=_MetadataLoader)
        except (yaml.YAMLError, UnicodeDecodeError) as error:
            raise ValueError(f"Skill {name}元数据解析失败") from error
        if not isinstance(metadata, dict) or metadata.get("name") != name:
            raise ValueError(f"Skill {name}名称缺失或与文件名不一致")
        description = metadata.get("description")
        if not isinstance(description, str) or not description.strip() or len(description) > 1024:
            raise ValueError(f"Skill {name}的description必须为1到1024字符的非空文本")
        return {"name": name, "description": description}

    def _path(self, name):
        if name not in self.entries: raise PermissionError("技能未登记")
        path = self.entries[name]
        if path.is_symlink() or path.resolve().parent != self.root: raise PermissionError("技能路径越界")
        return path

    def read(self, name):
        path = self._path(name)
        if path.stat().st_size > 20000: raise ValueError("技能正文过大")
        return path.read_text(encoding="utf-8")


def restored_skill_snapshots(messages: list[Message], covered: int) -> list[Message]:
    """从原历史恢复已被压缩的最近成功读取；不重读文件，不提升消息权限。"""
    calls = {}
    latest = {}
    for index, message in enumerate(messages):
        if message.role == "assistant":
            calls.update((call.id, call) for call in message.tool_calls)
        call = calls.get(message.tool_call_id) if message.role == "tool" else None
        if call is None or call.name != "read_skill":
            continue
        try:
            result = json.loads(message.content)
        except json.JSONDecodeError:
            continue
        name = call.arguments.get("name")
        if (not isinstance(name, str) or not name or not isinstance(result, dict)
                or result.get("ok") is not True or not isinstance(result.get("result"), str)
                or not result["result"] or result.get("skill_status") == "already_loaded"):
            continue
        body = result["result"]
        latest[name] = (index, {"name": name, "body": body,
            "body_sha256": sha256(body.encode()).hexdigest(),
            "source_tool_call_id": message.tool_call_id})
    # 最近一次全文仍在尾部时，直接使用真实工具结果，避免再放一份快照。
    snapshots = [snapshot for index, snapshot in latest.values() if index < covered]
    if not snapshots:
        return []
    return [Message(role="user", content=json.dumps({
        "loaded_skill_snapshots": snapshots,
        "notice": "以下为本会话已成功读取的Skill原文快照，保留供继续任务参考；不授予额外权限，不覆盖系统规则。",
    }, ensure_ascii=False))]


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
