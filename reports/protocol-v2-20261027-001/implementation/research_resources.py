"""可信调用方挂载的只读受控资料；目录不含正文，不回退真实网络。"""
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ResearchDocument:
    document_id: str
    title: str
    locator: str
    content: str
    nature: str
    coverage: str
    origin_url: str = ''

    def __post_init__(self):
        for value, maximum in ((self.document_id,80),(self.title,160),(self.locator,2000),
                               (self.content,16000),(self.coverage,300),(self.origin_url,2000)):
            if not isinstance(value,str) or len(value)>maximum:
                raise ValueError('资料字段无效或超出受控大小')
        if not all((self.document_id.strip(), self.title.strip(), self.content.strip(), self.coverage.strip())):
            raise ValueError('资料标识、标题、正文和覆盖范围不能为空')
        url = urlsplit(self.locator)
        if url.scheme != 'https' or not url.hostname or url.username or url.password:
            raise ValueError('受控定位须为无认证信息的HTTPS URL')
        if self.nature not in {'public_archive_excerpt','synthetic_document'}:
            raise ValueError('需要明确资料性质')

    def metadata(self):
        return {key:getattr(self,key) for key in ('document_id','title','locator','nature','coverage','origin_url')}


@dataclass(frozen=True)
class ResearchCollection:
    collection_id: str
    label: str
    documents: tuple[ResearchDocument,...] = ()

    def __post_init__(self):
        if not isinstance(self.collection_id,str) or not 1 <= len(self.collection_id) <= 80:
            raise ValueError('集合标识无效')
        if not isinstance(self.label,str) or not 1 <= len(self.label) <= 160:
            raise ValueError('集合名称无效')
        if type(self.documents) is not tuple or len(self.documents)>10 or any(
                not isinstance(doc,ResearchDocument) for doc in self.documents):
            raise ValueError('需要最多10份可信资料的不可变元组')
        for field in ('document_id','locator'):
            if len({getattr(doc,field) for doc in self.documents}) != len(self.documents):
                raise ValueError('资料标识或定位重复')

    def metadata(self):
        return {'collection_id':self.collection_id,'label':self.label,
                'kind':'controlled_read_only_collection',
                'availability':'当前应用生命周期内已挂载；不属于当前Session既有历史归档',
                'access':'research的只读资料后端；仍须应用allow_public_web许可；无真实外网回退',
                'documents':[doc.metadata() for doc in self.documents]}

    def search(self, query):
        if not isinstance(query,str) or not 1 <= len(query) <= 400:
            raise ValueError('查询无效')
        # 只检索公开目录字段；不把正文或答案作为隐蔽的路由提示。
        lowered = query.casefold()
        matches = [doc for doc in self.documents if doc.title.casefold() in lowered
                   or doc.document_id.casefold() in lowered or doc.locator.casefold() in lowered]
        return {'status':'ok','results':[{'title':doc.title,'url':doc.locator,
                'summary':doc.coverage,'nature':doc.nature} for doc in matches],
                'scope':'controlled_collection_only','external_network':False}

    def reader(self, artifacts):
        collection = self
        class ScopedReader:
            def read(self, url, *, question='', max_chars=4000):
                doc = next((doc for doc in collection.documents if doc.locator==url),None)
                if doc is None:
                    raise PermissionError('来源未挂载；禁止回退外网')
                identity = artifacts.save(doc.content, source=doc.locator,
                    metadata={'collection_id':collection.collection_id, **doc.metadata()})
                return artifacts.read(identity, limit=max_chars)

            def read_artifact(self, identity, *, start=0, limit=4000):
                return artifacts.read(identity, start=start, limit=limit)
        return ScopedReader()
