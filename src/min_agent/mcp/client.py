"""最小 MCP stdio 客户端：JSON-RPC 2.0、单请求串行、显式超时。

只实现本产品需要的 ``initialize`` / ``tools/list`` / ``tools/call``；
不实现采样、资源、提示词等能力，也不把服务器输出当作可信指令。
"""

import json
import os
import queue
import subprocess
import sys
import threading
from collections import deque
from time import monotonic

from min_agent.mcp.spec import MCPServerSpec

_ENV_ALLOWLIST = (
    "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP",
    "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE", "OS",
)


class MCPError(RuntimeError):
    """MCP 连接、协议或工具调用失败；消息只含可安全展示的诊断。"""


def _child_environment(spec: MCPServerSpec) -> dict[str, str]:
    """默认不继承完整环境，避免把凭据顺带交给第三方服务器。"""
    environment = {key: os.environ[key] for key in _ENV_ALLOWLIST if key in os.environ}
    environment.update(spec.env)
    return environment


class StdioMCPClient:
    """一个 MCP 服务器进程；同一时刻只允许一个未完成请求。"""

    def __init__(self, spec: MCPServerSpec) -> None:
        self._spec = spec
        self._process: subprocess.Popen | None = None
        self._inbox: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._next_id = 0
        self._stderr: deque[str] = deque(maxlen=40)
        self._threads: list[threading.Thread] = []
        self._broken = False
        self._closed = False

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> dict:
        if self._process is not None:
            raise MCPError("MCP 客户端已经启动")
        command = [self._spec.command, *self._spec.args]
        try:
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                cwd=self._spec.cwd,
                env=_child_environment(self._spec),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0,
            )
        except OSError as error:
            raise MCPError(f"MCP 服务器启动失败：{type(error).__name__}") from error
        reader = threading.Thread(target=self._read_stdout, name=f"mcp-stdout-{self._spec.name}", daemon=True)
        reader.start()
        stderr = threading.Thread(target=self._read_stderr, name=f"mcp-stderr-{self._spec.name}", daemon=True)
        stderr.start()
        self._threads = [reader, stderr]
        return self.initialize()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        process = self._process
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
        except OSError:
            pass
        try:
            process.terminate()
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass
        for stream in (process.stdout, process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass
        self._inbox.put({"_eof": True})

    # ---------------------------------------------------------------- protocol
    def initialize(self) -> dict:
        last_error: MCPError | None = None
        for version in ("2025-06-18", "2024-11-05"):
            try:
                result = self.request("initialize", {
                    "protocolVersion": version,
                    "capabilities": {},
                    "clientInfo": {"name": "adrian-harness", "version": "0.1.0"},
                })
            except MCPError as error:
                last_error = error
                if "protocol" not in str(error).lower():
                    raise
                continue
            self.notify("notifications/initialized")
            return result if isinstance(result, dict) else {}
        raise MCPError(f"MCP 初始化失败：{last_error}")

    def request(self, method: str, params: dict | None = None, *, timeout: float | None = None) -> dict:
        with self._lock:
            if self._broken:
                raise MCPError("MCP 连接已损坏，请重新连接")
            process = self._process
            if process is None or process.poll() is not None or process.stdin is None:
                raise MCPError("MCP 服务器进程未运行")
            self._next_id += 1
            request_id = self._next_id
            payload = {"jsonrpc": "2.0", "id": request_id, "method": method,
                       "params": params if params is not None else {}}
            try:
                process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                process.stdin.flush()
            except (OSError, ValueError) as error:
                self._broken = True
                raise MCPError(f"MCP 写入失败：{type(error).__name__}") from error
            deadline = monotonic() + (timeout if timeout is not None else float(self._spec.timeout_seconds))
            while True:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    self._broken = True
                    raise MCPError(f"MCP 请求超时：{method}")
                try:
                    message = self._inbox.get(timeout=remaining)
                except queue.Empty:
                    self._broken = True
                    raise MCPError(f"MCP 请求超时：{method}")
                if message.get("_eof"):
                    self._broken = True
                    raise MCPError("MCP 服务器已关闭连接")
                if message.get("id") != request_id:
                    continue
                if "error" in message:
                    error = message.get("error") or {}
                    detail = str(error.get("message", "unknown error"))[:300]
                    raise MCPError(f"MCP 返回错误：{detail}")
                result = message.get("result")
                if not isinstance(result, dict):
                    raise MCPError("MCP 返回格式无效：result 不是对象")
                return result

    def notify(self, method: str, params: dict | None = None) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise MCPError("MCP 服务器进程未运行")
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        process.stdin.flush()

    def list_tools(self) -> list[dict]:
        tools: list[dict] = []
        cursor = None
        for _ in range(20):
            params = {"cursor": cursor} if cursor else {}
            result = self.request("tools/list", params)
            page = result.get("tools")
            if not isinstance(page, list):
                raise MCPError("MCP tools/list 返回格式无效")
            tools.extend(item for item in page if isinstance(item, dict))
            cursor = result.get("nextCursor")
            if not cursor:
                return self._validate_tools(tools)
        raise MCPError("MCP tools/list 分页超过上限")

    def call_tool(self, name: str, arguments: dict) -> str:
        result = self.request(
            "tools/call",
            {"name": name, "arguments": arguments},
            timeout=max(float(self._spec.timeout_seconds), 30.0),
        )
        content = result.get("content")
        texts = []
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                    texts.append(block["text"])
        text = "\n".join(texts) if texts else json.dumps(result, ensure_ascii=False)
        return f"MCP error: {text}" if result.get("isError") else text

    # ---------------------------------------------------------------- internals
    def _validate_tools(self, tools: list[dict]) -> list[dict]:
        seen: set[str] = set()
        validated = []
        for item in tools:
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                raise MCPError("MCP 工具缺少有效名称")
            if name in seen:
                raise MCPError(f"MCP 工具名重复：{name}")
            schema = item.get("inputSchema")
            if schema is None:
                schema = {"type": "object", "properties": {}}
            if not isinstance(schema, dict) or schema.get("type", "object") != "object":
                raise MCPError(f"MCP 工具 {name} 的 inputSchema 顶层不是对象")
            seen.add(name)
            validated.append({
                "name": name,
                "description": str(item.get("description") or ""),
                "inputSchema": schema,
            })
        return validated

    def _read_stdout(self) -> None:
        stream = self._process.stdout if self._process is not None else None
        try:
            if stream is not None:
                for line in stream:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        message = json.loads(line)
                    except json.JSONDecodeError:
                        self._stderr.append(f"non-json stdout: {line[:120]}")
                        continue
                    if isinstance(message, dict):
                        self._inbox.put(message)
        except (OSError, ValueError):
            pass
        finally:
            self._inbox.put({"_eof": True})

    def _read_stderr(self) -> None:
        stream = self._process.stderr if self._process is not None else None
        try:
            if stream is not None:
                for line in stream:
                    text = line.strip()
                    if text:
                        self._stderr.append(text[:300])
        except (OSError, ValueError):
            pass

    @property
    def stderr_tail(self) -> tuple[str, ...]:
        return tuple(self._stderr)

    @property
    def healthy(self) -> bool:
        """进程还在、协议未损坏；损坏后由目录重新连接。"""
        process = self._process
        return (not self._closed and not self._broken
                and process is not None and process.poll() is None)
