"""Offline tests for the polite Crawler + URL/domain helpers + rate limit + robots."""

from __future__ import annotations

import socket

import httpx
import pytest

from general_ludd.web import ssrf_client as sc
from general_ludd.web.crawl import (
    Crawler,
    CrawlPolicy,
    normalize_url,
    registrable_domain,
)
from general_ludd.web.ratelimit import TokenBucket

_PUBLIC_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    def _fake(host, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_PUBLIC_IP, 443))]

    monkeypatch.setattr(sc.socket, "getaddrinfo", _fake)


# -- registrable domain -------------------------------------------------------

def test_registrable_domain_basic():
    assert registrable_domain("www.example.com") == "example.com"
    assert registrable_domain("example.com") == "example.com"
    assert registrable_domain("a.b.example.com") == "example.com"


def test_registrable_domain_multi_suffix():
    assert registrable_domain("shop.example.co.uk") == "example.co.uk"
    assert registrable_domain("foo.github.io") == "foo.github.io"


def test_registrable_domain_ip():
    assert registrable_domain("10.0.0.1") == "10.0.0.1"


# -- url normalize ------------------------------------------------------------

def test_normalize_dedup_and_default_port():
    a = normalize_url("https://Example.com:443/path/?b=2&a=1#frag")
    b = normalize_url("https://example.com/path?a=1&b=2")
    assert a == b


def test_normalize_rejects_non_http():
    assert normalize_url("ftp://example.com/x") is None
    assert normalize_url("mailto:a@b.com") is None


# -- token bucket -------------------------------------------------------------

def test_token_bucket_nonblocking_exhaust():
    b = TokenBucket(rate=1.0, capacity=1.0)
    assert b.take(sleep=False) == 0.0  # first token free
    assert b.take(sleep=False) == -1.0  # exhausted, non-blocking gives up


# -- crawler ------------------------------------------------------------------

_PAGES = {
    "/": "<html><body><a href='/a'>a</a><a href='/b'>b</a>"
         "<a href='https://other.test/x'>off</a></body></html>",
    "/a": "<html><body><a href='/c'>c</a></body></html>",
    "/b": "<html><body>leaf b</body></html>",
    "/c": "<html><body>leaf c</body></html>",
}


def _site_transport(robots_body: str | None = None, status_for=None):
    def handler(req):
        path = req.url.path
        if path == "/robots.txt":
            if robots_body is None:
                return httpx.Response(404)
            return httpx.Response(200, text=robots_body, headers={"content-type": "text/plain"})
        if status_for and path in status_for:
            return httpx.Response(status_for[path], text="<html>blocked</html>",
                                  headers={"content-type": "text/html"})
        body = _PAGES.get(path, "<html><body>unknown</body></html>")
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    return httpx.MockTransport(handler)


def _policy(**kw):
    base = dict(per_host_rps=1000.0, rate_limit_sleep=False, respect_robots=False)
    base.update(kw)
    return CrawlPolicy(**base)


def test_crawl_bfs_same_domain():
    crawler = Crawler(["https://example.com/"], policy=_policy(max_depth=2),
                      transport=_site_transport())
    res = crawler.crawl()
    assert res.ok is True
    visited = set(res.visited)
    assert "https://example.com/" in visited
    assert "https://example.com/a" in visited
    # off-domain link never crawled
    assert all("other.test" not in u for u in res.visited)
    assert res.stats["off_domain"] >= 1


def test_crawl_page_cap():
    crawler = Crawler(["https://example.com/"], policy=_policy(max_pages=2, max_depth=5),
                      transport=_site_transport())
    res = crawler.crawl()
    assert res.stats["pages"] <= 2


def test_crawl_depth_cap():
    crawler = Crawler(["https://example.com/"], policy=_policy(max_depth=0),
                      transport=_site_transport())
    res = crawler.crawl()
    # only the seed is fetched at depth 0
    assert res.visited == ["https://example.com/"]


def test_crawl_robots_denied():
    robots = "User-agent: *\nDisallow: /a\n"
    crawler = Crawler(["https://example.com/"],
                      policy=_policy(respect_robots=True, max_depth=2),
                      transport=_site_transport(robots_body=robots))
    res = crawler.crawl()
    assert "https://example.com/a" not in res.visited
    assert res.stats["robots_denied"] >= 1


def test_crawl_ssrf_blocked_link(monkeypatch):
    # A link host that resolves to an internal IP is blocked at fetch time.
    def _resolve(host, *a, **k):
        if host == "internal.test":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 443))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_PUBLIC_IP, 443))]

    monkeypatch.setattr(sc.socket, "getaddrinfo", _resolve)

    pages = {"/": "<html><body><a href='https://internal.test/secret'>x</a></body></html>"}

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=pages.get(req.url.path, "<html></html>"),
                              headers={"content-type": "text/html"})

    crawler = Crawler(
        ["https://example.com/"],
        policy=_policy(max_depth=2, allowed_domains=frozenset({"example.com", "internal.test"})),
        transport=httpx.MockTransport(handler),
    )
    res = crawler.crawl()
    # internal link was attempted and SSRF-blocked (recorded, crawl not aborted)
    assert res.ok is True
    assert res.stats["blocked"] >= 1
    assert any(s["reason"] == "ssrf_blocked" for s in res.skipped)


def test_crawl_rate_limited_skip():
    crawler = Crawler(
        ["https://example.com/"],
        policy=_policy(per_host_rps=1.0, rate_limit_sleep=False, max_depth=2),
        transport=_site_transport(),
    )
    res = crawler.crawl()
    # With a 1-token bucket and no sleeping, later same-host pages hit rate_limit
    assert res.stats["rate_limited"] >= 1
