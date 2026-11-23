"""真实模型层烟雾测试：用假模型走通 HarnessApplication，不调任何 API。

假模型只按题面做最直接的一次调用，证明 real_runner 的
submit -> wait -> snapshot -> 配对 -> verify 链路能走通。
它不证明真模型会选对工具。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from min_agent.contracts.models import Message, ModelResponse, ToolCall

from eval.real_runner import run_real_suite  # noqa: E402


class StubModel:
    """第一轮按 prompt 调一次工具，第二轮停下。"""

    def __init__(self):
        self._requests = 0
        self.usage = []
        self._settings = type("S", (), {"max_output_tokens": 1024})()

    def context_limits(self):
        from min_agent.context.model_context import ModelContextLimits
        return ModelContextLimits(model="stub", context_window=32768,
                                  max_output_tokens=1024, source="stub")

    def generate(self, messages, *, tools=None):
        self._requests += 1
        called = [c.name for m in messages for c in (m.tool_calls or [])]
        if called:
            return ModelResponse(finish_reason="stop", content="已按工具结果处理。")
        prompt = next((m.content for m in messages if m.role == "user"), "")
        if ".env" in prompt:
            call = ToolCall(id="stub-1", name="read", arguments={"path": ".env"})
        else:
            call = ToolCall(id="stub-1", name="read",
                            arguments={"path": "notes.txt", "offset": 2, "limit": 2})
        return ModelResponse(finish_reason="tool_calls", tool_calls=[call])

    @property
    def request_count(self):
        return self._requests

    def close(self):
        pass


def test_real_runner_with_stub_model():
    summary = run_real_suite(["C1", "S1"], lambda: StubModel(), repeat=1)
    by_id = {r["task_id"]: r for r in summary["results"]}
    assert by_id["C1"]["verdict"] == "PASS", by_id["C1"]
    assert by_id["S1"]["verdict"] == "PASS", by_id["S1"]
    assert summary["model_requests"] == 4  # 每题 2 次模型请求
