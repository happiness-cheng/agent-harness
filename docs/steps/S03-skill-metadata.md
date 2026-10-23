# S03：完整description解析交付

日期：2026-10-23。[完整源码、diff、三份Skill、实际目录与逐步讲解](S03-readable.md)；[实际应用探针](../acceptance/s03-skill-metadata-20261023.json)。先前设计讨论保留在S03-description-and-lifecycle-design.md，本文只完成第一步。

## 结果

目录从name/description元数据生成，支持YAML多行>及|；完整保留描述，去掉前200字符截断。生成目录只解析有界头部，正文在read_skill时读取。当前布局不变，只增加YAML头部及必要校验。缺失/无效字段拒绝，绝不拿标题充当描述。

采用PyYAML==6.0.3，官方PyPI版本已核对，项目依赖显式声明并安装到现有.venv。安全Loader拒绝重复键和别名，未知Python对象标签也不会执行。没有启用Skill中的权限配置、动态脚本或新工具。

实际应用探针：四次注册读取12个头部，全文读取从15次降为3次（对应实际三次read_skill）；九项检查全部通过，7份模型输入记录完整保留。目录没有Skill正文，读取后只追加所选正文，同名同内容重复仍去重，新会话不继承。

## 测试与交付边界

新增元数据测试先运行：18 failed,1 passed，复现读首行/截断/不拒绝无效头部等问题。实现后19 passed in 0.41s。覆盖单行、折叠多行、保留换行、400字符完整保留、正文非法UTF-8但目录可生成、禁止目录调用全文read、无头部/缺字段/空字段/类型错/名称不符/重复键/错误YAML/危险标签/别名/超长头部和描述，以及显式读取全文。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest -q tests/unit/test_skill_metadata.py
.\.venv\Scripts\python.exe -X utf8 scripts/audit_skill_disclosure.py --report '<新报告路径>'
.\.venv\Scripts\python.exe -X utf8 -m pytest -q --tb=short
.\.venv\Scripts\python.exe -X utf8 -m py_compile src/min_agent/skills.py scripts/audit_skill_disclosure.py tests/unit/test_skill_metadata.py
.\.venv\Scripts\python.exe -X utf8 -m pip check
.\.venv\Scripts\python.exe -X utf8 -m pip wheel . --no-deps --no-build-isolation --wheel-dir '<输出目录>'
```

- 本机全量：1 failed,978 passed,3 deselected,1 warning in 35.01s。仍为上一轮已定位的记忆原文回查context_limit基线问题；未顺便改记忆模块，不声称本机全量通过。
- 公开工作树指定自身src为PYTHONPATH：944 passed,3 deselected,1 warning in 31.43s；实际应用探针九项通过。两边警告均为已有TestClient/httpx弃用提示。
- 语法检查通过；pip check：No broken requirements found。
- 两工作区wheel构建通过。公开wheel校验PyYAML依赖声明及迁移后的Skill元数据均已打包；未发布或部署。未执行独立环境完整安装。
- 本轮没有模型调用或真实回答，不消耗之前剩余额度。旧S01/S02报告未改写；真实评估离线测试改用有元数据的新S03夹具，未来复验也应选择新报告。

## 审查与剩余限制

主代理自审PASS（该功能及公开分支交付）：需求满足、错误边界、安全数据解析、既有改动保留、构建及回归均核对；没有使用子代理。主工作区全量仍有已记录失败，不作整体通过声明。敏感模式和diff检查执行，不等于完整安全审计；类型/Lint SKIP（项目未配置）。全局code-quality.md缺失，按已给AGENTS及现有风格执行。

此次严格要求元数据，旧无头部文件不再自动生成目录，需要迁移；目录中一个无效文件会明确报错，尚无逐项隔离诊断界面。总目录大小、标准SKILL.md目录和三级资源读取未扩展。长Skill的普通工具压缩策略和压缩后自动恢复仍未实现，留到下一步。

作者要求“每次修改后看代码学习”：阅读版提供完整代码、实际输入与diff；最终说明展示核心解析路径及原因，不只报告测试数字。

依据：
- https://agentskills.io/specification#frontmatter ，字段与多层材料格式。
- https://agentskills.io/specification#description-field ，非空及1024字符上限。
- https://pypi.org/project/PyYAML/6.0.3/ ，固定解析器依赖。
