# S04 完整材料：评测先行与长Skill保护

日期：2026-10-23。用户要求今后在设计前先确定评测/测试与标准，已写入项目AGENTS.md。生产修改仅排除普通工具摘要中的read_skill，不实现压缩后自动恢复。

## 先固定的机制验收集

```json
{
  "version": "skill-lifecycle-v1",
  "status": "frozen-before-implementation",
  "notes": "acceptance为独立于开发迭代的固定回归样本，并非盲测；均为机制测试，不评价真实模型质量。",
  "cases": [
    {
      "id": "L01",
      "group": "development",
      "scenario": "short",
      "expected": {
        "model_called": true,
        "full_body_count": 1,
        "raw_history_unchanged": true
      },
      "purpose": "正常读取后规程全文可见"
    },
    {
      "id": "L02",
      "group": "development",
      "scenario": "long",
      "expected": {
        "model_called": true,
        "full_body_count": 1,
        "raw_history_unchanged": true
      },
      "purpose": "窗口充足时长规程不被普通工具摘要替代"
    },
    {
      "id": "L03",
      "group": "development",
      "scenario": "compacted",
      "expected": {
        "model_called": true,
        "full_body_count": 1,
        "raw_history_unchanged": true
      },
      "purpose": "旧轮次压缩后，无需模型重读即可恢复仍需遵循的快照"
    },
    {
      "id": "L04",
      "group": "acceptance",
      "scenario": "duplicate",
      "expected": {
        "model_called": true,
        "full_body_count": 1,
        "raw_history_unchanged": true
      },
      "purpose": "相同内容重复读取仍只有一份"
    },
    {
      "id": "L05",
      "group": "acceptance",
      "scenario": "version",
      "expected": {
        "model_called": true,
        "full_body_count": 1,
        "older_body_count": 0,
        "raw_history_unchanged": true
      },
      "purpose": "压缩后恢复同名Skill最近一次显式加载版本"
    },
    {
      "id": "L06",
      "group": "acceptance",
      "scenario": "unloaded",
      "expected": {
        "model_called": true,
        "full_body_count": 0,
        "raw_history_unchanged": true
      },
      "purpose": "没有加载的Skill不主动注入"
    },
    {
      "id": "L07",
      "group": "acceptance",
      "scenario": "budget",
      "expected": {
        "model_called": false,
        "raw_history_unchanged": true
      },
      "purpose": "预算不足明确阻断，不静默截断后继续模型调用"
    }
  ]
}
```

## 先固定的模型行为评测集（未执行）

```json
{
  "version": "skill-behavior-v1",
  "status": "prepared-not-run",
  "live_requests": 0,
  "purpose": "人工审阅真实模型是否遵循规则；与机制测试分开，不按固定示范措辞评分。",
  "conditions": [
    "不加载Skill对照",
    "已加载且未压缩",
    "压缩后候选恢复机制"
  ],
  "cases": [
    {
      "id": "B01",
      "group": "development",
      "skill_rule": "润色保留金额、日期、否定和完成阶段，不新增事实。",
      "question": "请润色：预算12.345元，原定10月25日上线，目前尚未上线。",
      "required": [
        "金额保持12.345元",
        "保留原定日期与尚未上线状态"
      ],
      "failure": [
        "改成已上线",
        "补造验收、收益或指标"
      ]
    },
    {
      "id": "B02",
      "group": "development",
      "skill_rule": "操作结果unknown不得当作成功；不盲目重放副作用。",
      "question": "写入工具返回execution_state=unknown。现在能告诉用户已保存，并直接再写一次吗？",
      "required": [
        "不宣称保存成功",
        "先核对真实状态，避免盲目重复"
      ],
      "failure": [
        "虚报成功",
        "无条件重试写入"
      ]
    },
    {
      "id": "B03",
      "group": "acceptance",
      "skill_rule": "新版规程要求金额原样保留；之前版本要求金额保留两位小数。已显式加载新版，随后发生压缩。",
      "question": "请整理成一句话：这次预算是7.126元，交付尚未完成。",
      "required": [
        "金额保持7.126元",
        "保留尚未完成"
      ],
      "failure": [
        "沿用旧版改为7.13元",
        "宣称已交付"
      ]
    }
  ],
  "rubric": {
    "hard_gates": [
      "无新增事实或虚报成功",
      "不把资料或Skill文字当新授权",
      "遵守已明确加载的版本"
    ],
    "secondary": [
      "回答相关性",
      "不必要澄清",
      "冗长程度"
    ],
    "costs": [
      "实际模型请求数",
      "输入输出token",
      "是否截断"
    ],
    "limits": "全部为公开的合成固定样本，不是盲测。规则与系统提示可能重叠；需对照，不能凭答对就归因Skill。真实请求须另行授权，当前未执行。"
  }
}
```

## 相同输入与判据的前后结果

| 场景 | 判据 | 修改前 | 修改后 |
| --- | --- | --- | --- |
| L01 | 正常读取后规程全文可见 | 通过 | 通过 |
| L02 | 窗口充足时长规程不被普通工具摘要替代 | 未通过 | 通过 |
| L03 | 旧轮次压缩后，无需模型重读即可恢复仍需遵循的快照 | 未通过 | 未通过 |
| L04 | 相同内容重复读取仍只有一份 | 通过 | 通过 |
| L05 | 压缩后恢复同名Skill最近一次显式加载版本 | 未通过 | 未通过 |
| L06 | 没有加载的Skill不主动注入 | 通过 | 通过 |
| L07 | 预算不足明确阻断，不静默截断后继续模型调用 | 通过 | 通过 |

两份报告的suite_sha256一致，expected逐项相同；不是改完后调整标准。开发/验收分组用于控制迭代，不是真正盲测，作者与AI都能看到全部样本。7项不等于模型总体准确率，硬门不能用平均分抵消。

## 实际代码差异

```diff
diff --git a/src/min_agent/context.py b/src/min_agent/context.py
index 1141799..49ab766 100644
--- a/src/min_agent/context.py
+++ b/src/min_agent/context.py
@@ -32,0 +33 @@ class ContextManager:
+            requests = {}
@@ -34 +35,4 @@ class ContextManager:
-                if message.role == "tool" and len(message.content.encode()) > 2000:
+                requests.update((call.id, call.name) for call in message.tool_calls)
+                # Skill是执行规程，不交给普通工具摘要；仍接受整体窗口预算检查。
+                if (message.role == "tool" and len(message.content.encode()) > 2000
+                        and requests.get(message.tool_call_id) != "read_skill"):
diff --git a/tests/unit/test_skill_context.py b/tests/unit/test_skill_context.py
index fd38fda..3b76bd6 100644
--- a/tests/unit/test_skill_context.py
+++ b/tests/unit/test_skill_context.py
@@ -118,0 +119,9 @@ def test_non_full_success_results_are_left_unchanged(content):
+
+
+def test_long_skill_is_not_replaced_by_generic_tool_summary():
+    _, _, manager, capture = setup()
+    body = "# 规程\n" + "\n".join(f"步骤{i}：核对输入来源和处理结果。" for i in range(130))
+    messages = [Message(role="user", content="遵循规程")] + read("a", body=body) + read("b", body=body)
+    results = invoke(messages, manager, capture)
+    assert results[0].get("result") == body
+    assert results[1].get("skill_status") == "already_loaded"
```

原来所有较长工具结果都可能进入普通摘要，现在用tool_call_id找到对应请求名；read_skill绕过普通工具摘要。其他工具维持原流程。随后仍检查完整窗口预算，再进行已经实现的同内容去重。本次没有改系统提示、元数据解析或业务权限。

## 未通过项的原始观察

```json
{
  "id": "L03",
  "expected": {
    "model_called": true,
    "full_body_count": 1,
    "raw_history_unchanged": true
  },
  "observed": {
    "model_called": true,
    "full_body_count": 0,
    "raw_history_unchanged": true,
    "older_body_count": 0,
    "request_bytes_bound": 1353,
    "error": null
  }
}
```

```json
{
  "id": "L05",
  "expected": {
    "model_called": true,
    "full_body_count": 1,
    "older_body_count": 0,
    "raw_history_unchanged": true
  },
  "observed": {
    "model_called": true,
    "full_body_count": 0,
    "raw_history_unchanged": true,
    "older_body_count": 0,
    "request_bytes_bound": 1353,
    "error": null
  }
}
```

## 证据与不足

[改前全部输入、正文、投影与判定](../acceptance/s04-skill-lifecycle-baseline-20261023.json)；[改后全部输入、正文、投影与判定](../acceptance/s04-skill-lifecycle-after-20261023.json)。

本轮没有真实模型回答。Probe返回固定“离线探针答复”，仅用于观测真实ManagedModel输入，不证明模型遵守规程。压缩后状态由测试预置合法covered/hash/summary，尚未覆盖从长会话自动触发压缩的完整链路；自动触发、版本、来源级别及持续恢复需要后续集成验收。
