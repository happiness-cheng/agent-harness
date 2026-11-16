# MCP 接入（渐进式披露）

> 2026-11-17（MCP-001）。目标：对话开始时**只暴露 MCP 服务器的 name + description**，
> 模型决定使用后调用 `connect_mcp`，连接成功才把该服务器的工具并入 `tools`。
> 只验证了 stdio；远程 HTTP/OAuth、resources/prompts/sampling 未实现。

## 工作方式

```
第一轮请求
  system: 基础规则 + “## MCP 服务器（按需连接）” 段
          - everything：官方 MCP Everything 参考服务器…
  tools : 普通工具 + connect_mcp(name=enum[everything])
  message: 用户输入 + 状态尾部

模型调用 connect_mcp("everything")
  → 启动/连接服务器 → initialize → tools/list → 校验/规范化名字
  → 以 mcp__everything__<tool> 注册进同一个 ToolRegistry
  → 工具结果只回名称清单（不回大段说明，详细 schema 在下一轮）

下一轮请求
  tools : 普通工具 + connect_mcp + 16 个 mcp__everything__* 工具
```

- **工具 schema 原样透传**：`ToolDefinition.raw_schema` 保存服务器返回的 `inputSchema`，
  `ToolRegistry.schemas()` 直接使用它；调用参数由服务器自己校验（本地只要求是 JSON 对象）。
- **连接是会话级的**：同一会话跨任务复用连接；应用 `close()` 时统一终止子进程。
- **连接结果只回名称**：避免把几十个工具的说明塞进 tool result 触发工具摘要/额外模型调用。

## 配置

`--mcp-config path.json`：

```json
{
  "servers": [
    {
      "name": "everything",
      "description": "官方 MCP Everything 参考服务器（stdio，含 echo/add 等工具）",
      "command": "D:\\Program Files\\nodejs\\npx.cmd",
      "args": ["-y", "@modelcontextprotocol/server-everything"],
      "policy": "allow",
      "timeout_seconds": 120,
      "env": {}
    }
  ]
}
```

| 字段 | 说明 |
|---|---|
| `name` | 模型可见的服务器名；只能字母/数字/`_`/`-`，1–32 字符 |
| `description` | 连接前给模型看的唯一说明；不要放凭据或内部路径 |
| `command` / `args` | **可信 host 配置**；模型永远不能提供 |
| `env` | 追加给子进程的环境变量；不设置时只白名单继承 |
| `cwd` | 子进程工作目录；默认继承 |
| `policy` | `allow`（调用）或 `confirm`（第一版直接拒绝，不假装有审批 UI） |
| `timeout_seconds` | initialize/tools/list 超时；默认 20 |

启动：

```powershell
.venv\Scripts\python.exe -X utf8 -m min_agent.web --qianwen --mcp-config reports\mcp-20261117\everything.json
```

## 安全边界

1. **授权来自 host 配置，不来自服务器**：不读 `annotations`、不读服务器自述权限；
   `policy=confirm` 在第一版返回“需要宿主确认；本次未执行”，不会产生副作用。
2. **模型只能选服务器名**：`command/args/env/cwd` 全部来自配置；`name` 用动态 `Literal`
   约束，未知名字在参数校验层就被拒绝（可修正的 `invalid_arguments`）。
3. **子进程环境白名单**：默认只继承 `PATH/PATHEXT/SYSTEMROOT/WINDIR/COMSPEC/TEMP/TMP/HOME/
   USERPROFILE/APPDATA/LOCALAPPDATA/NUMBER_OF_PROCESSORS/PROCESSOR_ARCHITECTURE/OS`，
   不把完整环境（含凭据）交给第三方服务器；配置可显式追加。
4. **名字冲突/长度**：统一加 `mcp__<server>__<tool>` 前缀（≤64 字符），规范化后重名或与
   已有工具冲突都在连接/绑定阶段拒绝，不做静默覆盖。
5. **协议失败可见但不致命**：连接失败/超时可以重试；工具调用返回 `MCP error: ...` 文本，
   模型能看到原因，不会把整个任务伪装成成功。
6. **不自动重连**：服务器进程退出后连接标记损坏；重新连接需要再次调用 `connect_mcp`
   （本版不自动重启）。

## 上下文成本（实测）

`@modelcontextprotocol/server-everything` 连接后：16 个工具、schema 约 **8.8 KB**、
按现有估算约 **9.9k tokens**（还没算系统提示词和历史）。因此：

- 小窗口模型（如 8k num_ctx）**不应**连接大服务器；会立刻触发 `context_limit`。
- 渐进式披露的意义是把“连接前”成本压到只有 `name/description` 和 `connect_mcp`。
- 连接后仍会把整批工具 schema 放进每次请求——这是当前实现的已知成本，不是零成本。

## 测试

离线（真实子进程协议、假服务器，不联网、不调用模型）：

```powershell
.venv\Scripts\python.exe -X utf8 -m pytest tests/unit/test_mcp_spec.py tests/unit/test_mcp_client.py tests/unit/test_mcp_catalog.py tests/integration/test_mcp_runtime.py tests/integration/test_mcp_application.py -q
```

真实 MCP（npx，需网络/包缓存；**不调用模型**）：

```powershell
.venv\Scripts\python.exe -X utf8 scripts\mcp_probe.py --config reports\mcp-20261117\everything.json --server everything
.venv\Scripts\python.exe -X utf8 scripts\mcp_probe.py --config reports\mcp-20261117\everything.json --server everything --call echo --args '{"message":"hello-mcp"}'
```

应用层 live 测试（ScriptedModel 代替真实模型，验证“先暴露名称、连接后并入工具”）：

```powershell
$env:MCP_LIVE_CONFIG = "reports\mcp-20261117\everything.json"
$env:MCP_LIVE_TOOL = "echo"
$env:MCP_LIVE_ARGS = '{"message":"mcp-live"}'
$env:MCP_LIVE_EXPECT = "Echo: mcp-live"
.venv\Scripts\python.exe -X utf8 -m pytest tests/live/test_mcp_live.py -m live -q
```

## 明确未做 / 下一步

- **未做**：远程 HTTP/SSE 传输与 OAuth；MCP resources / prompts / sampling；工具调用进度与
  取消；`confirm` 的交互审批；多服务器工具名去重之外的冲突策略；服务器崩溃自动重连；
  把 MCP 工具调用计入单独预算。
- **上下文方向**：本次只加了 MCP 组件提示词段（`SystemPrompt` 的 section 机制）。
  skills / memory 的组件提示词段尚未接入；它们是下一步，不应与 MCP 混在一批改。
- **验证边界**：真实 MCP 只测了 `server-everything` 的 stdio；不证明其他服务器、
  Streamable HTTP、鉴权服务器、Windows 服务化启动。
