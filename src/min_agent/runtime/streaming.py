"""会话级流式文本缓冲与模型包装；只保存增量正文，不保存指令或凭据。

设计边界：流式是**显示通道**，Runtime 仍然拿到完整的 ModelResponse；
工具调用、预算、trace 的语义与之前完全一致。
"""

from threading import Condition


class StreamSink:
    """按会话累积增量文本，供 SSE 端点按游标读取。"""

    def __init__(self, limit: int = 64 * 1024) -> None:
        if type(limit) is not int or limit <= 0:
            raise ValueError("上限无效")
        self._chunks: list[str] = []
        self._size = 0
        self._limit = limit
        self._closed = False
        self._condition = Condition()
        self.overflow = False

    def push(self, text) -> None:
        """模型每产出一段就调用；超过上限后停止显示，但不影响最终响应。"""
        if not isinstance(text, str) or not text:
            return
        with self._condition:
            if self._closed:
                return
            if self._size + len(text) > self._limit:
                self.overflow = True
                return
            self._chunks.append(text)
            self._size += len(text)
            self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def read(self, cursor: int, timeout: float = 10.0):
        """返回 (新片段, 新游标, 是否已结束)；没有新内容时最多等 timeout 秒。"""
        if type(cursor) is not int or cursor < 0:
            raise ValueError("游标无效")
        with self._condition:
            if cursor >= len(self._chunks) and not self._closed:
                self._condition.wait(timeout)
            return self._chunks[cursor:], len(self._chunks), self._closed


class StreamingModel:
    """把支持 generate_stream 的适配器包装成 Runtime 需要的 generate 接口。"""

    def __init__(self, inner, sink: StreamSink) -> None:
        if not callable(getattr(inner, "generate_stream", None)):
            raise TypeError("模型不支持流式生成")
        if not isinstance(sink, StreamSink):
            raise TypeError("sink 必须是 StreamSink")
        self._inner = inner
        self._sink = sink

    def generate(self, messages, *, tools=None):
        return self._inner.generate_stream(messages, tools=tools, on_delta=self._sink.push)

    def __getattr__(self, name):
        # 转发 _provider / _settings / context_limits / usage 等只读属性。
        return getattr(self._inner, name)