"""向两份新隔离源码装配相同资料入口；不修改研究候选差异。"""
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
base = HERE/'baseline/workspace/src/min_agent'
shutil.copy2(HERE/'implementation/research_resources.py', base/'research_resources.py')
for variant in ('baseline','candidate'):
    directory = HERE/variant/'workspace/src/min_agent'
    path = directory/'application.py'
    text = path.read_text(encoding='utf-8')
    assert 'mount_research_collection' not in text
    text = text.replace('from min_agent.research import ResearchService',
                        'from min_agent.research import ResearchService\nfrom min_agent.research_resources import ResearchCollection')
    text = text.replace('self._pool = ThreadPoolExecutor', 'self._research_collections = {}\n        self._pool = ThreadPoolExecutor')
    text = text.replace('    def create_session(self, label="新会话"):', '''    def mount_research_collection(self, session_id, collection):
        """可信应用方在空闲Session挂载资料；不会授权网络或复制正文到聊天。"""
        context = self.context(session_id)
        if not isinstance(collection, ResearchCollection):
            raise TypeError("需要可信ResearchCollection")
        with self._lock:
            if str(context.session_id) in self._running:
                raise RuntimeError("运行中不能更换资料后端")
            self._research_collections[context.session_id] = collection
            return self.research_resources(session_id)

    def research_resources(self, session_id):
        """从真实绑定和Session存储投影目录；不包含正文或验收标准。"""
        context = self.context(session_id)
        with self._lock:
            collection = self._research_collections.get(context.session_id)
            artifacts = self.sessions.extension(context, "artifacts", {})
            return {"controlled_collection": collection.metadata() if collection else None,
                    "session_artifacts": [{"artifact_id":key,"source":value["source"],"kind":value["kind"]}
                                          for key,value in artifacts.items()],
                    "allow_public_web":bool(self.sessions.extension(context,"allow_public_web",False)),
                    "notice":"目录仅表示已登记资源；不授予权限；未列出的内部资料不代表可用。"}

    def create_session(self, label="新会话"):''')
    old = '''            with self._lock:
                if context.session_id not in self._fetchers:
                    self._fetchers[context.session_id] = PublicFetcher()
                fetcher = self._fetchers[context.session_id]
            search = SearchService(proxy=self.proxy)
            reader = WebReader(fetcher, artifacts)'''
    new = '''            with self._lock:
                collection = self._research_collections.get(context.session_id)
                if collection is None:
                    if context.session_id not in self._fetchers:
                        self._fetchers[context.session_id] = PublicFetcher()
                    fetcher = self._fetchers[context.session_id]
            search = collection if collection is not None else SearchService(proxy=self.proxy)
            reader = collection.reader(artifacts) if collection is not None else WebReader(fetcher, artifacts)'''
    assert old in text
    text = text.replace(old,new)
    old = '        registry.register(ToolDefinition("research", "针对已允许的公开问题自主搜索、阅读与补查，返回有原文位置/限制/冲突的证据包；不复制私人聊天、不写业务或递归委派。", _Question, research))'
    new = '''        description = "针对已允许的公开问题自主搜索、阅读与补查，返回有原文位置/限制/冲突的证据包；不复制私人聊天、不写业务或递归委派。"
        if context.session_id in self._research_collections:
            description += "\\nRESOURCE_CATALOG_JSON:" + json.dumps(self.research_resources(context.session_id), ensure_ascii=False)
        registry.register(ToolDefinition("research", description, _Question, research))'''
    assert old in text
    path.write_text(text.replace(old,new), encoding='utf-8')
    if variant != 'baseline':
        shutil.copy2(base/'research_resources.py', directory/'research_resources.py')
