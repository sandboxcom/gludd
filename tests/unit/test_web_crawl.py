"""Offline tests for PoliteCrawler — robots, caps, confinement, dedup, SSRF."""

from __future__ import annotations

import httpx

from general_ludd.web.crawl import PoliteCrawler, normalize_url, registrable_domain
from general_ludd.web.policy import WebPolicy
from general_ludd.web.resilience import WebResilience
from general_ludd.web.safe_fetch import SafeFetcher
from general_ludd.web.types import WebError


def _public_resolver(host: str, port: int) -> list[str]:
    return ["93.184.216.34"]


def _internal_resolver(host: str, port: int) -> list[str]:
    return ["10.0.0.5"]


def _crawler(handler, resolver=_public_resolver, **pol) -> PoliteCrawler:
    policy = WebPolicy(max_attempts=1, per_host_rps=1000.0, obey_robots=True,
                       robots_on_error="fail_open", **pol)
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    fetcher = SafeFetcher(client=client, resolver=resolver, policy=policy)
    res = WebResilience(policy)
    res._sleep = lambda _s: None
    return PoliteCrawler(policy=policy, fetcher=fetcher, resilience=res)


# -- helpers ----------------------------------------------------------------
def test_registrable_domain() -> None:
    assert registrable_domain("www.example.com") == "example.com"
    assert registrable_domain("a.b.example.co.uk") == "example.co.uk"
    assert registrable_domain("foo.github.io") == "foo.github.io"
    assert registrable_domain("example.com") == "example.com"


def test_normalize_url_dedup_key() -> None:
    a = normalize_url("https://Example.com:443/path#frag")
    b = normalize_url("https://example.com/path")
    assert a == b


# -- crawl ------------------------------------------------------------------
def _page(links: list[str]) -> str:
    anchors = "".join(f'<a href="{h}">x</a>' for h in links)
    return f"<html><body><p>page</p>{anchors}</body></html>"


def test_crawl_bfs_confined_and_capped() -> None:
    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.path == "/robots.txt":
            return httpx.Response(404)
        if r.url.host != "example.com":
            return httpx.Response(200, text="<p>offsite</p>")
        if r.url.path == "/":
            return httpx.Response(200, text=_page([
                "https://example.com/a", "https://example.com/b",
                "https://elsewhere.com/x",  # off-domain: must be excluded
            ]), headers={"content-type": "text/html"})
        return httpx.Response(200, text=_page([]), headers={"content-type": "text/html"})

    crawler = _crawler(handler, max_pages=10, max_depth=2)
    result = crawler.crawl("https://example.com/")
    assert result.ok is True
    crawled = {g.url for g in result.results or []}
    assert "https://example.com/" in crawled
    assert "https://example.com/a" in crawled
    assert "https://example.com/b" in crawled
    assert not any("elsewhere.com" in u for u in crawled)  # confinement


def test_crawl_respects_max_pages() -> None:
    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=_page([
            f"https://example.com/{i}" for i in range(10)
        ]), headers={"content-type": "text/html"})

    crawler = _crawler(handler, max_pages=3, max_depth=5)
    result = crawler.crawl("https://example.com/")
    fetched = result.meta["pages_fetched"]
    assert fetched <= 3


def test_crawl_robots_disallow_skips() -> None:
    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private",
                                  headers={"content-type": "text/plain"})
        if r.url.path == "/":
            return httpx.Response(200, text=_page(["https://example.com/private/x"]),
                                  headers={"content-type": "text/html"})
        return httpx.Response(200, text="<p>secret</p>", headers={"content-type": "text/html"})

    crawler = _crawler(handler, max_pages=10, max_depth=2)
    result = crawler.crawl("https://example.com/")
    skipped = [g for g in result.results or [] if g.error == WebError.ROBOTS_DISALLOWED]
    assert any("/private/" in g.url for g in skipped)
    assert result.meta["skipped_robots"] >= 1


def test_crawl_ssrf_blocks_malicious_link_mid_crawl() -> None:
    # An in-scope subdomain link whose host resolves to an internal IP must be
    # SSRF-blocked mid-crawl (confinement allows the subdomain; the per-hop SSRF
    # guard still rejects it). allow_subdomains keeps it in scope.
    def resolver(host: str, port: int) -> list[str]:
        return ["10.0.0.9"] if host == "internal.example.com" else ["93.184.216.34"]

    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.path == "/robots.txt":
            return httpx.Response(404)
        if r.url.host == "example.com" and r.url.path == "/":
            return httpx.Response(200, text=_page(["https://internal.example.com/secret"]),
                                  headers={"content-type": "text/html"})
        return httpx.Response(200, text="<p>x</p>", headers={"content-type": "text/html"})

    crawler = _crawler(handler, resolver=resolver, max_pages=10, max_depth=2,
                       allow_subdomains=True)
    result = crawler.crawl("https://example.com/")
    blocked = [g for g in result.results or [] if g.error == WebError.SSRF_BLOCKED]
    assert any("internal.example.com" in g.url for g in blocked)
