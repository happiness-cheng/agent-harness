# 诊断探针的失败记录

第一次临时重放生成的 `legacy-source-read.json` **无效，不作为修复证据**：旧 handler 注入误用了 ToolRegistry 不存在的公开 `sessions/context` 属性，触发的是工具异常，因此旧分支没有观测到第二次模型窗口计算。原始无效输出保留，不据此声称修复。

正式重放脚本 `scripts/audit_legacy_source_read_v02.py` 使用既有内部绑定，并要求同时满足“旧形状实际超过 32768 字节且原回归失败”“新形状不超过窗口且原回归通过”。有效结果见 `legacy-source-read-verified.json`。生产修复与 A/B/C 源码、Gold 未因这个诊断脚本错误发生变化。
