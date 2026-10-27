"""公开地址检查、DNS固定、限量流读取与有原文引用的正文片段。"""

from ipaddress import ip_address
from urllib.parse import urlsplit, urljoin
import socket
import re
import zlib
from threading import Lock

import httpx


def _resolve(host):
    return list({item[4][0] for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)})


class PublicFetcher:
    def __init__(self, *, transport=None, resolver=None, proxy=None, max_requests=20, max_bytes=2_000_000):
        if proxy is not None:
            raise ValueError("固定DNS的网页读取只支持直连；代理隧道未验证SNI边界")
        self.resolver, self.max_requests, self.max_bytes = resolver or _resolve, max_requests, max_bytes
        self.requests, self._lock = 0, Lock()
        self.client = httpx.Client(transport=transport, proxy=proxy, timeout=10, follow_redirects=False, trust_env=False)

    def _target(self, url):
        try:
            if not isinstance(url, str) or len(url) > 4000 or re.search(r"sk-[\w-]{8,}|(?i:api[_-]?key|token|password)=", url):
                raise ValueError("URL过长或疑似包含凭据")
            parts = urlsplit(url)
            if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
                raise ValueError("URL格式不允许")
            if parts.port not in {None, 80 if parts.scheme == "http" else 443} or "%" in parts.hostname:
                raise ValueError("端口或地址格式不允许")
            host = parts.hostname.rstrip(".").encode("idna").decode()
            if host.lower() == "localhost" or host.lower().endswith((".localhost", ".local", ".internal")):
                raise ValueError("禁止本地地址")
            try:
                addresses = [str(ip_address(host))]
            except ValueError:
                addresses = self.resolver(host)
            if not addresses or any(not ip_address(address).is_global for address in addresses):
                raise ValueError("禁止非公开地址")
            addresses = sorted(addresses, key=lambda address: ip_address(address).version)
            return httpx.URL(url).copy_with(host=addresses[0]), host, httpx.URL(url).netloc.decode()
        except (ValueError, OSError, UnicodeError):
            raise ValueError("URL或解析地址不允许公开获取") from None

    def fetch(self, url):
        for _ in range(4):
            target, sni, host = self._target(url)
            with self._lock:
                if self.requests >= self.max_requests: raise ValueError("网页累计请求预算不足")
                self.requests += 1
            with self.client.stream("GET", target, headers={"Host": host, "User-Agent": "AgentHarness/0.1", "Accept-Encoding": "identity"},
                                    extensions={"sni_hostname": sni}) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location: raise ValueError("重定向缺目标")
                    url = urljoin(url, location); continue
                response.raise_for_status()
                encoding = response.headers.get("content-encoding", "identity").lower()
                if encoding not in {"identity", "", "gzip", "deflate"}:
                    raise ValueError("服务返回未支持的压缩格式")
                mime = response.headers.get("content-type", "").split(";", 1)[0].strip()
                if mime not in {"text/html", "text/plain", "application/xhtml+xml"}:
                    raise ValueError("只支持HTML或纯文本")
                data, truncated, raw_size = bytearray(), False, 0
                decoder = (zlib.decompressobj(16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS)
                           if encoding in {"gzip", "deflate"} else None)
                chunks = response.iter_raw(chunk_size=65536) if decoder is not None else response.iter_bytes()
                for raw in chunks:
                    raw_size += len(raw)
                    if decoder is not None and raw_size > self.max_bytes:
                        truncated = True; break
                    available = self.max_bytes - len(data)
                    # decompress的max_length在分配输出前生效，不能先无限解压再切片。
                    part = decoder.decompress(raw, available + 1) if decoder is not None else raw
                    data.extend(part[:available])
                    if len(part) > available:
                        truncated = True; break
                    if decoder is not None and decoder.unused_data:
                        raise ValueError("不接受拼接压缩流")
                if decoder is not None and not truncated and not decoder.eof:
                    raise ValueError("压缩正文不完整")
                return {"url": url, "text": bytes(data).decode(response.encoding or "utf-8", errors="replace"),
                        "truncated": truncated, "content_type": mime}
        raise ValueError("重定向次数过多")

    def close(self):
        self.client.close()


class WebReader:
    def __init__(self, fetcher, artifacts):
        self.fetcher, self.artifacts = fetcher, artifacts

    def read(self, url, *, question="", max_chars=4000):
        if type(max_chars) is not int or not 200 <= max_chars <= 8000:
            raise ValueError("正文预览预算需要200到8000字符")
        fetched = self.fetcher.fetch(url)
        from trafilatura import extract
        text = (extract(fetched["text"], url=fetched["url"], include_comments=False, include_tables=True,
                        favor_recall=True, output_format="txt") or "") if fetched["content_type"] != "text/plain" else fetched["text"]
        if not text.strip(): raise ValueError("未提取到正文，不能作为已读证据")
        identity = self.artifacts.save(text, source=fetched["url"], kind="webpage",
                                       metadata={"fetch_truncated": fetched["truncated"], "raw_html": fetched["text"]})
        paragraphs = text.splitlines(); tokens = re.findall(r"\w+", question.casefold())
        best = max(range(len(paragraphs)), key=lambda index: (
            sum(token in paragraphs[index].casefold() for token in tokens)
            + (2 if paragraphs[index].rstrip("¶ ").casefold() == question.casefold().strip() else 0)))
        start_line = max(0, best - 1)
        start = sum(len(line) + 1 for line in paragraphs[:start_line])
        result = self.artifacts.read(identity, start=start, limit=max_chars)
        result["truncated"] = fetched["truncated"] or start > 0 or result["truncated"]
        result["complete_fetch"] = not fetched["truncated"]
        return result

    def read_artifact(self, artifact_id, *, start=0, limit=4000):
        return self.artifacts.read(artifact_id, start=start, limit=limit)
