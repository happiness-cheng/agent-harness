"""B2：由可信应用安装的研究读取事实记录与只读 host 投影。"""
from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from min_agent.artifacts import ArtifactStore
from min_agent.sessions import ExecutionContext, SessionStore, TaskState


class ObservationContract(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", allow_inf_nan=False)


class DocumentRange(ObservationContract):
    start: int = Field(ge=0)
    end: int = Field(ge=0)


class ResearchDocument(ObservationContract):
    artifact_id: str = Field(min_length=1, max_length=100)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    ranges: tuple[DocumentRange, ...]


class ResearchObservation(ObservationContract):
    run_ref: str = Field(min_length=1, max_length=100)
    status: Literal["VERIFIED", "PARTIAL", "NONE", "UNKNOWN", "WITHHELD"]
    documents: tuple[ResearchDocument, ...] = ()
    verified_citations: int = Field(default=0, ge=0)
    reason_code: Literal[
        "MECHANICAL_CITATIONS_VERIFIED", "INCOMPLETE_RESEARCH_EVIDENCE",
        "NO_READ_DOCUMENTS", "RESEARCH_INCOMPLETE", "UNVERIFIED_RESEARCH_RUN",
    ]
    execution_authorization: Literal["NOT_GRANTED"] = "NOT_GRANTED"


def _withheld() -> ResearchObservation:
    return ResearchObservation(run_ref="withheld", status="WITHHELD",
                               reason_code="UNVERIFIED_RESEARCH_RUN")


class ResearchObservationStore:
    """按会话保存最多 20 个 run；只保存元数据，不保存正文。"""

    KEY = "research_observations"
    LIMIT = 20

    def __init__(self, sessions: SessionStore) -> None:
        if not isinstance(sessions, SessionStore):
            raise TypeError("研究观察需要SessionStore")
        self._sessions = sessions

    def start(self, context: ExecutionContext, parent_task: TaskState, *,
              budget_id: UUID | None, proposal_id: str | None) -> "ResearchRunCollector":
        if not isinstance(context, ExecutionContext):
            raise TypeError("研究观察需要ExecutionContext")
        if not isinstance(parent_task, TaskState):
            raise TypeError("研究观察需要父TaskState")
        collector = ResearchRunCollector(self, context, parent_task,
                                         budget_id=budget_id, proposal_id=proposal_id)
        collector.start()
        return collector

    def _load(self, context: ExecutionContext) -> list[dict]:
        value = self._sessions.extension(context, self.KEY, [])
        return list(value) if isinstance(value, list) else []

    def _append(self, context: ExecutionContext, record: dict) -> None:
        with self._sessions.control(context):
            records = self._load(context)
            records = [item for item in records
                       if isinstance(item, dict) and item.get("run_ref") != record["run_ref"]]
            records.append(record)
            records = records[-self.LIMIT:]
            self._sessions.set_extension(context, self.KEY, records)

    def _replace(self, context: ExecutionContext, record: dict) -> None:
        with self._sessions.control(context):
            records = self._load(context)
            found = False
            for index, item in enumerate(records):
                if isinstance(item, dict) and item.get("run_ref") == record["run_ref"]:
                    records[index] = record
                    found = True
                    break
            if not found:
                records.append(record)
                records = records[-self.LIMIT:]
            self._sessions.set_extension(context, self.KEY, records)

    def get(self, context: ExecutionContext, run_ref: str | None = None) -> ResearchObservation:
        try:
            # 观察必须基于数据库当前状态；会话缓存可能被另一个合法Store更新。
            snapshot = self._sessions.authoritative_snapshot(context)
            records = snapshot.extensions.get(self.KEY, [])
            if not isinstance(records, list):
                return _withheld()
        except Exception:
            return _withheld()
        record = None
        if run_ref is not None:
            record = next((item for item in records
                           if isinstance(item, dict) and item.get("run_ref") == str(run_ref)), None)
        else:
            # 只选择当前父 attempt 的最新记录；不能把旧任务当作当前证明。
            matched = [item for item in records if isinstance(item, dict)
                       and snapshot.task is not None
                       and item.get("parent_task_id") == str(snapshot.task.task_id)
                       and item.get("logical_task_id") == str(snapshot.task.logical_task_id)]
            record = matched[-1] if matched else None
        if record is None:
            return _withheld()
        if record.get("owner_id") != str(context.owner_id) or record.get("session_id") != str(context.session_id):
            return _withheld()
        if snapshot.task is None:
            return _withheld()
        if (record.get("parent_task_id") != str(snapshot.task.task_id)
                or record.get("logical_task_id") != str(snapshot.task.logical_task_id)
                or record.get("attempt_id") != str(snapshot.task.task_id)):
            return _withheld()
        if record.get("proposal_id") != (str(snapshot.work_state.confirmed_goal_id)
                                         if snapshot.work_state.confirmed_goal_id is not None else None):
            return _withheld()
        if record.get("budget_id") != (str(snapshot.active_budget_id)
                                       if snapshot.active_budget_id is not None else None):
            return _withheld()
        if record.get("status") == "WITHHELD" or not record.get("trusted", False):
            return _withheld()
        documents = self._documents(snapshot, record)
        if documents is None:
            return _withheld()
        status = record.get("status", "UNKNOWN")
        if not record.get("finished", False) and status != "UNKNOWN":
            status = "UNKNOWN"
        if status not in {"VERIFIED", "PARTIAL", "NONE", "UNKNOWN"}:
            return _withheld()
        reason = record.get("reason_code", "RESEARCH_INCOMPLETE")
        if reason not in {"MECHANICAL_CITATIONS_VERIFIED", "INCOMPLETE_RESEARCH_EVIDENCE",
                          "NO_READ_DOCUMENTS", "RESEARCH_INCOMPLETE"}:
            reason = "RESEARCH_INCOMPLETE"
        verified = record.get("verified_citations", 0)
        if type(verified) is not int or verified < 0:
            return _withheld()
        return ResearchObservation(
            run_ref=str(record["run_ref"]), status=status, documents=documents,
            verified_citations=verified,
            reason_code=reason,
        )

    def _documents(self, snapshot, record: dict):
        raw = record.get("documents", [])
        if not isinstance(raw, list):
            return None
        artifacts = snapshot.extensions.get("artifacts", {})
        if not isinstance(artifacts, dict):
            return None
        documents = []
        for entry in raw:
            if not isinstance(entry, dict):
                return None
            identity = str(entry.get("artifact_id"))
            digest = entry.get("sha256")
            start, end = entry.get("start"), entry.get("end")
            ranges = entry.get("ranges")
            if type(start) is not int or type(end) is not int or start < 0 or end < start:
                return None
            if not isinstance(digest, str) or len(digest) != 64:
                return None
            if not isinstance(ranges, list) or not ranges:
                return None
            try:
                item = artifacts.get(identity)
                if not isinstance(item, dict):
                    return None
                content = item["content"]
            except Exception:
                return None
            if not isinstance(content, str):
                return None
            if sha256(content.encode()).hexdigest() != digest or item.get("sha256") != digest:
                return None
            checked_ranges = []
            for span in ranges:
                if not isinstance(span, dict):
                    return None
                s, e = span.get("start"), span.get("end")
                if type(s) is not int or type(e) is not int or s < 0 or e < s or e > len(content):
                    return None
                checked_ranges.append(DocumentRange(start=s, end=e))
            if not checked_ranges or min(x.start for x in checked_ranges) != start or max(x.end for x in checked_ranges) != end:
                return None
            documents.append(ResearchDocument(
                artifact_id=identity, sha256=digest, start=start, end=end,
                ranges=tuple(sorted(checked_ranges, key=lambda x: (x.start, x.end))),
            ))
        return tuple(documents)


class ResearchRunCollector:
    """单次 ResearchService.run 的 collector；不读取模型文本，不签发权限。"""

    def __init__(self, store: ResearchObservationStore, context: ExecutionContext,
                 parent_task: TaskState, *, budget_id: UUID | None, proposal_id: str | None) -> None:
        self._store = store
        self._sessions = store._sessions
        self._context = context
        self._parent = parent_task
        self._budget_id = str(budget_id) if budget_id is not None else None
        self._proposal_id = str(proposal_id) if proposal_id is not None else None
        self.run_ref = str(uuid4())
        self._documents: dict[str, dict] = {}
        self._verified_citations = 0
        self._trusted = True
        self._finished = False
        self._invalid_citation = False
        self._read_failed = False
        self._record: dict | None = None

    def _base(self, status: str, reason: str) -> dict:
        return {
            "run_ref": self.run_ref, "owner_id": str(self._context.owner_id),
            "session_id": str(self._context.session_id),
            "parent_task_id": str(self._parent.task_id),
            "logical_task_id": str(self._parent.logical_task_id),
            "attempt_id": str(self._parent.task_id),
            "budget_id": self._budget_id, "proposal_id": self._proposal_id,
            "status": status, "reason_code": reason, "trusted": self._trusted,
            "finished": self._finished, "verified_citations": self._verified_citations,
            "documents": list(self._documents.values()),
            "created_at": datetime.now(UTC).isoformat(),
        }

    def start(self) -> None:
        self._record = self._base("UNKNOWN", "RESEARCH_INCOMPLETE")
        try:
            self._store._append(self._context, self._record)
        except Exception:
            self._trusted = False
            raise PermissionError("research_observation_unavailable") from None

    def _persist(self, status: str, reason: str) -> None:
        if not self._trusted:
            return
        self._record = self._base(status, reason)
        try:
            self._store._replace(self._context, self._record)
        except Exception:
            self._trusted = False

    def note_read(self, result: dict) -> None:
        try:
            if not isinstance(result, dict):
                raise ValueError("bad read result")
            identity = str(result["artifact_id"])
            start, end = result["start"], result["end"]
            if type(start) is not int or type(end) is not int:
                raise ValueError("read range types invalid")
            item = ArtifactStore(self._sessions, self._context).get(identity)
            content = item["content"]
            digest = sha256(content.encode()).hexdigest()
            if digest != item.get("sha256") or result.get("sha256") != digest:
                raise ValueError("artifact digest mismatch")
            if start < 0 or end < start or end > len(content):
                raise ValueError("range invalid")
            if content[start:end] != result.get("text"):
                raise ValueError("literal range mismatch")
            current = self._documents.get(identity)
            span = {"start": start, "end": end}
            if current is None:
                self._documents[identity] = {
                    "artifact_id": identity, "sha256": digest,
                    "start": start, "end": end, "ranges": [span],
                }
            else:
                if current["sha256"] != digest:
                    raise ValueError("artifact changed in run")
                spans = current["ranges"]
                if span not in spans:
                    spans.append(span)
                    spans.sort(key=lambda x: (x["start"], x["end"]))
                current["start"] = min(x["start"] for x in spans)
                current["end"] = max(x["end"] for x in spans)
            self._persist("UNKNOWN", "RESEARCH_INCOMPLETE")
        except Exception:
            self._trusted = False
            raise PermissionError("research_read_not_authentic") from None

    def note_read_failure(self) -> None:
        self._read_failed = True
        self._persist("WITHHELD", "UNVERIFIED_RESEARCH_RUN")

    def note_citation(self, artifact_id: str, start: int, end: int) -> bool:
        entry = self._documents.get(str(artifact_id))
        if entry is None or start < 0 or end <= start:
            self._invalid_citation = True
            return False
        if not any(span["start"] <= start and end <= span["end"] for span in entry["ranges"]):
            self._invalid_citation = True
            return False
        self._verified_citations += 1
        return True

    def finish(self, result: dict) -> None:
        if self._finished:
            return
        self._finished = True
        if not self._trusted:
            self._record = self._base("WITHHELD", "UNVERIFIED_RESEARCH_RUN")
            try:
                self._store._replace(self._context, self._record)
            except Exception:
                pass
            return
        if self._read_failed:
            self._persist("WITHHELD", "UNVERIFIED_RESEARCH_RUN")
            return
        status_value = result.get("status") if isinstance(result, dict) else None
        evidence = result.get("evidence") if isinstance(result, dict) else None
        citations = evidence.get("citations") if isinstance(evidence, dict) else None
        if status_value == "completed" and isinstance(citations, list) and citations:
            if self._invalid_citation or self._verified_citations != len(citations) or not self._documents:
                status, reason = "PARTIAL", "INCOMPLETE_RESEARCH_EVIDENCE"
            else:
                status, reason = "VERIFIED", "MECHANICAL_CITATIONS_VERIFIED"
        elif status_value in {"partial", "failed", "cancelled"}:
            status, reason = ("PARTIAL", "INCOMPLETE_RESEARCH_EVIDENCE") if self._documents else (
                "NONE", "NO_READ_DOCUMENTS")
        elif status_value == "completed":
            status, reason = ("PARTIAL", "INCOMPLETE_RESEARCH_EVIDENCE") if self._documents else (
                "NONE", "NO_READ_DOCUMENTS")
        else:
            status, reason = "UNKNOWN", "RESEARCH_INCOMPLETE"
        if self._documents and status == "UNKNOWN":
            status, reason = "PARTIAL", "INCOMPLETE_RESEARCH_EVIDENCE"
        self._persist(status, reason)

    def finish_error(self) -> None:
        if self._finished:
            return
        self._finished = True
        if self._read_failed or not self._trusted:
            self._persist("WITHHELD", "UNVERIFIED_RESEARCH_RUN")
        elif self._documents:
            self._persist("PARTIAL", "INCOMPLETE_RESEARCH_EVIDENCE")
        else:
            self._persist("UNKNOWN", "RESEARCH_INCOMPLETE")

    def invalidate(self, reason: str = "stale_goal_version") -> None:
        if not self._finished:
            self._finished = True
        if self._documents:
            self._persist("PARTIAL", "INCOMPLETE_RESEARCH_EVIDENCE")
        else:
            self._persist("NONE", "NO_READ_DOCUMENTS")
