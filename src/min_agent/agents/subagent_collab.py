"""MA-P5-005 / MA-P5-013：协作执行地基（**默认关闭、不接主循环、模块内不主动发网络请求**）。

本模块补的是「功能」，不是测试。它把三件此前只在一次性实验脚本里出现的能力
固化成可复用接口：

1. **用例定义与后端解耦**：同一份 :class:`CaseSet` 可以由离线后端
   （:class:`ScriptTransport`，脚本回复、0 次模型调用）或在线后端
   （:class:`HttpChatTransport`，真实模型）执行，产出**同结构**结果。
   ``CaseSet.fingerprint()`` 让以后「离线/在线是否用同一测试集」可机械核对。
2. **模型驱动的提议者**：:class:`ModelProposer` 由模型产出 ``artifact`` 与
   ``declared_complete``；证据引用、检查项绑定、回执 SHA-256 **仍由可信 host
   从工具轨迹构造**，模型不自报（沿用 G2/H2/J3）。``completed_checks`` 刻意留空，
   用来证明自报不参与判定。
3. **独立信息源的审核者**：:class:`IndependentEvidenceSource` 让审核者读与提议者
   通道分离的**原始字节**，而不是提议者的转述；:class:`ModelReviewer` 的意见只写入
   并列语义层，:func:`combine_verdicts` 保证它不能覆盖确定性 FAIL、不能把
   UNKNOWN 抬成 PASS、也不能单独宣布 PASS。

边界（不得当作已实现）：

* 默认 off：不注册任何生产入口，不修改 ``application.py`` / ``runtime.py``；
  ``subagent_review.py`` 的判定逻辑一字未改。
* ``HttpChatTransport`` 只实现、不在本模块内调用；API key 只由调用方注入参数，
  模块不读 ``.env``、不写日志、不进异常文本。
* **MA-P5-013 起独立性是机械公式，不是措辞**（DECISION_LOG → MA-P5-013 D5）::

      model_different = (双方 vendor 已知且不同) OR (双方 model_id 已知且不同)
      evidence_source_different = 至少一侧有真实原始字节
                                  AND (字节 SHA-256 集合不同 OR 双方 channel 已知且不同)
      independent_evidence = model_different AND evidence_source_different

  任一必要元数据未知时**不得猜为不同**：``independent_evidence`` 保持 ``False``，
  ``source_comparison`` 记 ``"unknown"``。``same_source_as_proposer`` 改为比较
  **原始字节 SHA-256 集合 + channel**，不再只比引用字符串；``source_comparison``
  用来区分「已证明相同」「已证明不同」「无法判定」三种情况，避免把
  ``same_source=False`` 误读成「已证明来源不同」。**channel 不同但字节完全相同**
  时机械定义判为来源不同，但说明里会写明「没有证明内容信息增量」。
* 语义质量仍固定 UNKNOWN；本模块不负责从真实工具执行生成轨迹，``ToolTrace``
  由可信调用方提供。不做并行、不抢占、不递归委派。
* ``ChatResult.tool_calls`` 只是**数据**：本模块不执行工具、不做白名单校验，
  执行与校验由调用方在预算与契约内完成（生产路径见
  :mod:`min_agent.agents.multi_agent_service`）。
"""

from __future__ import annotations

import json
import re
import threading
from hashlib import sha256
from typing import Callable, Literal, Mapping, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field

from min_agent.contracts.models import ToolCall
from min_agent.agents.subagent import EvidenceRef, receipt_ref
from min_agent.agents.subagent_review import (
    HostChecks, LoopResult, ProposerOutput, ReviewSpec, ReviewVerdict,
    review_loop,
)

__all__ = [
    "ChatResult", "ChatTransport", "ScriptTransport", "HttpChatTransport",
    "ToolCallRecord", "CollabCase", "CaseSet", "HostArtifacts",
    "build_host_artifacts", "ModelProposer", "ProposerUnavailable",
    "IndependentEvidenceSource", "ModelReviewer", "combine_verdicts",
    "SourceComparison", "compare_sources", "evaluate_independence",
    "run_case", "run_case_set",
]

BackendMode = Literal["offline", "online"]
FinishReason = Literal["stop", "tool_calls", "length", "error"]
SourceComparison = Literal["same", "different", "unknown"]


class _Strict(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class ChatResult(_Strict):
    """一次模型调用的脱敏结果；不含凭据、不含完整推理过程。"""

    content: str = ""
    finish_reason: FinishReason = "stop"
    http_status: int | None = None
    usage: dict[str, int] | None = None
    error: str | None = None
    # MA-P5-013：工具请求只是数据，本模块不执行、不校验白名单。
    tool_calls: tuple[ToolCall, ...] = ()


class ChatTransport(Protocol):
    """可替换的执行后端。离线/在线实现必须返回同结构的 :class:`ChatResult`。

    MA-P5-013 增加两项能力：

    * ``vendor`` / ``model_id``：**独立性判定所需的元数据**。离线脚本一律
      ``None``（不冒充真实模型）；在线后端的 ``vendor`` 必须由可信调用方显式声明，
      未知就留 ``None``——独立性公式对未知一律按「非独立」处理。
    * ``complete(..., *, tools=None)``：第三个参数是 keyword-only 且默认 ``None``，
      **旧的两参数调用保持可用**；消息字典只要求 ``role`` / ``content``，assistant
      消息可带 ``tool_calls``、tool 消息可带 ``tool_call_id``。
    """

    @property
    def mode(self) -> BackendMode: ...

    @property
    def calls(self) -> int: ...

    @property
    def vendor(self) -> str | None: ...

    @property
    def model_id(self) -> str | None: ...

    def complete(self, messages: tuple[dict[str, object], ...],
                 max_tokens: int, *,
                 tools: list[dict[str, object]] | None = None) -> ChatResult: ...


class ScriptTransport:
    """离线后端：按序返回固定回复，0 次模型调用、0 次网络请求。

    ``vendor`` / ``model_id`` 恒为 ``None``：脚本不是模型，**不冒充真实模型**，
    因此在独立性公式里永远拿不到 ``model_different``。
    """

    def __init__(self, replies: Sequence[str], *, name: str = "script") -> None:
        self._replies = tuple(replies)
        self._index = 0
        self._name = name
        self._calls = 0
        self._lock = threading.Lock()

    @property
    def mode(self) -> BackendMode:
        return "offline"

    @property
    def calls(self) -> int:
        return self._calls

    @property
    def vendor(self) -> str | None:
        return None

    @property
    def model_id(self) -> str | None:
        return None

    @property
    def name(self) -> str:
        return self._name

    def complete(self, messages: tuple[dict[str, object], ...],
                 max_tokens: int, *,
                 tools: list[dict[str, object]] | None = None) -> ChatResult:
        with self._lock:  # 并行下同一实例可能被多个线程使用
            self._calls += 1
            if self._index >= len(self._replies):
                return ChatResult(finish_reason="error",
                                  error="script_replies_exhausted")
            text = self._replies[self._index]
            self._index += 1
        return ChatResult(content=text, finish_reason="stop",
                          usage={"prompt_tokens": 0, "completion_tokens": 0})


class HttpChatTransport:
    """在线后端：OpenAI 兼容 Chat Completions。**构造不发请求**，只在 ``complete`` 时发。

    ``api_key`` 只由调用方注入；模块不读环境变量、不打印、不写入任何结果对象，
    异常文本中的 key 一律替换为 ``<redacted>``。本模块本身不调用此实现。

    ``vendor`` 必须由**可信调用方显式声明**；不清楚就留 ``None``（保守判非独立）。
    """

    def __init__(self, *, base_url: str, model: str, api_key: str,
                 timeout: float = 30.0, vendor: str | None = None) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("在线端点必须使用 HTTPS")
        if not api_key:
            raise ValueError("api_key 必须由调用方显式注入")
        if vendor is not None and (not isinstance(vendor, str) or not vendor.strip()):
            raise ValueError("vendor 必须是非空白字符串或 None")
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout = timeout
        self._vendor = vendor.strip() if vendor is not None else None
        self._calls = 0
        self._lock = threading.Lock()

    @property
    def mode(self) -> BackendMode:
        return "online"

    @property
    def calls(self) -> int:
        return self._calls

    @property
    def vendor(self) -> str | None:
        return self._vendor

    @property
    def model_id(self) -> str | None:
        return self._model or None

    def redacted_config(self) -> dict[str, object]:
        return {"base_url": self._base_url, "model": self._model,
                "timeout": self._timeout, "mode": self.mode,
                "vendor": self._vendor, "api_key": "<redacted>"}

    def complete(self, messages: tuple[dict[str, object], ...],
                 max_tokens: int, *,
                 tools: list[dict[str, object]] | None = None) -> ChatResult:
        with self._lock:
            self._calls += 1
        try:
            import httpx
        except ImportError:
            return ChatResult(finish_reason="error", error="httpx_unavailable")
        payload: dict[str, object] = {
            "model": self._model, "messages": list(messages),
            "max_tokens": max_tokens, "stream": False,
        }
        if tools:
            payload["tools"] = list(tools)
        try:
            with httpx.Client(timeout=self._timeout, trust_env=True) as client:
                response = client.post(
                    self._base_url + "/chat/completions",
                    headers={"Authorization": f"Bearer {self._api_key}",
                             "Content-Type": "application/json"},
                    json=payload,
                )
        except Exception as error:  # 网络异常不把 key 带进文本
            return ChatResult(finish_reason="error", error=type(error).__name__)
        if response.status_code != 200:
            excerpt = response.text[:200].replace(self._api_key, "<redacted>")
            return ChatResult(finish_reason="error",
                              http_status=response.status_code, error=excerpt)
        try:
            body = response.json()
            choices = body.get("choices") or []
            first = choices[0]
            content = (first.get("message") or {}).get("content") or ""
            raw_finish = first.get("finish_reason") or "stop"
            usage = body.get("usage")
            raw_calls = (first.get("message") or {}).get("tool_calls") or []
        except (ValueError, AttributeError, IndexError, TypeError):
            return ChatResult(finish_reason="error", error="response_shape",
                              http_status=response.status_code)
        finish: FinishReason = (
            "length" if raw_finish == "length"
            else "tool_calls" if raw_finish == "tool_calls"
            else "stop"
        )
        safe_usage = {k: v for k, v in usage.items()
                      if isinstance(k, str) and isinstance(v, int)} if isinstance(usage, dict) else None
        return ChatResult(content=str(content), finish_reason=finish,
                          http_status=response.status_code, usage=safe_usage,
                          tool_calls=_parse_tool_calls(raw_calls))


def _parse_tool_calls(raw: object) -> tuple[ToolCall, ...]:
    """只保留形状完整的调用；形状不对就整条丢弃，不猜参数。

    ``arguments`` 必须是 JSON 对象字符串——与生产 ``ToolCall.arguments`` 的类型一致。
    返回空元组表示「这次没有可用调用」，调用方必须把 ``finish_reason=tool_calls``
    但有非法调用的响应当作错误，而不是当作正常结束。
    """
    if not isinstance(raw, list):
        return ()
    parsed: list[ToolCall] = []
    for item in raw:
        if not isinstance(item, dict):
            return ()
        function = item.get("function")
        if not isinstance(function, dict):
            return ()
        call_id = item.get("id")
        name = function.get("name")
        raw_args = function.get("arguments")
        if not isinstance(call_id, str) or not isinstance(name, str):
            return ()
        if not isinstance(raw_args, str):
            return ()
        try:
            arguments = json.loads(raw_args)
        except ValueError:
            return ()
        if not isinstance(arguments, dict):
            return ()
        parsed.append(ToolCall(id=call_id, name=name, arguments=arguments))
    return tuple(parsed)


class ToolCallRecord(_Strict):
    """可信 host 记录的一次真实工具执行；``check_name`` 是它支撑的检查项。"""

    tool: str = Field(min_length=1, max_length=64)
    raw_receipt: bytes
    check_name: str = Field(min_length=1, max_length=64)


class CollabCase(_Strict):
    """与后端无关的单个用例定义：**离线和在线必须共用同一份**。"""

    case_id: str = Field(min_length=1, max_length=64)
    objective: str = Field(min_length=1, max_length=2000)
    prompt: str = Field(min_length=1, max_length=4000)
    spec: ReviewSpec
    tool_calls: tuple[ToolCallRecord, ...] = ()


class CaseSet(_Strict):
    """冻结用例集合；``fingerprint()`` 用于核对两次运行是否用了同一测试集。"""

    name: str = Field(min_length=1, max_length=128)
    cases: tuple[CollabCase, ...]

    def case_ids(self) -> tuple[str, ...]:
        return tuple(case.case_id for case in self.cases)

    def fingerprint(self) -> str:
        payload = [
            {
                "case_id": case.case_id,
                "objective": case.objective,
                "prompt": case.prompt,
                "spec": case.spec.model_dump(),
                "tool_calls": [
                    {"tool": call.tool, "check_name": call.check_name,
                     "receipt_ref": receipt_ref(call.raw_receipt)}
                    for call in case.tool_calls
                ],
            }
            for case in self.cases
        ]
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return sha256(raw).hexdigest()


class HostArtifacts(_Strict):
    """由可信 host 从工具轨迹生成的证据，模型无法伪造。"""

    raw_receipts: dict[str, bytes]
    host_checks: dict[str, str]
    evidence_refs: tuple[EvidenceRef, ...]
    receipt_refs: tuple[str, ...]


def build_host_artifacts(case: CollabCase) -> HostArtifacts:
    """把工具轨迹折叠成原始回执表、检查项绑定与证据引用。"""
    raw: dict[str, bytes] = {}
    checks: dict[str, str] = {}
    refs: list[EvidenceRef] = []
    for call in case.tool_calls:
        ref = receipt_ref(call.raw_receipt)
        raw[ref] = call.raw_receipt
        checks[call.check_name] = ref
        refs.append(EvidenceRef(kind="tool_receipt", ref=ref))
    return HostArtifacts(
        raw_receipts=raw, host_checks=checks,
        evidence_refs=tuple(refs), receipt_refs=tuple(sorted(raw)),
    )


class ProposerUnavailable(RuntimeError):
    """模型调用失败；由 ``review_loop`` 捕获为 UNKNOWN，不伪装成已完成。"""


class ModelProposer:
    """实现 ``Proposer`` 契约：模型只产正文与「是否截断」，其余由 host 构造。

    ``completed_checks`` 刻意留空，因为 MA-P5-004 起该字段不参与判定；
    保留它只是为了证明「模型自述不能制造 PASS」。
    """

    def __init__(self, transport: ChatTransport, case: CollabCase,
                 artifacts: HostArtifacts, *, max_tokens: int = 256) -> None:
        self._transport = transport
        self._case = case
        self._artifacts = artifacts
        self._max_tokens = max_tokens
        self.last_artifact = ""

    def __call__(self, round_no: int,
                feedback: ReviewVerdict | None) -> ProposerOutput:
        receipt_text = "\n".join(
            value.decode("utf-8", errors="replace")
            for value in self._artifacts.raw_receipts.values()
        )
        messages: tuple[dict[str, str], ...] = (
            {"role": "system", "content":
                "你是提议者。依据给定的工具回执给出简短最终答案，不要编造未出现的数值。"
                "只输出答案短句，不要输出证据引用或检查项。"},
            {"role": "user", "content":
                f"任务：{self._case.prompt}\n工具回执：{receipt_text}\n"
                f"轮次：{round_no}\n请给出最终答案。"},
        )
        if feedback is not None:
            # 只回传结构化结论，绝不回传思考过程或消息历史。
            messages += (
                {"role": "user", "content":
                    f"上一轮审核结论：{feedback.outcome} / {feedback.reason_code}；"
                    f"缺失检查项：{list(feedback.missing_checks)}"},
            )
        result = self._transport.complete(messages, self._max_tokens)
        if result.finish_reason == "error" or result.error:
            raise ProposerUnavailable(result.error or "model_call_failed")
        self.last_artifact = result.content.strip() or "(模型未返回正文)"
        return ProposerOutput(
            artifact=self.last_artifact,
            declared_complete=(result.finish_reason == "stop" and bool(result.content.strip())),
            completed_checks=(),
            evidence_refs=self._artifacts.evidence_refs,
            receipt_refs=self._artifacts.receipt_refs,
            stop_reason="done" if result.finish_reason == "stop" else "budget",
        )


class IndependentEvidenceSource:
    """审核者的独立信息通道：只读原始字节，不读提议者的转述。

    ``channel`` 用于如实标注信息源。**通道分离不等于独立证据**：若与提议者
    共用同一模型/上下文，仍须标注「非独立证据」（DECISION_LOG H5）。
    """

    def __init__(self, raw_receipts: Mapping[str, bytes], *,
                 channel: str = "tool_host") -> None:
        self._raw = dict(raw_receipts)
        self._channel = channel

    @property
    def channel(self) -> str:
        return self._channel

    def read(self) -> dict[str, bytes]:
        return dict(self._raw)

    def refs(self) -> tuple[str, ...]:
        return tuple(sorted(self._raw))


def _parse_opinion(text: str) -> dict[str, str]:
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(),
                     flags=re.MULTILINE).strip()
    try:
        parsed = json.loads(cleaned)
    except ValueError:
        return {"consistency": "unknown", "reason": "unparseable_reviewer_output"}
    if not isinstance(parsed, dict):
        return {"consistency": "unknown", "reason": "reviewer_output_not_object"}
    consistency = parsed.get("consistency")
    if consistency not in ("pass", "fail", "unknown"):
        consistency = "unknown"
    return {"consistency": consistency,
            "reason": str(parsed.get("reason", ""))[:200]}


def _content_hashes(receipts: Mapping[str, bytes] | None) -> frozenset[str]:
    """按**真实字节**算 SHA-256 集合；不看调用方给的 key 或引用字符串。"""
    if not receipts:
        return frozenset()
    return frozenset(
        sha256(value).hexdigest()
        for value in receipts.values() if isinstance(value, bytes)
    )


def _clean_label(value: str | None) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def compare_sources(*, proposer_receipts: Mapping[str, bytes] | None,
                    proposer_channel: str | None,
                    reviewer_receipts: Mapping[str, bytes] | None,
                    reviewer_channel: str | None) -> dict[str, object]:
    """比**原始字节 SHA-256 集合 + channel**，并与「是不是同一来源」分开表达。

    ``source_comparison`` 三态：

    * ``"different"``——字节集合不同，或双方 channel 已知且不同（满足机械定义）；
    * ``"same"``——字节集合相同**且**双方 channel 已知且相同；
    * ``"unknown"``——没有任何真实字节，或字节相同但 channel 信息不全。

    只有 ``"different"`` 才让 ``evidence_source_different`` 为真。
    """
    proposer_hashes = _content_hashes(proposer_receipts)
    reviewer_hashes = _content_hashes(reviewer_receipts)
    has_bytes = bool(proposer_hashes) or bool(reviewer_hashes)
    left = _clean_label(proposer_channel)
    right = _clean_label(reviewer_channel)
    channels_known = left is not None and right is not None
    channels_different = channels_known and left != right
    hashes_different = proposer_hashes != reviewer_hashes
    identical_bytes = bool(proposer_hashes) and proposer_hashes == reviewer_hashes

    different = bool(has_bytes) and (hashes_different or channels_different)
    if not has_bytes:
        comparison: SourceComparison = "unknown"
    elif different:
        comparison = "different"
    elif identical_bytes and channels_known and not channels_different:
        comparison = "same"
    else:
        comparison = "unknown"
    return {
        "source_comparison": comparison,
        "evidence_source_different": different,
        "same_source": comparison == "same",
        "identical_content": identical_bytes,
        "proposer_content_hashes": sorted(proposer_hashes),
        "reviewer_content_hashes": sorted(reviewer_hashes),
        "proposer_channel": left,
        "reviewer_channel": right,
    }


def evaluate_independence(*, proposer_vendor: str | None,
                          proposer_model_id: str | None,
                          reviewer_vendor: str | None,
                          reviewer_model_id: str | None,
                          evidence_source_different: bool) -> dict[str, object]:
    """机械判据：``model_different AND evidence_source_different``。

    任一必要元数据未知时**不猜为不同**——未知一律落回「非独立」。
    """
    pv, rv = _clean_label(proposer_vendor), _clean_label(reviewer_vendor)
    pm, rm = _clean_label(proposer_model_id), _clean_label(reviewer_model_id)
    vendor_known = pv is not None and rv is not None
    model_known = pm is not None and rm is not None
    vendor_different = vendor_known and pv != rv
    model_different = model_known and pm != rm
    different = bool(vendor_different or model_different)
    if different:
        model_comparison = "different"
    elif vendor_known and model_known:
        model_comparison = "same"
    else:
        model_comparison = "unknown"
    return {
        "model_different": different,
        "model_comparison": model_comparison,
        "vendor": {"proposer": pv, "reviewer": rv},
        "model_id": {"proposer": pm, "reviewer": rm},
        "evidence_source_different": bool(evidence_source_different),
        "independent_evidence": bool(different and evidence_source_different),
    }


class ModelReviewer:
    """并列语义层：只看验收规格、独立原始回执、最终交付与确定性结论。

    MA-P5-013 起它同时给出**可复核的独立性判定**，不再硬编码 ``False``。
    """

    def __init__(self, transport: ChatTransport,
                 source: IndependentEvidenceSource, *, max_tokens: int = 512) -> None:
        self._transport = transport
        self._source = source
        self._max_tokens = max_tokens

    def review(self, spec: ReviewSpec, artifact: str,
               verdict: ReviewVerdict | None, *,
               proposer_refs: tuple[str, ...] = (),
               proposer_receipts: Mapping[str, bytes] | None = None,
               proposer_channel: str | None = None,
               proposer_vendor: str | None = None,
               proposer_model_id: str | None = None) -> dict[str, object]:
        """``proposer_refs`` 仅为签名兼容保留：**不再参与判定**（引用字符串可伪造）。

        真正用于比较的是调用方传入的 ``proposer_receipts`` 原始字节，以及两侧
        各自声明的 ``channel`` / ``vendor`` / ``model_id``。
        """
        reviewer_receipts = self._source.read()
        comparison = compare_sources(
            proposer_receipts=proposer_receipts,
            proposer_channel=proposer_channel,
            reviewer_receipts=reviewer_receipts,
            reviewer_channel=self._source.channel,
        )
        independence = evaluate_independence(
            proposer_vendor=proposer_vendor,
            proposer_model_id=proposer_model_id,
            reviewer_vendor=self._transport.vendor,
            reviewer_model_id=self._transport.model_id,
            evidence_source_different=bool(comparison["evidence_source_different"]),
        )
        receipts = "\n".join(
            value.decode("utf-8", errors="replace")
            for value in reviewer_receipts.values()
        )
        prompt = (
            "You are an independent reviewer. You receive ONLY the verification "
            "criteria, the raw tool receipts, and the final answer. You do NOT see "
            "any prior reasoning or conversation history. Decide whether the final "
            "answer is supported by the raw receipts.\n"
            'Reply with strict JSON only: {"consistency":"pass|fail|unknown","reason":"short"}\n\n'
            f"Criteria: answer must contain {list(spec.required_markers)} and be "
            f"based on the receipts. Required checks: {list(spec.required_checks)}.\n"
            f"Raw receipts: {receipts}\nFinal answer: {artifact}\n"
        )
        result = self._transport.complete(
            ({"role": "user", "content": prompt},), self._max_tokens)
        if result.finish_reason == "error" or result.error:
            opinion = {"consistency": "unknown", "reason": result.error or "reviewer_error"}
        else:
            opinion = _parse_opinion(result.content)
        notes: list[str] = []
        if not independence["independent_evidence"]:
            if independence["model_comparison"] == "same":
                notes.append("同模型/同上下文不构成独立证据")
            elif independence["model_comparison"] == "unknown":
                notes.append("模型元数据未知，不得猜为不同模型")
            if not comparison["evidence_source_different"]:
                notes.append("证据来源无法证明不同")
        else:
            notes.append("机械独立判据成立；语义质量仍固定 UNKNOWN")
        if comparison["source_comparison"] == "different" and comparison["identical_content"]:
            notes.append("channel 不同但字节完全相同，没有证明内容信息增量")
        return {
            "channel": self._source.channel,
            "backend": self._transport.mode,
            "opinion": opinion,
            "http_status": result.http_status,
            "usage": result.usage,
            "same_source_as_proposer": comparison["same_source"],
            "source_comparison": comparison["source_comparison"],
            "proposer_channel": comparison["proposer_channel"],
            "proposer_content_hashes": comparison["proposer_content_hashes"],
            "reviewer_content_hashes": comparison["reviewer_content_hashes"],
            "model_comparison": independence["model_comparison"],
            "vendor": independence["vendor"],
            "model_id": independence["model_id"],
            "independent_evidence": independence["independent_evidence"],
            "independent_evidence_note": "；".join(notes),
            "deterministic_outcome_seen": verdict.outcome if verdict else None,
        }


def combine_verdicts(loop: LoopResult,
                     reviewer: Mapping[str, object] | None) -> str:
    """硬门优先：模型意见不能覆盖 FAIL、不能把 UNKNOWN 抬成 PASS。"""
    if loop.outcome == "FAIL":
        return "FAIL"
    if loop.outcome == "UNKNOWN":
        return "UNKNOWN"
    if reviewer is None:
        return "PASS"
    opinion = reviewer.get("opinion")
    if isinstance(opinion, Mapping) and opinion.get("consistency") == "fail":
        return "PASS_WITH_SEMANTIC_DISPUTE"
    return "PASS"


def _independence_summary(review: Mapping[str, object]) -> dict[str, object]:
    """从审核结果里抽出独立性结论；**不含**任何回执正文。"""
    return {
        "independent_evidence": review.get("independent_evidence"),
        "model_comparison": review.get("model_comparison"),
        "source_comparison": review.get("source_comparison"),
        "note": review.get("independent_evidence_note"),
    }


def run_case(case: CollabCase, proposer_transport: ChatTransport, *,
             reviewer_transport: ChatTransport | None = None,
             reviewer_source: IndependentEvidenceSource | None = None,
             case_set_fingerprint: str = "",
             proposer_channel: str = "tool_host",
             reviewer_channel: str | None = None) -> dict[str, object]:
    """同一用例在任意后端下的统一执行入口；离线/在线产出同结构结果。

    ``proposer_channel`` / ``reviewer_channel`` 是**通道标识**，用于独立性机械判定；
    默认审核者与提议者读同一份 host 回执、同一通道，所以默认结果是「来源相同」，
    ``independent_evidence=false``——这是如实标注，不是缺陷。
    """
    artifacts = build_host_artifacts(case)
    proposer = ModelProposer(proposer_transport, case, artifacts)
    loop = review_loop(case.spec, proposer, artifacts.raw_receipts,
                       host_checks=artifacts.host_checks)
    reviewer = None
    if reviewer_transport is not None:
        source = reviewer_source or IndependentEvidenceSource(
            artifacts.raw_receipts,
            channel=reviewer_channel or proposer_channel)
        reviewer = ModelReviewer(reviewer_transport, source).review(
            case.spec, proposer.last_artifact, loop.final_verdict,
            proposer_refs=artifacts.receipt_refs,
            proposer_receipts=artifacts.raw_receipts,
            proposer_channel=reviewer_channel or proposer_channel,
            proposer_vendor=proposer_transport.vendor,
            proposer_model_id=proposer_transport.model_id)
    final = loop.final_verdict.model_dump() if loop.final_verdict else None
    return {
        "case_id": case.case_id,
        "backend": proposer_transport.mode,
        "proposer_vendor": proposer_transport.vendor,
        "proposer_model_id": proposer_transport.model_id,
        "case_set_fingerprint": case_set_fingerprint,
        "deterministic": {
            "outcome": loop.outcome, "reason_code": loop.reason_code,
            "rounds": loop.rounds, "final_verdict": final,
        },
        "semantic_review": reviewer,
        "independence": _independence_summary(reviewer) if reviewer else None,
        "combined": combine_verdicts(loop, reviewer),
        "model_calls": proposer_transport.calls,
        "artifact_present": bool(proposer.last_artifact),
    }


def run_case_set(case_set: CaseSet, proposer_transport: ChatTransport, *,
                 reviewer_transport: ChatTransport | None = None,
                 reviewer_source: IndependentEvidenceSource | None = None,
                 reviewer_source_factory:
                     Callable[[CollabCase], IndependentEvidenceSource] | None = None,
                 proposer_channel: str = "tool_host",
                 reviewer_channel: str | None = None) -> dict[str, object]:
    """整份用例集跑一个后端；换后端时 ``fingerprint`` 与 ``case_ids`` 必须一致。

    ``reviewer_source`` 会**逐个用例**透传给 :func:`run_case`。若传的是固定对象，
    它对每个用例都读到同一份回执——只有该对象本来就是按用例构造时才成立；
    需要按用例构造时用 ``reviewer_source_factory``（优先于固定对象）。
    """
    fingerprint = case_set.fingerprint()
    results = []
    for case in case_set.cases:
        source = (reviewer_source_factory(case)
                  if reviewer_source_factory is not None else reviewer_source)
        results.append(run_case(
            case, proposer_transport,
            reviewer_transport=reviewer_transport,
            reviewer_source=source,
            case_set_fingerprint=fingerprint,
            proposer_channel=proposer_channel,
            reviewer_channel=reviewer_channel,
        ))
    return {
        "case_set": case_set.name,
        "fingerprint": fingerprint,
        "case_ids": list(case_set.case_ids()),
        "backend": proposer_transport.mode,
        "reviewer_backend": reviewer_transport.mode if reviewer_transport else None,
        "reviewer_source": _reviewer_source_metadata(results, reviewer_transport),
        "case_count": len(results),
        "results": results,
        "limits": [
            "用例定义与后端解耦；离线/在线必须共用同一 fingerprint",
            "证据链由可信 host 从工具轨迹构造，模型自报不参与判定",
            "模型审核意见是并列语义层，不能覆盖确定性 FAIL 或抬升 UNKNOWN",
            "独立性是机械判据：模型元数据未知时不得猜为不同，语义质量仍 UNKNOWN",
            "默认 off、未接生产主链；本模块不主动发起任何网络请求",
        ],
    }


_MAX_HASHES_IN_METADATA = 64


def _reviewer_source_metadata(results: Sequence[Mapping[str, object]],
                              reviewer_transport: ChatTransport | None
                              ) -> dict[str, object] | None:
    """脱敏的审核者来源元数据：channel、内容哈希集合、vendor/model_id。

    **不回原始回执正文**，也不回审核者提示词；哈希集合只用于核对「两次运行是否
    读到了同一批字节」，本身不能反推内容。
    """
    if reviewer_transport is None:
        return None
    channels: set[str] = set()
    reviewer_hashes: set[str] = set()
    proposer_hashes: set[str] = set()
    comparisons: set[str] = set()
    independent = 0
    for row in results:
        review = row.get("semantic_review")
        if not isinstance(review, Mapping):
            continue
        channel = review.get("channel")
        if isinstance(channel, str):
            channels.add(channel)
        for key, bucket in (("reviewer_content_hashes", reviewer_hashes),
                            ("proposer_content_hashes", proposer_hashes)):
            values = review.get(key)
            if isinstance(values, list):
                bucket.update(item for item in values if isinstance(item, str))
        comparison = review.get("source_comparison")
        if isinstance(comparison, str):
            comparisons.add(comparison)
        if review.get("independent_evidence") is True:
            independent += 1
    return {
        "channels": sorted(channels),
        "source_comparisons": sorted(comparisons),
        "reviewer_content_hashes": sorted(reviewer_hashes)[:_MAX_HASHES_IN_METADATA],
        "reviewer_content_hash_count": len(reviewer_hashes),
        "proposer_content_hashes": sorted(proposer_hashes)[:_MAX_HASHES_IN_METADATA],
        "proposer_content_hash_count": len(proposer_hashes),
        "vendor": reviewer_transport.vendor,
        "model_id": reviewer_transport.model_id,
        "backend": reviewer_transport.mode,
        "cases_with_independent_evidence": independent,
        "case_count": len(results),
    }
