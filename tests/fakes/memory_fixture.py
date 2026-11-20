"""测试专用：从已删除的 long_memory.evaluation 迁出的合成批准 Fixture。

只服务于 ``test_adrian_evidence_delivery``；不是生产代码，不注册、不自动导入。
"""

from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import re
from uuid import NAMESPACE_URL, uuid5

from min_agent.long_memory import LongMemory
from min_agent.long_memory.models import Evidence, Memory, MemoryEdge, Topic
from min_agent.session.store import SessionStore


def timestamp(value):
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result


def normalized(text):
    return re.sub(r"\s+", " ", str(text).casefold()).strip()


def build_approved_fixture(case, *, approval):
    if approval != "approved_fixture":
        raise PermissionError("仅显式approved_fixture可模拟批准，不是生产默认")
    store = SessionStore()
    owner = uuid5(NAMESPACE_URL, "book-memory:" + case["test_id"])
    context = store.create(owner)
    memory = LongMemory(store)
    histories = sorted(case["conversation_histories"], key=lambda h: timestamp(h["timestamp"]))
    roles = Counter()
    units = 0
    for number, history in enumerate(histories):
        at = timestamp(history["timestamp"])
        memory.clock = lambda at=at: at
        business = str(history.get("metadata", {}).get("business", case["category"]))
        topic_id = str(uuid5(owner, "topic:" + business))
        origin = str(uuid5(owner, "session:" + str(history["conversation_id"])))

        def action(doc, clock, history=history, number=number, at=at,
                   business=business, topic_id=topic_id, origin=origin):
            nonlocal units
            doc.topics.setdefault(topic_id, Topic(
                id=topic_id, user_id=str(owner), name=business,
                keywords=[business], created_at=at))
            for index, message in enumerate(history["messages"]):
                role = message["role"]
                if role not in {"user", "assistant", "tool"}:
                    continue
                content = str(message.get("content", ""))
                if not content:
                    continue
                roles[role] += 1
                source_id = str(uuid5(owner, f"{origin}:{index}"))
                classification = "DIRECT" if role == "user" else "INFERRED"
                doc.evidence[source_id] = Evidence(
                    id=source_id, user_id=str(owner), session_id=origin,
                    message_ref=index, role=role, observed_at=at,
                    source_hash=sha256(content.encode()).hexdigest(),
                    excerpt_or_pointer=f'book://{case["test_id"]}/{origin}/{index}',
                    classification=classification)
                for start in range(0, len(content), 5000):
                    identity = str(uuid5(owner, f"memory:{origin}:{index}:{start}"))
                    text = (f'[{role};session={history["conversation_id"]};'
                            f"range={start}:{min(start + 5000, len(content))}] "
                            + content[start:start + 5000])
                    cost = 3 if re.search(
                        r"\b(must|never|deadline|cannot|allerg|expire|cancel)\w*\b|不要|必须|禁止|截止",
                        text, re.I) else 1
                    item = Memory(
                        id=identity, user_id=str(owner), session_id=origin, kind="episode",
                        content=text, evidence_type=classification, evidence_refs=[source_id],
                        observed_at=at, effective_from=at, created_at=at, updated_at=at,
                        confirmed_at=at, confirmation_ref="approved_fixture:" + source_id,
                        topic_ids=[topic_id], decision_cost=cost)
                    doc.memories[identity] = [item]
                    edge_id = str(uuid5(owner, "edge:" + identity))
                    doc.edges[edge_id] = MemoryEdge(
                        id=edge_id, user_id=str(owner), source_id=identity,
                        target_id=topic_id, type="BELONGS_TO",
                        evidence_refs=[source_id], observed_at=at)
                    occurrences = sum(
                        normalized(m.get("content", "")) == normalized(content)
                        for previous in histories[:number + 1]
                        for m in previous["messages"])
                    for n in range(max(0, occurrences - 1)):
                        doc.usages.append(dict(
                            memory_id=identity,
                            event_id=f"exact_historical_mention:{n}", at=at.isoformat()))
                    units += 1

        memory._write(context, action)
    return memory, context, {"units": units, "roles": dict(roles),
                             "last_time": timestamp(histories[-1]["timestamp"])}