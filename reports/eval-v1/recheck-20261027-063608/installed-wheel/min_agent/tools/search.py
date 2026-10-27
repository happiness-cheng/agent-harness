"""真实公开网络搜索适配；只发送有限查询，不自动发送会话内容。"""

import re
from datetime import UTC, datetime
from urllib.parse import urlsplit
from threading import Lock
import httpx


class QueryMismatch(ValueError):
    pass


class SearchService:
    def __init__(self, *, backend=None, proxy=None, max_queries=12, transport=None):
        if transport is not None and proxy is not None:
            raise ValueError("离线传输不能与真实代理组合")
        self.backend = backend
        self.transport = transport
        self.proxy, self.max_queries, self.queries = proxy, max_queries, 0
        self._lock = Lock()

    def _public_query(self, query, limit):
        # 复用现有库的Bing字段和重定向链接解析，不调用其禁用后端/auto回退。
        from ddgs.engines.bing import Bing, unwrap_bing_url
        from lxml import html
        with httpx.Client(proxy=self.proxy, transport=self.transport, trust_env=False,
                          timeout=10, follow_redirects=False) as client:
            with client.stream("GET", "https://www.bing.com/search", params={"q": query, "count": limit},
                               headers={"User-Agent": "Mozilla/5.0", "Accept-Encoding": "identity"}) as response:
                response.raise_for_status()
                if response.headers.get("content-encoding", "identity").lower() not in {"identity", ""}:
                    raise ValueError("搜索响应未遵守无压缩预算")
                if "text/html" not in response.headers.get("content-type", ""):
                    raise ValueError("搜索未返回HTML")
                content = bytearray()
                for chunk in response.iter_bytes():
                    if len(content) + len(chunk) > 2_000_000: raise ValueError("搜索响应过大")
                    content.extend(chunk)
                text = content.decode(response.encoding or "utf-8", errors="replace")
        tree = html.fromstring(text)
        results = []
        for item in tree.xpath(Bing.items_xpath):
            record = {}
            for name, xpath in Bing.elements_xpath.items():
                record[name] = " ".join(str(value) for value in item.xpath(xpath)).strip()
            url = record.get("href", "")
            if url.startswith("https://www.bing.com/aclick?"): continue
            if url.startswith("https://www.bing.com/ck/a?"):
                try: record["href"] = unwrap_bing_url(url) or ""
                except (ValueError, UnicodeError): continue
            results.append(record)
        if not results:
            # HTML空解析可能是站点改版或校验页，不能伪称事实不存在。
            raise ValueError("搜索页未取得结果")
        tokens = set(re.findall(r"[a-z0-9_]{3,}", query.casefold())) - {
            "the", "and", "for", "with", "from", "how", "what", "does", "official", "documentation"}
        combined = " ".join(row.get("title", "") + " " + row.get("body", "") for row in results).casefold()
        if tokens and not any(token in combined for token in tokens):
            # 只做“完全没有查询词”的保守粗筛；通过也不代表结果已验证相关。
            raise QueryMismatch("返回内容完全不含查询词")
        return results

    def search(self, query, limit=5):
        if not isinstance(query, str) or not query.strip() or len(query) > 400:
            raise ValueError("公开查询需要1到400字符")
        if re.search(r"sk-[\w-]{8,}|(?i:bearer|api[_-]?key)\s*[:= ]|[A-Za-z]:\\|/Users/|/home/", query):
            raise ValueError("查询疑似包含凭据或本地路径，拒绝外传")
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError("结果数量需为1到10")
        with self._lock:
            if self.queries >= self.max_queries:
                return {"status": "budget_exhausted", "results": []}
            self.queries += 1
        try:
            if self.backend is None:
                raw = self._public_query(query, limit)
            else:
                raw = self.backend(query, limit)
            results = []
            for item in raw:
                url = item.get("href", item.get("url", ""))
                parts = urlsplit(url)
                if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
                    continue
                results.append({"title": str(item.get("title", ""))[:500], "url": url,
                    "source": parts.hostname, "summary": str(item.get("body", item.get("description", "")))[:1500],
                    "published_at": item.get("date"), "retrieved_at": datetime.now(UTC).isoformat()})
                if len(results) >= limit: break
            return {"status": "ok" if results else "empty", "results": results}
        except Exception as error:
            category = ("query_mismatch" if isinstance(error, QueryMismatch) else
                        "rate_limited" if isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 429 else
                        "timeout" if isinstance(error, (httpx.TimeoutException, TimeoutError)) else "search_failed")
            return {"status": "unavailable", "error": category, "results": []}
