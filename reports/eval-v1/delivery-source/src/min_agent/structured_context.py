"""第二层：独立交互时间线加状态视图，不调用LLM、不合并原始归档。"""

from min_agent.checkpoints import history_hash
from min_agent.compression_text import choose_excerpts, text_units
from min_agent.memory import MemoryJournal
from min_agent.memory_records import interaction_events, source_text


def structured_history(sessions, context, messages, end, objective):
    journal = MemoryJournal(sessions, context)
    records = sessions.extension(context, "memory_journal", {})
    boundaries = [index for index, message in enumerate(messages[:end]) if message.role == "user"]
    if not boundaries or boundaries[0] != 0:
        raise ValueError("结构化历史必须从完整用户交互开始")
    boundaries.append(end)
    episodes = []
    for start, stop in zip(boundaries, boundaries[1:]):
        segment = messages[start:stop]
        if not segment or sessions._pending(segment):
            raise ValueError("结构化层不能拆开工具请求与结果")
        source_hash = history_hash(segment)
        record = next((value for value in records.values() if value["start"] == start
            and value["end"] == stop and value["history_hash"] == source_hash), None)
        if record is None:
            identity = journal.archive(segment, "完整交互的结构化来源索引", start=start)
            record = journal.read(identity)
        events = interaction_events(context, record["id"], segment, start)
        compact = []
        for event in events:
            if event["kind"] in {"user_request", "assistant_statement"}:
                message = messages[event["source"]["message_index"]]
                units, _ = text_units(source_text(message), objective)
                excerpts = choose_excerpts(units, objective, max_bytes=1400)
                compact.append({"kind": event["kind"], "unverified": True, "excerpts": [
                    {"quote": unit["quote"],
                        **{name: unit[name] for name in ("model_selected", "rule_matched", "relevance_review")},
                        "source": {**event["source"],
                        "start": unit["start"], "end": unit["end"]}} for unit in excerpts]})
            else:
                compact.append({key: value for key, value in event.items() if key != "id"})
        episodes.append({"archive_id": record["id"], "start": start, "end": stop, "events": compact})
    return {"kind": "structured", "episodes": episodes,
            "notice": "按交互保留独立记录；原句和工具报告不自动成为已核实事实或授权"}
