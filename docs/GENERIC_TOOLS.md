# 通用四工具模式（GENERIC-TOOLS-001）

> 状态：PARTIAL / 实验入口。默认服务仍是专用工具链；本模式通过 `--generic-tools`
> 显式开启。它不经过 Plan/Build 与目标确认门，`bash` 不是沙箱。只在可信本机目录使用。

## 是什么

把模型可见工具收敛为四个通用入口，参考 pi 的 `read / write / edit / bash`：

| 工具 | 参数 | 行为 |
|---|---|---|
| `read` | `path`, `offset=1`, `limit=2000` | 读工作区 UTF-8 文本，按行切片；返回原文、起止行、总行数、sha256 |
| `write` | `path`, `content` | 新建或原子覆盖；父目录必须已存在；拒绝敏感路径和超过 1 MiB |
| `edit` | `path`, `old_text`, `new_text` | 唯一匹配替换；0 次/多次匹配都拒绝；原子写 |
| `bash` | `command`, `timeout_seconds=120` | 在工作区根执行 Git Bash；返回 exit_code/stdout/stderr，各截断 64 KiB |

没有 `todo_*`、`reminder_*`、长期记忆、`research`、`delegate_task`、MCP 工具，
也没有单独的工具注册入口。专用工具代码保留，切回默认模式即可恢复。

## 怎么启动

```powershell
# 一键（千问平台 + 四工具 + 工作区=仓库根，默认端口 8766）
scripts\start-pi.cmd

# 等价显式命令
.venv\Scripts\python.exe -X utf8 -m min_agent.web --qianwen --generic-tools `
  --workspace-root . --port 8766 --database data/adrian-pi.sqlite3
```

`--generic-tools` 未给 `--workspace-root` 时取当前目录；`--qianwen`/`--live`/`--ollama`
仍需各自既有的凭据或本机服务。启动时会打印一行边界提示。

## 权限与安全边界（重要）

- **不经过 Plan/Build、目标确认或动作消费。** 写入和命令直接生效；系统提示词会
  明确告知模型。界面上仍可能显示 Plan/Build，但本模式不读取该状态。
- **`bash` 以当前用户权限运行，不是沙箱。** 能读写工作区外文件、访问网络、
  读取 `.env`/凭据文件/数据库；`read`/`write`/`edit` 仍走 `FileSandbox`，会拒绝
  敏感路径，但 `bash` 可以绕过。
- **环境变量清洗**：启动 bash 时移除名称含 `API_KEY`/`TOKEN`/`SECRET`/`PASSWORD`/
  `CREDENTIAL` 的变量，并把项目 `.venv/Scripts` 放到 PATH 前面；这不等于文件系统隔离。
- **超时与输出上限**：默认 120 秒、范围 1–600 秒；超时会用 `taskkill /T` / 进程组
  连同子进程终止。stdout/stderr 各截断 64 KiB。
- **没有回收站**：本模式不注册 `fs_delete`；`bash rm` 是硬删除。破坏性命令需用户明确要求。
- **平台**：只使用 Git Bash；拒绝 `C:\Windows\System32\bash.exe`（WSL）以避免工作区
  路径语义不一致。未安装 Git for Windows 时 `bash` 返回 `bash_not_found`。

## 与默认模式的关系

| 模式 | 工具数 | 授权 | 状态 |
|---|---|---|---|
| 默认（`start-qianwen` / `start-adrian`） | 21–28 | Plan/Build + 确认 + 审计 | 不变，全量测试覆盖 |
| `--generic-tools` | 4 | 无 Plan/Build 门 | 本实验 |

默认模式没有被删除或改写；`generic_tools=False` 时注册链完全走原路径。新增的
`show_state=False` 只在本模式关闭 `application_state` 尾部，避免模型看到与实际
能力矛盾的 `mode=plan`。

## 验证与未验证

- 已测：`tests/unit/test_generic_tools.py`（14 项）、`tests/integration/test_generic_mode.py`
  （7 项），共 21 项。覆盖行切片、越界、目录提示、敏感路径、覆盖写、唯一匹配、bash 退出码/
  超时杀子进程/输出截断/环境清洗、默认模式隔离、Runtime 真实执行 write→read、完整 worker 线程跑通。
- 未测：真实模型选择四个工具的效果；Windows 外的 Git Bash；`bash` 对 GUI/交互式命令；
  大文件（>256 KiB）分段读；程序化工具调用（代码内回调工具）；网络隔离。
- 小样本、脚本化模型结果不能证明自然语言场景的成功率。

## 下一步（未做）

1. `read` 支持超过 256 KiB 的分段读取（当前直接拒绝，模型需改用 `bash`）。
2. 程序化工具调用：代码里回调受控工具，只把聚合结果写回上下文（需要预算/审计桥）。
3. 评价：同一组固定任务对比默认模式与四工具模式的工具调用数、上下文字节、成功率。