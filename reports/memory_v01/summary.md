# Adrian Memory v0.1：实际实验与交付报告

**本轮实现和离线实验已执行；完整Runtime回归仍有一项任务开始前已存在的失败，真实模型任务质量未验收。没有外部调用、私人数据外传、生产schema迁移或远端发布。**

## 1. 代码审计与改动

根目录任务书原先不存在，按用户提供附件原文保存为Adrian_Memory_v0.1_Implementation_Brief.md。以本地代码为准，保留原有提示词、Skill、Context与State Bar改动。新增long_memory包，最小接线到SessionStore、SQLiteStorage、HarnessApplication、ToolRegistry、Runtime动态尾部和既有Web middleware。

Schema未新增SQL表，使用内部owner权威快照；MemoryJournal原会话范围不改变。FTS5为过滤后临时索引，向量服务NOT_RUN。确认是用户API行为，模型只有只读工具；没有自动语义画像写入或完整审核UI。当前可信API/窄语法是可用确认通路。

源码：src/min_agent/long_memory/{models,store,retrieval,selection,integration,evaluation}.py。依赖PyYAML已在原本地pyproject中声明，本轮补齐两个requirements锁文件的6.0.3条目；未升级其他依赖。

本地实验分支feat/adrian-memory-v01-local，父基线e1fa7b76488672591cd47806067e5b86a48e00fb单独保存此前本地代码；Memory差异另行提交。报告保存实际运行代码散列和数据散列，不把主分支220f6ec误当成完整的运行源码版本。

## 2. 实际运行与测试

| 阶段 | 命令/证据 | 实际结果 |
| --- | --- | --- |
| P0基线 | python -X utf8 -m pytest -q --tb=short | 1124 passed、1既有failed、3 live deselected、1 warning，75.93s |
| 新功能定向 | 7个新增测试文件 | 51 passed、1既有warning |
| 主目录完整回归首轮 | regression.json | 1168 passed、1既有failed、3 deselected、1 warning，110.62s；当时尚未加入最后4项回归 |
| 最终复核目录完整回归 | release-regression.json | 1175 passed、1既有failed、3 deselected、1 warning，110.784s |
| 语法/依赖 | compileall；pip check | PASS；No broken requirements found |
| 类型/lint | 本机mypy、ruff探测 | 未配置/未安装，SKIP，不把语法检查当类型检查 |
| wheel/独立安装 | pip wheel --no-index；安装到独立target并核验import路径 | PASS；合成案例Core证据覆盖1，非模型质量 |

既有失败：test_explicit_source_read_and_structured_recall_are_not_silently_head_truncated，预期completed、实际partial。P0已复现，当前仍在，不用新测试通过掩盖，不宣称全量验收通过。Starlette/httpx弃用warning保持。

51项定向覆盖：owner/内部快照、候选确认与失效、来源角色、同源证据、不同情景、ID融合、全局兜底、具体例外/当前指令、目标历史、Core预算、压缩移除后的重注、真实Web Cookie/CSRF、实际模型请求接线、删除派生物及失败门控、SQLite事务回滚、评测gold隔离和私有路径越界。脚本模型仅证明接线，不等于自然语言质量。

## 3. 同池同预算实验

已跑60题，每层20；原句episode共13042条，跨会话中间回放80次，未来记忆门检查失败0次。approved_fixture模拟批准比例100%，不等于用户真实批准。实际模型/Judge统一NOT_RUN。每组Core预算2048 UTF-8序列化字节上界；主比较检索工具预算均0。另行报告同条件Top8检索指标。

下表仅为Core必要事实覆盖均值。机器可读gold只有8题；分母为该层有标注题，不是全20题。没有标注的52题质量NOT_RUN。

| 层级（有gold/运行题） | NONE | RECENCY | FREQUENCY | GOAL_VALUE | 真实任务分数 |
| --- | --- | --- | --- | --- | --- |
| L1（4/20） | 0.00% | 12.50% | 12.50% | 0.00% | NOT_RUN |
| L2（2/20） | 0.00% | 0.00% | 16.67% | 0.00% | NOT_RUN |
| L3（2/20） | 0.00% | 12.50% | 12.50% | 12.50% | NOT_RUN |
| 合计（8/60） | 0.00% | 9.38% | 13.54% | 3.12% | NOT_RUN |

GoalValue在这批原句episode/小预算代理指标上没有胜出，不能宣称改进成功；也不能据此证明Frequency普遍更好或Goal-Value设计原则无效。书中没有可直接使用的显式Goal标签，本轮不从gold制造目标；当前价值规则退化为可解释的缺失代价/证据/相关性信号，原句长短及元数据开销也影响入选。

同一检索器在8题上的fact Recall@8均值为26.04%，first-relevant MRR=0.24375。三种有记忆策略共享它，因此该数值不能拿来证明某种Core策略的优势。Core零覆盖不必然导致任务失败，保险案例检索覆盖1而Core为0就是两层职责不同的例子。

配对差异只比较8题的Core事实覆盖，非真实任务收益：

| GoalValue相对 | 胜 | 平 | 负 | 平均差（百分点） |
| --- | --- | --- | --- | --- |
| RECENCY | 0 | 7 | 1 | -6.25 |
| FREQUENCY | 0 | 6 | 2 | -10.42 |

样本公开、只有8题有事实标注，不给总体显著性或因果收益结论；真实回答precision/recall/reasoning/proactivity、总体成功率、幻觉、情景语义适用率、例外语义遗漏率均NOT_RUN。原始报告、评分权重及gold保留。

## 4. 全部实际检索漏召案例

以下7题Top8没有覆盖全部gold事实，是组件层漏召；不是7个已证实的模型任务失败。完整ID/路径/排序信号见failure_cases.json及book-reviewed.json.gz。

| 案例 | Top8事实覆盖 | GoalValue Core覆盖 |
| --- | --- | --- |
| layer1_01_bank_account | 0.5000 | 0.0000 |
| layer1_03_medical_appointment | 0.0000 | 0.0000 |
| layer1_04_airline_booking | 0.0000 | 0.0000 |
| layer2_01_multiple_vehicles | 0.3333 | 0.0000 |
| layer2_03_multiple_credit_cards | 0.0000 | 0.0000 |
| layer3_01_travel_coordination | 0.2500 | 0.2500 |
| layer3_04_warranty_coordination | 0.0000 | 0.0000 |

可从证据直接确认：pool有来源但Top8覆盖不足；Core排序的explicit_active_goal信号为0；决策代价关键词规则和完整元数据预算会影响选择。词项FTS缺乏同义/实体关系理解、raw episode粒度过粗是待验证解释，不伪装成已完成的消融因果结论。

本轮自审复现后修复的实现缺陷：删除只清主记录却留下派生回答；旧来源候选可在删除后持旧token确认；相同会话私有文字被跨会话合并；INFERRED没有disputed；具体例外没有压过一般偏好；弱主题命中阻断全局兜底；NONE仍暴露跨会话只读工具。均新增先失败再通过的回归，未改gold或importance权重。

## 5. 安全、私有数据与开销

实际合成机制控制：

```json
{
  "unconfirmed_long_term_writes": 0,
  "bad_confirmation_denied": 1,
  "confirmed_positive_control": 1,
  "cross_owner_returns": 0,
  "deleted_returns": 0,
  "expired_returns": 0,
  "unauthorized_goal_pauses": 0,
  "fusion_raw_paths": 6,
  "fusion_unique_ids": 2,
  "first_memory_retained_paths": 5,
  "first_injection": 1,
  "duplicate_injection": 0,
  "reinjection_after_removal": 1,
  "live_model_calls": 0,
  "scope": "synthetic deterministic mechanism controls; not model-quality or generalized safety proof"
}
```

计数0仅代表所运行的机制控制，没有被泛化为所有输入安全。确认正向控制1保证不是空库假安全。ID融合6路径→2ID，M1五条路径保留；源为vector的路径是合成性质测试，不代表实际运行向量检索。

私有目录实际106份会话、1903角色边界，解析与索引数量不一致文件0；Markdown 3146457字符。按整段updated保守划分74/32，不编造逐消息时间。规则发现27条待评审提示，项目外只保存来源路径/范围/散列，不复制原文；它们不是27条已确认有用记忆。正式导入0、私有外部调用0，语义提取/个人价值gold/留出段任务收益NOT_RUN。

删除覆盖本模块权威内容、受控派生回答、工具参数、归档和缓存；原始用户消息/导出文件/外部副本未删除，不保证磁盘取证或备份擦除。没有触碰生产数据库。需要删除原始资料时须另行明确范围，不能将逻辑Memory删除宣称为完全遗忘。

最终本地60题选择与评分耗时86.448s；与回归并行运行，不作为稳定延迟benchmark。selection_ms包含确定性评分开销，不是纯排序/网络延迟。各组provider Token、真实调用延迟和费用测量均NOT_RUN，新增外部请求0。UTF-8字节上界不能冒充Token费用或KV cache命中率。

## 6. 复跑与代码阅读

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
.venv/Scripts/python.exe -X utf8 -m pytest -q --tb=short
.venv/Scripts/python.exe -X utf8 scripts/verify_adrian_memory.py --report <新controls.json>
.venv/Scripts/python.exe -X utf8 scripts/eval_adrian_memory.py --book-root <参考仓库或评测目录> --approval approved_fixture --limit 60 --core-budget 2048 --report <新report.json>
# --private-root 仅作本地结构适配；--private-review-output 必须指向项目外的新文件
```

证据文件使用排他创建，不覆盖旧报告。完整JSON同时保留原始文件和gzip归档，压缩仅减小体积，没有删记录；SHA256见summary.json。必要依赖使用requirements锁文件；不自动下载模型或embedding。

核心实现：同一本书从五条路径找到，也只占一个位置；不同书号保留独立位置。以下直接摘自retrieval.py：

```python
def fuse_hits(hits, *, top_k):
    combined={}
    for hit in hits:
        hit=SearchHit.model_validate(hit.model_dump())
        row=combined.setdefault(hit.memory_id,dict(memory_id=hit.memory_id,score=hit.score,paths=[],reasons=[]))
        row['score']=max(row['score'],hit.score)  # 不因属于多个组而累加高分。
        path=dict(source=hit.source,topic_id=hit.topic_id,score=hit.score)
        if path not in row['paths']: row['paths'].append(path)
        row['reasons']=sorted(set(row['reasons'])|set(hit.reasons))
    return sorted(combined.values(),key=lambda r:(-r['score'],r['memory_id']))[:top_k]
```

完整设计见docs/MEMORY_ARCHITECTURE_V01.md；协议见docs/MEMORY_EVAL_PROTOCOL.md。P0—P4已具备可运行落点；P5以这些实跑记录交付，不将未运行项目说成完成。

## 7. 下一步优先级

1. 先做小批用户审核的语义记忆卡片与明确Goal/缺失代价标注，冻结后再比较；不要用当前raw episode结果证明最终个人Memory能力。
2. 在明确模型/数据/请求预算后跑真实成对任务与统一Judge，并独立报告提供材料与回答质量；保留当前失败，不调gold追胜率。
3. 修复既有长原文读取/上下文窗口回归，并完善原始资料删除范围、审核UI和索引可扩展性；本轮不顺手改旧压缩模块。

## 最终版本与补强记录

主实验精确源码在experiment-source.zip，14份文件与book-reviewed报告散列逐一吻合；source-version-receipt.json列出后续差异。最后仅LongMemory.delete、assemble_context及Runtime绑定中的删除依赖/工具返回形状改变；主实验的事实池构建、records、goals、importance、rank_for_core、retrieval.search和评分函数未变。后续版本通过51项定向及1175通过/1既有失败的完整回归，不将旧实验冒充最新代码重跑。

补强包括清除从派生回答再次形成的候选/记忆、孤立主题/场景，以及索引元数据的删除依赖；模型可见搜索结果只返回短ID，避免触发通用工具摘要，完整路径留在本地trace。初版报告和失败记录均保留。

最终wheel SHA256：8976c557391a38e6dc00eb2b8f7f30e0e90b21dced1029dd8cfe8b44a16c8969。13份核心源码与wheel内容逐字节相同，独立target安装及合成smoke通过。

进一步限制：生产observed_at表示本系统提候选时的观察时刻，原Runtime未提供逐消息原始时戳；相对日期需审核明确有效期。Goal当前主要驱动排序并可经可信接口查询，尚无通用自然语言目标识别/完整UI。Frequency有可信usage接口，书中使用精确原句重复代理，尚非语义提及统计。过长条目会因预算跳过，本版没有跨会话长正文分页工具。拒绝采用精确指纹，不宣称能识别所有改写。

最终收尾核对：51项定向通过，最终完整回归1175通过/1项P0失败/3 live deselected。详见review.md；未删测试、未调gold，未远端发布。模型可见检索响应最大30条ID时保持在通用摘要门槛以下。
