"""只按当前会话的随机ID存取原始材料，不提供任意磁盘路径。"""

from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID, uuid4
import json


class ArtifactStore:
    def __init__(self, sessions, context, *, max_bytes=12_000_000):
        self.sessions, self.context = sessions, sessions.check_access(context)
        self.max_bytes = max_bytes

    def save(self, content: str, *, source="", kind="text", metadata=None):
        if not isinstance(content, str):
            raise TypeError("原文必须为文本")
        with self.sessions.control(self.context):
            values = self.sessions.extension(self.context, "artifacts", {})
            if len(json.dumps(values, ensure_ascii=False).encode()) + len(content.encode()) + len(json.dumps(metadata or {}, ensure_ascii=False).encode()) + 500 > self.max_bytes:
                raise ValueError("当前会话原始材料预算不足")
            identity = str(uuid4())
            values[identity] = {"id": identity, "content": content, "source": source, "kind": kind,
                "sha256": sha256(content.encode()).hexdigest(), "metadata": metadata or {},
                "retrieved_at": datetime.now(UTC).isoformat()}
            self.sessions.set_extension(self.context, "artifacts", values)
            return identity

    def get(self, artifact_id):
        try:
            identity = str(UUID(str(artifact_id)))
        except (ValueError, TypeError):
            raise PermissionError("原文引用无效") from None
        item = self.sessions.extension(self.context, "artifacts", {}).get(identity)
        if item is None:
            raise PermissionError("当前会话没有该原文引用")
        return item

    def read(self, artifact_id, *, start=0, limit=4000):
        if type(start) is not int or type(limit) is not int or start < 0 or not 1 <= limit <= 8000:
            raise ValueError("原文读取范围无效")
        item = self.get(artifact_id); content = item["content"]
        return {"artifact_id": item["id"], "source": item["source"], "start": start,
                "end": min(start + limit, len(content)), "text": content[start:start + limit],
                "total_characters": len(content),
                "truncated": start + limit < len(content), "sha256": item["sha256"]}
