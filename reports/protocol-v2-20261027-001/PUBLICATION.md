# GitHub与本地核验、发布范围

用户在本轮追加明确授权：“继续，并且核验github上的内容与本地内容，推送github”。这改变了原“不提交/推送”的边界，不授权新增Provider或Codex CLI生成。

git fetch origin后：原本地HEAD为220f6ec，GitHub origin/main为fd360064049a76a81e8cffce1283865268290cf0，领先本地2条State Bar相关提交。逐个读取远端tree与本地实际文件（包括尚未跟踪文件，不能直接将git diff中的伪删除当成事实）：239个远端已跟踪文件全部在本地存在，归一化换行后213个相同、26个不同。明细见github-vs-local-before.json。

基于最新origin/main新建codex/adrian-v11-source-entry-v2发布分支，再按显式manifest复制本地src/tests/scripts/docs/项目配置及经过筛选的实验文本、图和脱敏结果。没有reset/rebase/force，未自动合并main；主工作树原有改动保留。发布的是当前本地开发快照和本次v2离线实验材料，不将报告内的候选自动合入生产src。

发布快照生产src与本地原src字节哈希相同：c953ec972ec5d6825d009509e5bbf203b8e3b8dcba3456f26e5be2fcf24a60f7。README增加实验状态/报告入口并规范换行，.gitignore增加隔离工作树/正式Memory数据排除，均在发布工作树完成。历史报告保留当时失败与未提交说明，不改写成成功。

默认git diff --check发现原Windows进程模块已有一个文件尾空行（git-diff-check-default.txt），这是非功能性WARN，不改已验基线或伪称默认检查全绿。曾试探移除，AST相同，但为保持源码/重放hash一致已经回退该发布副本格式探测（publication-formatting.json），源文件字节未变。正式行尾空白检查使用明确的core.whitespace=-blank-at-eof,blank-at-eol,space-before-tab,cr-at-eol；默认检查仅允许该已记录EOF警告，其余错误仍阻止提交。报告内历史patch格式不改写。

发布时增加.gitattributes，源码、报告及docs/acceptance禁止Git自动换行转换；这是为了保留冻结源码/证据的原始字节，不让Windows autocrlf改变hash。暂存后还须按实际Git blob重新计算src hash，而不是仅看工作树文件。根主工作树和其Codex配置均不修改。

不上传：凭据、.env、所有SQLite/DB、正式Memory/业务数据、reports/memory_v01、隔离Git工作树、重复源码快照、构建产物与缓存。原始运行数据库继续本地保留。公开文本没有非空provider_state或密钥样式命中；扫描不是对所有可能个人信息的数学证明，范围来自本批合成/公开资料与已有脱敏记录。完整选择列表及hash见publication-manifest.json；publication-security.json保存扫描结果。

发布前验证：主工程快照1259 passed、3 deselected、1 warning（241.29秒，full-publication.txt）；隔离候选1268 passed、3 deselected、1 warning（251.38秒）；原20题15 PASS/2 FAIL/3 UNKNOWN且符合原预期。compileall、pip --isolated check和无联网wheel构建成功。Type/Lint无项目配置，SKIP。相关接口、权限、范围和Windows脚本由独立审查及复现另外核对。

只使用显式路径列表git add --pathspec-from-file，不使用git add -A；提交采用feat:格式，无Co-Authored-By。普通git push -u origin codex/adrian-v11-source-entry-v2；执行后以git ls-remote核对远端head与实际commit一致。本地github-push-receipt.json记录最终命令和远端收据；该文件在提交后产生，不声称属于其自己的提交内容。

## 核心接线

目录像资料馆的书目：让读者知道有什么书及在哪里，正文仍须真实借阅。实际实现位于隔离src/min_agent/application.py及归档entry-v2.patch，核心如下：

```python
# 两版都从真实Session绑定取受控后端，挂载不改变许可。
if not self.sessions.extension(context, "allow_public_web", False):
    raise AuthorizationRequired("network_not_authorized")
collection = self._research_collections.get(context.session_id)
search = collection if collection is not None else SearchService(proxy=self.proxy)
reader = collection.reader(artifacts) if collection is not None else WebReader(fetcher, artifacts)
# 仍进入原ResearchService、Runtime和共享预算。
result = ResearchService(raw_model, search, reader, artifacts, input_window=self.window).run(...)
# 只在实际挂载时，通过真实工具Schema公开目录。
if context.session_id in self._research_collections:
    description += "\nRESOURCE_CATALOG_JSON:" + json.dumps(self.research_resources(context.session_id), ensure_ascii=False)
registry.register(ToolDefinition("research", description, _Question, research))
```

这是摘录，完整可运行源码由implementation/和wire.py装配。首轮Schema不含正文，模型是否实际选用入口依旧待真实实验验证。
