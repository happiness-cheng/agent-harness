# 最小只读 Activity Connector

支持已观察的 Codex rollout JSONL/zstd、DSH session version 4 和 version 0（old）。只输出元数据报告，不写 Event Store、Checkpoint 或 Memory。

## 安装与运行

```powershell
.venv/Scripts/python.exe -m pip install -e ".[activity]"
.venv/Scripts/python.exe -X utf8 -m min_agent.activity_connector "$HOME/.codex/sessions" --max-files 5 --output reports/codex-activity.json
.venv/Scripts/python.exe -X utf8 -m min_agent.activity_connector "$HOME/.dsh/sessions" --max-files 5 --output reports/dsh-activity.json
.venv/Scripts/python.exe -X utf8 -m min_agent.activity_connector "C:/你的目录/单个历史文件.jsonl"
```

支持单文件或递归目录；目录只选 `.jsonl`、`.zst`、`.zstd`，跳过 symlink/reparse points。报告父目录需已存在；输出必须是新文件且不能位于输入目录内部，已有报告拒绝覆盖。不传 `--output` 时打印 JSON。退出码 0 表示扫描范围内无异常；2 表示部分读取、限制、解析异常或参数/输出拒绝。KNOWN_LIMIT/provisional 不自动等同解析错误。

## 报告解释

- 文件数、成功/失败记录数、带异常文件数、按来源/类型/命名空间计数、合法时间范围及有界时间线。头部计入 parsed_records，但不作为活动事件写入时间线。
- 来源用匿名 source_ref，位置使用零基 line_index 与解压后的 logical_byte_offset；压缩文件的 physical_byte_offset 为 null，避免伪造偏移映射。
- 仅原生 session ID 可证明时派生 object_id；不输出原生 ID。没有 Manifest/root/generation/Fork 证据，所以所有非头记录保持 provisional，native_record_id 为 null。provisional ID 可随观察路径变动；可证明的 object ID 不依赖路径。
- DSH old seq 和 seq0 分开处理；两者同时出现或两者皆缺时报告歧义/缺失，不猜。多个 session 头会中止后续归属；同 object 的多个文件报告 unresolved_same_object_multiple_files，分别统计不合并。
- 只输出类型白名单、合法时间、数值定位、状态及匿名标识。不输出正文、摘要原文、凭据、异常原始文本或私有路径；未知 type 映射为 other。时间线是来源记录顺序，时间来自字段，不能据此推断任务完成或模型调用。报告仍属于本地活动元数据，不应当作公共原始数据上传。

## 稳定性与边界

文件仅 rb 打开。流式解压逐行解析，完整帧/完整换行记录才接受；尾部半行、未结束 zstd 流、损坏均报告异常。两遍物理指纹与前后 stat 不一致则 unstable_or_unverified_read，绝不作为正常历史结尾。该机制不等于锁住文件，不能证明未来不会变化。

默认每文件物理/逻辑上限 256 MiB、单行 4 MiB、100000 行；最多 100 个文件、每文件最多展示 500 个时间线项和 100 个错误。达到读取上限报告异常；时间线展示截断保留总计数和 omitted 数。可通过 `--max-files`、`--timeline-limit` 控制展示规模。

zstandard 为必要的可选依赖；依据官方 [Decompression APIs / ZstdDecompressionObj](https://python-zstandard.readthedocs.io/en/latest/decompressor.html#zstandard.ZstdDecompressionObj)，检查 eof/unused_data，不将物理 EOF 等同帧完整。缺依赖明确报 missing_zstandard_dependency。已在真实 DSH 未声明内容大小的流式帧上验证。

复用 M1 身份派生和 M3 Locator/Fingerprint 规则。既有 canonical 子集不支持浮点，含此类值时标 fingerprint unsupported，不编造指纹。M4 接口需要整份 bytes，本轮没有为了复用它而全量载入历史；没有持久前态时变化分类为 unresolved_no_persistent_baseline。M4 的内存执行器不代表持久化已经完成。

M5/M6 暂缓：不确认复杂 Fork、物理副本、NTFS 单字段身份，不共享 Checkpoint、不自动映射。没有服务/Hook/插件/模型调用，也不连接生产 Activity/Memory。

验收及真实小批统计见[阶段记录](../phase-records/2026-11-06-readonly-activity-connector.md)和[脱敏证据](../acceptance/activity-readonly-20261106.json)。
