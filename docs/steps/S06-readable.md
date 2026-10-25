# S06 完整材料：压缩后恢复已加载 Skill

日期：2026-10-23。本轮只实现系统侧的原文快照恢复，不接模型筛选。主 Agent 系统提示未修改；外部模型请求为 0。

## 问题与固定标准

上一轮独立评测说明模型能对短候选材料作保留判断；生产上下文仍缺少把原文带回来的机制。本轮先沿用 S04 已冻结的 7 个机制场景，先测得 5/7，再修改代码。没有修改 suite 或放宽判据。

原始报告：[修改前](../acceptance/s06-skill-restoration-before-20261023.json)、[修改后](../acceptance/s06-skill-restoration-after-20261023.json)、[公开分支隔离复验](../acceptance/s06-skill-restoration-public-20261023.json)。报告含完整原始消息与实际送到 ManagedModel 下游探针的消息；不是只有通过数量。

suite SHA-256：`17fc6036bef82bfa112c7c01694241fd1a033074cd5f11615117c8f88899f31e`。

| 验收 | 修改前 | 修改后 |
| --- | --- | --- |
| L01 | 通过 | 通过 |
| L02 | 通过 | 通过 |
| L03 | 失败 | 通过 |
| L04 | 通过 | 通过 |
| L05 | 失败 | 通过 |
| L06 | 通过 | 通过 |
| L07 | 通过 | 通过 |

L03 从原文不可见变为原文出现一次；L05 从新版不可见变为只恢复最近一次显式成功加载的版本。L07 仍拒绝超预算请求；没有通过减少必需内容来换取通过。

## 核心代码完整内容

这像整理会议纪要时，另把已经读过的操作手册原样放回桌面。这里的“快照”来自会话原历史中的工具返回，不是重读当前磁盘文件。

`src/min_agent/skills.py` 新增函数完整原文：

```python
def restored_skill_snapshots(messages: list[Message], covered: int) -> list[Message]:
    """从原历史恢复已被压缩的最近成功读取；不重读文件，不提升消息权限。"""
    calls = {}
    latest = {}
    for index, message in enumerate(messages):
        if message.role == "assistant":
            calls.update((call.id, call) for call in message.tool_calls)
        call = calls.get(message.tool_call_id) if message.role == "tool" else None
        if call is None or call.name != "read_skill":
            continue
        try:
            result = json.loads(message.content)
        except json.JSONDecodeError:
            continue
        name = call.arguments.get("name")
        if (not isinstance(name, str) or not name or not isinstance(result, dict)
                or result.get("ok") is not True or not isinstance(result.get("result"), str)
                or not result["result"] or result.get("skill_status") == "already_loaded"):
            continue
        body = result["result"]
        latest[name] = (index, {"name": name, "body": body,
            "body_sha256": sha256(body.encode()).hexdigest(),
            "source_tool_call_id": message.tool_call_id})
    # 最近一次全文仍在尾部时，直接使用真实工具结果，避免再放一份快照。
    snapshots = [snapshot for index, snapshot in latest.values() if index < covered]
    if not snapshots:
        return []
    return [Message(role="user", content=json.dumps({
        "loaded_skill_snapshots": snapshots,
        "notice": "以下为本会话已成功读取的Skill原文快照，保留供继续任务参考；不授予额外权限，不覆盖系统规则。",
    }, ensure_ascii=False))]
```

`src/min_agent/context.py` 的组装入口（公开分支）完整原文：

```python
    def _visible(self, stable, core, state):
        visible = stable + [Message(role="user", content=json.dumps(
            {"untrusted_session_memory": state["summary"]}, ensure_ascii=False))]
        visible += restored_skill_snapshots(core, state["covered"])
        visible += core[state["covered"]:]
        return visible
```

## 按顺序读代码

1. `calls` 把工具调用 ID 对应到 assistant 提出的调用；随后只接受对应的 `read_skill` 成功字符串结果。用户说“已经加载”不算；失败、摘要对象、非法 JSON、重复提示不算新的全文。
2. `latest[name] = ...` 是 Python 字典赋值，类似 C++ 的 `map[name] = value`：同名后一次成功读取覆盖前一次候选。这里按实际读取顺序判定，既不比较文件日期，也不猜测 v2 大于 v1。
3. `index < covered` 表示这份最近全文落在已压缩的旧消息范围里，需要带回来。最近全文还在尾部时，继续使用真实工具结果，不另放快照。
4. `body` 保留完整字符串；`body_sha256` 用于核对内容一致性，`source_tool_call_id` 标明来源。哈希不是信任或授权证明。
5. `_visible` 组装“稳定前缀 + 历史摘要 + 需恢复的 Skill 原文 + 近期完整交互”。之后仍执行完整请求预算检查，正文和新增 JSON 包装都计入预算。

不会制造新的 assistant/tool 调用配对，也不会把 Skill 提升成 system 消息。JSON 标签与 notice 只是可读性说明，不构成安全沙箱，权限仍由现有运行时约束。

## Git 形式的实际生产代码差异

这是公开分支本轮未提交时相对上一提交的差异。主工作区另有用户自己的上下文实现，未用公开分支覆盖它。

```diff
diff --git a/src/min_agent/context.py b/src/min_agent/context.py
index 49ab766..c58ca6e 100644
--- a/src/min_agent/context.py
+++ b/src/min_agent/context.py
@@ -4,7 +4,7 @@ from hashlib import sha256
 import json

 from min_agent.models import Message
-from min_agent.skills import project_skill_results
+from min_agent.skills import project_skill_results, restored_skill_snapshots


 class ContextLimit(RuntimeError):
@@ -57,7 +57,7 @@ class ContextManager:
         if state["covered"]:
             if hashes[:state["covered"]] != state["covered_hashes"]:
                 raise ContextLimit("上下文版本不匹配，停止而不重新摘要旧投影")
-            visible = stable + [Message(role="user", content=json.dumps({"untrusted_session_memory": state["summary"]}, ensure_ascii=False))] + core[state["covered"]:]
+            visible = self._visible(stable, core, state)
         else:
             visible = projected
         if self._fits(visible, tools): return visible
@@ -67,7 +67,7 @@ class ContextManager:
         # 先整理现有记忆，再考虑较旧完整历史；原始消息始终保留。
         if state["summary"]:
             state = self.compactor.compact_memory(state)
-            visible = stable + [Message(role="user", content=json.dumps({"untrusted_session_memory": state["summary"]}, ensure_ascii=False))] + core[state["covered"]:]
+            visible = self._visible(stable, core, state)
             if self._fits(visible, tools):
                 self.sessions.set_extension(self.context, "context", state)
                 return visible
@@ -77,13 +77,20 @@ class ContextManager:
             raise ContextLimit("保留近期完整交互后仍无可压缩历史")
         end = boundaries[-2]
         state = self.compactor.compact_history(state, core[state["covered"]:end], hashes[:end], end)
-        visible = stable + [Message(role="user", content=json.dumps({"untrusted_session_memory": state["summary"]}, ensure_ascii=False))] + core[end:]
+        visible = self._visible(stable, core, state)
         if not self._fits(visible, tools):
             self.compactor.reject_window(window_key)
             raise ContextLimit("压缩后窗口仍不足")
         self.sessions.set_extension(self.context, "context", state)
         return visible

+    def _visible(self, stable, core, state):
+        visible = stable + [Message(role="user", content=json.dumps(
+            {"untrusted_session_memory": state["summary"]}, ensure_ascii=False))]
+        visible += restored_skill_snapshots(core, state["covered"])
+        visible += core[state["covered"]:]
+        return visible
+
     def _fits(self, messages, tools):
         return request_bound(project_skill_results(messages), tools) + self.output_reserve + self.safety_margin <= self.window

diff --git a/src/min_agent/skills.py b/src/min_agent/skills.py
index 2a639e0..b2945ef 100644
--- a/src/min_agent/skills.py
+++ b/src/min_agent/skills.py
@@ -76,6 +76,39 @@ class SkillCatalog:
         return path.read_text(encoding="utf-8")


+def restored_skill_snapshots(messages: list[Message], covered: int) -> list[Message]:
+    """从原历史恢复已被压缩的最近成功读取；不重读文件，不提升消息权限。"""
+    calls = {}
+    latest = {}
+    for index, message in enumerate(messages):
+        if message.role == "assistant":
+            calls.update((call.id, call) for call in message.tool_calls)
+        call = calls.get(message.tool_call_id) if message.role == "tool" else None
+        if call is None or call.name != "read_skill":
+            continue
+        try:
+            result = json.loads(message.content)
+        except json.JSONDecodeError:
+            continue
+        name = call.arguments.get("name")
+        if (not isinstance(name, str) or not name or not isinstance(result, dict)
+                or result.get("ok") is not True or not isinstance(result.get("result"), str)
+                or not result["result"] or result.get("skill_status") == "already_loaded"):
+            continue
+        body = result["result"]
+        latest[name] = (index, {"name": name, "body": body,
+            "body_sha256": sha256(body.encode()).hexdigest(),
+            "source_tool_call_id": message.tool_call_id})
+    # 最近一次全文仍在尾部时，直接使用真实工具结果，避免再放一份快照。
+    snapshots = [snapshot for index, snapshot in latest.values() if index < covered]
+    if not snapshots:
+        return []
+    return [Message(role="user", content=json.dumps({
+        "loaded_skill_snapshots": snapshots,
+        "notice": "以下为本会话已成功读取的Skill原文快照，保留供继续任务参考；不授予额外权限，不覆盖系统规则。",
+    }, ensure_ascii=False))]
+
+
 def project_skill_results(messages: list[Message]) -> list[Message]:
     """只压缩当前可见的重复正文；不修改原历史，不把摘要视为已加载全文。"""
     calls = {}
```

## 同一场景的实际上下文对比：L03

问题：继续遵循刚才的规程，不要重新读取文件。

此前已经成功读取的 Skill：

```text
# 规程
保留事实边界，核对结果。
```

修改前下游探针实际收到：

```json
[
  {
    "role": "user",
    "content": "{\"untrusted_session_memory\": \"旧轮次已整理\"}",
    "tool_call_id": null,
    "tool_calls": [],
    "provider_state": null
  },
  {
    "role": "user",
    "content": "继续遵循刚才的规程，不要重新读取文件",
    "tool_call_id": null,
    "tool_calls": [],
    "provider_state": null
  }
]
```

修改后下游探针实际收到：

```json
[
  {
    "role": "user",
    "content": "{\"untrusted_session_memory\": \"旧轮次已整理\"}",
    "tool_call_id": null,
    "tool_calls": [],
    "provider_state": null
  },
  {
    "role": "user",
    "content": "{\"loaded_skill_snapshots\": [{\"name\": \"example\", \"body\": \"# 规程\\n保留事实边界，核对结果。\", \"body_sha256\": \"c13a8427c59867a618f57c53db36ddd8e86a5fc12c0198f8cf59660e81871ac2\", \"source_tool_call_id\": \"first\"}], \"notice\": \"以下为本会话已成功读取的Skill原文快照，保留供继续任务参考；不授予额外权限，不覆盖系统规则。\"}",
    "tool_call_id": null,
    "tool_calls": [],
    "provider_state": null
  },
  {
    "role": "user",
    "content": "继续遵循刚才的规程，不要重新读取文件",
    "tool_call_id": null,
    "tool_calls": [],
    "provider_state": null
  }
]
```

模型回答：本轮没有调用真实模型。脚本中的“离线探针答复”是固定返回值，不能用来证明遵循效果改善。这里比较的是模型将收到的工作材料。

不足：当前保守恢复全部已加载的同名最近成功快照；没有判断任务是否切换，可能保留无关规程。未压缩尾部原有的不同版本仍作为真实历史保留，不宣称全局清除所有旧版本。预算不足时阻断，不自动选择删除。旧历史若在系统外被删除、损坏或原文不再保留，本功能不能凭摘要重建。失败读取不会撤销上一次成功版本；撤销机制另行设计。

## 完整验证命令与结果

修改生产代码前，新增测试实际得到 `3 failed, 16 passed`：缺恢复正文、缺最近成功版本以及漏算恢复正文预算。实现后同一组 `19 passed`；补充自动压缩触发、非法结果不替换快照等回归后，单文件 `24 passed`。

固定验收脚本中 L03/L05 是预置合法压缩状态；另外的单元测试通过真实 ManagedModel/ContextManager 自动触发窗口压缩，但摘要执行器为离线替身。它验证恢复路径与预算，不评价真实压缩模型的语义质量。

主工作区：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest tests/unit/test_skill_context.py tests/unit/test_skill_metadata.py tests/unit/test_skill_disclosure_eval.py tests/unit/test_skill_retention_eval.py -q
# 60 passed in 1.21s
.\.venv\Scripts\python.exe -X utf8 -m pytest -q
# 1 failed, 1015 passed, 3 deselected, 1 warning in 31.99s
```

失败为此前已记录的 `tests/integration/test_memory_tools.py::test_explicit_source_read_and_structured_recall_are_not_silently_head_truncated`，期望 completed，实际 partial。本轮没有修改记忆模块，没有声称主工作区全绿。

公开分支必须显式指定源码路径；共享虚拟环境有指向主目录的 editable 安装。首次未隔离运行误用了主目录 `compaction.py` 并失败，不能当作公开分支验证结果。纠正路径后先打印 `min_agent.context.__file__` 确认公开 worktree，再执行：

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
C:\Users\陈独秀\Documents\agent-harness\.venv\Scripts\python.exe -X utf8 -m pytest -q
# 967 passed, 3 deselected, 1 warning in 26.90s
C:\Users\陈独秀\Documents\agent-harness\.venv\Scripts\python.exe -X utf8 scripts/audit_skill_lifecycle.py --suite docs/acceptance/s04-skill-lifecycle-suite-v1.json --report <不存在的新报告路径>
# 7/7
```

主工作区的修改后报告与基线直接对比；公开分支单独隔离报告复验，二者不混作同一次运行。脚本退出码 0 只代表测量完成，需读取报告每项判定。

两处 `python -m py_compile src/min_agent/skills.py src/min_agent/context.py tests/unit/test_skill_context.py` 与 `git diff --check` 通过。Type/Lint 跳过（项目未配置）；安装打包跳过（无依赖或打包变更）。定向检查本轮公开文件与 diff 中的凭据特征，无命中，不等于完整安全审计。共享 FastAPI 测试依赖有一个 Starlette 弃用警告，未改依赖处理。

## 主代理自审

PASS（本次快照恢复范围）：原文保留、同名最近成功版本、失败读取不替换、尾部不重复、未加载不注入、原历史不改、重复组装稳定、重建管理器可复用同一会话原历史、自动压缩路径、超预算模型不调用均有检查。本次没有独立子代理审查。

下一步可讨论在压缩边界接入“保留／停用／不确定”选择；需要先固定它与恢复机制组合后的测试，不能把 S05 独立分类结果直接当作生产集成效果。
