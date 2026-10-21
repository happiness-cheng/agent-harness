import httpx
import pytest
from uuid import uuid4

from min_agent.artifacts import ArtifactStore
from min_agent.sessions import SessionStore
from min_agent.tools.search import SearchService
from min_agent.tools.read_webpage import PublicFetcher, WebReader


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://[::1]/", "file:///secret", "https://user:pass@example.com/", "https://example.com:444/", "http://169.254.169.254/", "http://2130706433/", "http://localhost/"])
def test_private_and_ambiguous_targets_are_blocked_without_transport(url):
    called = []
    fetcher = PublicFetcher(transport=httpx.MockTransport(lambda request: called.append(request)),
                            resolver=lambda host: ["127.0.0.1"])
    with pytest.raises(ValueError): fetcher.fetch(url)
    assert called == []


def test_redirect_to_private_address_is_checked_and_dns_connection_is_pinned():
    requests = []
    def send(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/secret"})
    fetcher = PublicFetcher(transport=httpx.MockTransport(send), resolver=lambda host: ["93.184.216.34"])
    with pytest.raises(ValueError): fetcher.fetch("https://example.com/start")
    assert len(requests) == 1
    assert requests[0].url.host == "93.184.216.34" and requests[0].headers["host"] == "example.com"
    assert requests[0].extensions["sni_hostname"] == "example.com"


def test_read_saves_raw_and_returns_bounded_original_chunks_with_owner_checks():
    store = SessionStore(); context = store.create(uuid4()); other = store.create(context.owner_id)
    html = '<html><title>Evidence</title><body><nav>Advertisement</nav><article><h1>Source</h1>' + ''.join(
        '<p>Evidence paragraph %d: the condition is not optional. %s</p>' % (i, 'detail ' * 60) for i in range(30)
    ) + '</article></body></html>'
    fetcher = PublicFetcher(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=html,
        headers={"content-type": "text/html"})), resolver=lambda host: ["93.184.216.34"])
    artifacts = ArtifactStore(store, context)
    reader = WebReader(fetcher, artifacts)
    result = reader.read("https://example.com/article", question="condition", max_chars=1800)
    assert len(result["text"]) <= 1800 and result["truncated"]
    assert result["source"] == "https://example.com/article"
    raw = artifacts.get(result["artifact_id"])
    assert "Evidence paragraph" in raw["content"]
    with pytest.raises(PermissionError): ArtifactStore(store, other).get(result["artifact_id"])
    supplement = reader.read_artifact(result["artifact_id"], start=100, limit=400)
    assert len(supplement["text"]) <= 400 and supplement["start"] == 100


def test_search_normalizes_missing_fields_empty_results_and_errors():
    service = SearchService(backend=lambda query, limit: [
        {"title": "Source", "href": "https://example.com", "body": "summary"},
        {"title": "Missing URL"}, {"title": "Private", "href": "file:///secret"},
    ])
    result = service.search("public fact")
    assert len(result["results"]) == 1 and result["results"][0]["published_at"] is None
    assert result["results"][0]["retrieved_at"] and result["results"][0]["source"] == "example.com"
    assert SearchService(backend=lambda *args: []).search("public fact")["status"] == "empty"
    def failure(*args): raise TimeoutError("private-network-detail")
    assert SearchService(backend=failure).search("public fact")["status"] == "unavailable"
    with pytest.raises(ValueError): service.search(r"C:\Users\private secret sk-abcdef123456789")


def test_search_uses_fixed_public_endpoint_and_existing_parser_without_auto_fallback():
    from base64 import urlsafe_b64encode
    called = []
    encoded = urlsafe_b64encode(b"https://www.sqlite.org/lang_transaction.html").decode().rstrip("=")
    def send(request):
        called.append(request)
        assert request.url.host == "www.bing.com" and request.url.path == "/search"
        assert request.url.params["q"] == "SQLite"
        return httpx.Response(200, text=f'<html><li class="b_algo"><h2><a href="https://www.bing.com/ck/a?u=a1{encoded}">SQLite</a></h2><p>公开摘要</p></li></html>',
            headers={"Content-Type":"text/html"})
    result = SearchService(transport=httpx.MockTransport(send)).search("SQLite")
    assert result["status"] == "ok"
    assert result["results"][0]["url"] == "https://www.sqlite.org/lang_transaction.html"
    assert len(called) == 1


def test_search_rate_limit_and_redirect_do_not_silently_follow_or_retry():
    calls = []
    def send(request):
        calls.append(request)
        return httpx.Response(302, headers={"Location":"http://127.0.0.1/private"})
    service = SearchService(transport=httpx.MockTransport(send), max_queries=1)
    assert service.search("SQLite")["status"] == "unavailable"
    assert service.search("SQLite")["status"] == "budget_exhausted"
    assert len(calls) == 1
    limited = SearchService(transport=httpx.MockTransport(lambda request: httpx.Response(429)))
    assert limited.search("SQLite")["error"] == "rate_limited"


def test_search_rejects_clearly_unrelated_html_instead_of_claiming_success():
    content = '<li class="b_algo"><h2><a href="https://example.com">Unrelated sports</a></h2><p>Only football news</p></li>'
    result = SearchService(transport=httpx.MockTransport(lambda request:
        httpx.Response(200, text=content, headers={"Content-Type":"text/html"}))).search("SQLite transactions")
    assert result["status"] == "unavailable" and result["error"] == "query_mismatch"
    assert result["results"] == []


def test_gzip_webpages_are_decoded_with_a_hard_decompressed_output_cap():
    import gzip
    body = b"public fact " * 10000
    compressed = gzip.compress(body)
    def send(request):
        return httpx.Response(200, stream=httpx.ByteStream(compressed),
            headers={"Content-Type":"text/plain", "Content-Encoding":"gzip"})
    fetcher = PublicFetcher(transport=httpx.MockTransport(send),
        resolver=lambda host:["93.184.216.34"], max_bytes=1000)
    try:
        result = fetcher.fetch("https://example.com")
        assert len(result["text"]) == 1000 and result["truncated"]
        assert result["text"] == body[:1000].decode()
    finally: fetcher.close()


def test_complete_small_gzip_webpage_is_not_reported_as_truncated():
    import gzip
    compressed = gzip.compress(b"public condition is not optional")
    fetcher = PublicFetcher(transport=httpx.MockTransport(lambda request: httpx.Response(200,
        stream=httpx.ByteStream(compressed), headers={"Content-Type":"text/plain","Content-Encoding":"gzip"})),
        resolver=lambda host:["93.184.216.34"])
    try:
        result = fetcher.fetch("https://example.com")
        assert result["text"] == "public condition is not optional" and not result["truncated"]
    finally: fetcher.close()


def test_plain_page_over_limit_keeps_prefix_with_truncation():
    fetcher = PublicFetcher(transport=httpx.MockTransport(lambda request: httpx.Response(200,
        text="public fact " * 100, headers={"Content-Type":"text/plain"})),
        resolver=lambda host:["93.184.216.34"], max_bytes=100)
    try:
        result = fetcher.fetch("https://example.com")
        assert result["text"] == ("public fact " * 100)[:100] and result["truncated"]
    finally: fetcher.close()
