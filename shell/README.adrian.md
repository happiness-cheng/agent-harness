# 上游说明（本目录是 vendored fork）

- **来源**：[deepseek-ai/chat-demo](https://github.com/deepseek-ai/chat-demo)
- **快照 commit**：`5d5dd75`（2026-11-13 上游最后一次推送）
- **许可证**：MIT，见本目录 `LICENSE`；构建产物会随 `dist/LICENSE` 一起分发，不得删除。
- **本地化改动**：见下（未改动的文件保持上游原样，便于后续对比升级）。

## 我们计划改什么、不改什么

| 部分 | 处理 |
|---|---|
| `src/components`、`src/app`、`src/i18n`、`src/services/markdown` | 保留外观与交互，尽量不动 |
| `src/services/api/*` | **替换**为调用本仓库 `/api/*` 的客户端（不再直连 DeepSeek） |
| `src/services/chat/*` | **替换**存储层：会话/消息以服务端 SQLite 为准，浏览器只做缓存 |
| 语音（ASR/TTS）、附件（Files API） | 本批**不接**，入口先隐藏 |
| 分支/重生成/编辑重发 | 与服务端单线会话语义冲突，先隐藏，后续单独设计 |
| 模型选择、待办、通知、轨迹、Plan/Build | 由我们自己的面板承载（后续批次） |

## 构建

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-shell.ps1 -Install
```

产物复制到 `src/min_agent/web/shell/`，由 FastAPI 挂在 `/app`；旧界面仍在 `/`。

## 已知限制

- 依赖 Node ≥22 与 pnpm（通过 `corepack` 调用，本机已可用）。
- 构建期常量 `__API_ORIGIN__` 默认 `https://api.deepseek.com`；改造后会改为同源，不再需要跨域。
- 产物约 4.2 MB（mermaid/katex 占大头），随包分发，不参与 Python 测试。