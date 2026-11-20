# TEMPLATE · 三类日志复制式模板

> 用法：把下面 4 个片段**分别追加**到对应日志文件的 `<Task ID>` 段。不要新建「每任务一个文件」。
> 三册以同一 Task ID 关联：DECISION_LOG 只写决策、IMPLEMENTATION_LOG 只写实施、VALIDATION_LOG 只写验证；同一事实不重复。
> 均为追加式：已写内容不回改，更正写 `[日期] 更正内容：原因与依据` 追加到条目末尾。
> 确无内容写「无」及原因，不留空字段。字段可精简，但第 3、5、7 类必填项不得省略。

---

## 片段 A · 执行前 → 追加到 IMPLEMENTATION_LOG.md

```markdown
## <Task ID> · <YYYY-MM-DD> · Phase: <PHASE>

**状态：IN PROGRESS**

### 执行前（动手前建立）

1. **Task ID**：
2. **任务目标与范围**：
3. **当前项目基线及工作区状态**：Git HEAD = ；`git status --short` = ；已有未提交改动归属 =
4. **预期修改的模块**：
5. **设计依据与相关决策**：
6. **已知风险和禁止操作**：
```

---

## 片段 B · 设计决策 → 追加到 DECISION_LOG.md

```markdown
## <Task ID> · <YYYY-MM-DD> · Phase: <PHASE>

主题：

### 修改说明及理由
- 改什么：
- 为什么：
- 依据：

### 关键设计选择

| # | 决策 | 原因 | 替代方案 | 为什么没选 / Trade-off |
|---|---|---|---|---|
| D1 | | | | |

### 是否发现与原有设计不一致的地方
1. 是／否（已处理／保留不改）：

### 追溯
- 实施：[IMPLEMENTATION_LOG.md](IMPLEMENTATION_LOG.md) → <Task ID>
- 验证：[VALIDATION_LOG.md](VALIDATION_LOG.md) → <Task ID>
```

---

## 片段 C · 执行中／执行后 → 追加到 IMPLEMENTATION_LOG.md 同 Task ID 段

```markdown
### 执行中（按发生顺序追加，不回改上文）

1. **实际完成了什么**：
2. **为什么需要这些修改**：
3. **修改了哪些文件及其具体作用**：

| 文件 | 实际修改内容 | 作用 |
|---|---|---|
| | | |

4. **关键设计选择及原因**：见 DECISION_LOG → <Task ID>（不在此重复）
5. **考虑过的替代方案、为什么没选**：见 DECISION_LOG → <Task ID>（不在此重复）
6. **是否发现与原有设计不一致的地方**：见 DECISION_LOG → <Task ID>（不在此重复）

### 执行后

1. **实际修改文件清单**（`git status --short` 输出）：
2. **测试命令、环境与结果**：见 VALIDATION_LOG → <Task ID>（不在此重复）
3. **测试数量／失败处理**：见 VALIDATION_LOG → <Task ID>（不在此重复）
4. **尚未解决的问题 / 哪些工作没有完成**：（必填，无则写「无」并说明）
5. **潜在风险及影响范围**：
6. **副作用**：数据库 = ／文件 = ／配置 = ／其他 =
7. **最终状态**：COMPLETED / PARTIAL / BLOCKED
```

---

## 片段 D · 验证与自检 → 追加到 VALIDATION_LOG.md

```markdown
## <Task ID> · <YYYY-MM-DD> · Phase: <PHASE>

### 环境
- OS / Shell / Python / Git：

### 测试与检查

| # | 命令 | 范围 | 新增 | 通过 | 失败 | 跳过 | 未执行(NOT RUN) | 警告 | 结果 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | | | | | | | | | |

- 没有实际执行的测试不得标记通过；未运行一律 NOT RUN 并写原因。
- 失败原因及处理情况：
- 复用已有可信证据的来源与版本：
- 验证范围与边界（测试通过 ≠ 功能正确；程序测试 ≠ 自然语言效果）：

### 自检报告
| 门禁项 | 结果 |
|---|---|
| ① 检查实际 Git Diff | |
| ② 核对修改文件清单 | |
| ③ 执行与修改范围相关的测试 | |
| ④ 记录准确结果（含失败／跳过／NOT RUN） | |
| ⑤ 更新三册本 Task ID 条目 | |
| ⑥ 向用户总结 | |

### 范围确认
- 任务边界逐条核对：
- 超范围动作：无／（列出）

### 追溯
- 决策：[DECISION_LOG.md](DECISION_LOG.md) → <Task ID>；实施：[IMPLEMENTATION_LOG.md](IMPLEMENTATION_LOG.md) → <Task ID>
```
