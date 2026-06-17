"""Offline tests for fetch_raw / fetch_parsed — structured WebResult always."""

from __future__ import annotations

import httpx

from general_ludd.web.policy import WebPolicy
from general_ludd.web.resilience import WebResilience
from general_ludd.web.safe_fetch import SafeFetcher
from general_ludd.web.tools import fetch_parsed, fetch_raw
from general_ludd.web.types import WebError


def _public_resolver(host: str, port: int) -> list[str]:
    return ["93.184.216.34"]


def _internal_resolver(host: str, port: int) -> list[str]:
    return ["10.0.0.5"]


def _kit(handler, resolver=_public_resolver, **pol):
    pol.setdefault("max_attempts", 2)
    policy = WebPolicy(**pol)
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    fetcher = SafeFetcher(client=client, resolver=resolver, policy=policy)
    res = WebResilience(policy)
    res._sleep = lambda _s: None
    return fetcher, res


def test_fetch_raw_success() -> None:
    def handler(r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body>hi</body></html>",
                              headers={"content-type": "text/html"})
    fetcher, res = _kit(handler)
    result = fetch_raw("https://example.com/", fetcher=fetcher, resilience=res)
    assert result.ok is True
    assert result.status == 200
    assert "hi" in (result.body or "")
    assert result.elapsed_ms >= 0


def test_fetch_raw_ssrf_dns_block_structured() -> None:
    def handler(r: httpx.Request) -> httpx.Response:
        return httpx.Response(200)
    fetcher, res = _kit(handler, resolver=_internal_resolver)
    result = fetch_raw("https://evil.example.com/", fetcher=fetcher, resilience=res)
    assert result.ok is False
    assert result.error == WebError.SSRF_BLOCKED
    # Never raised — returned a structured result.


def test_fetch_raw_offline_structured() -> None:
    def handler(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")
    fetcher, res = _kit(handler)
    result = fetch_raw("https://example.com/", fetcher=fetcher, resilience=res)
    assert result.ok is False
    assert result.error == WebError.OFFLINE


def test_fetch_raw_timeout_retried_then_structured() -> None:
    calls = {"n": 0}
    def handler(r: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ReadTimeout("slow")
    fetcher, res = _kit(handler, max_attempts=3)
    result = fetch_raw("https://example.com/", fetcher=fetcher, resilience=res)
    assert result.ok is False
    assert result.error == WebError.TIMEOUT
    assert calls["n"] >= 2  # retried


def test_fetch_raw_403_not_retried() -> None:
    calls = {"n": 0}
    def handler(r: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(403, text="forbidden")
    fetcher, res = _kit(handler, max_attempts=3)
    result = fetch_raw("https://example.com/", fetcher=fetcher, resilience=res)
    assert result.ok is False
    assert result.error == WebError.HTTP_4XX
    assert calls["n"] == 1  # a 403 body is a terminal response, not retried


def test_fetch_raw_5xx_retried_then_structured() -> None:
    # A 503 (no captcha marker) is a TRANSIENT server error: it must be retried
    # with backoff (the "retry transient 5xx/429" contract), then — if it never
    # recovers — surfaced as a structured HTTP_5XX. The fetcher returns a
    # _RawResponse for any status, so the tool layer raises RetryableStatusError
    # to re-enter the retry loop AND record a per-host breaker failure.
    calls = {"n": 0}
    def handler(r: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, text="down")
    fetcher, res = _kit(handler, max_attempts=3)
    result = fetch_raw("https://example.com/", fetcher=fetcher, resilience=res)
    assert result.ok is False
    assert result.error == WebError.HTTP_5XX
    assert result.status == 503
    assert calls["n"] > 1  # retried, not a one-shot terminal


def test_fetch_raw_5xx_then_200_recovers() -> None:
    # A transient 503 followed by a 200 must SUCCEED via retry (not surface the
    # 503), proving the retry actually re-enters and recovers.
    calls = {"n": 0}
    def handler(r: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(503, text="warming up")
        return httpx.Response(200, text="ok now", headers={"content-type": "text/plain"})
    fetcher, res = _kit(handler, max_attempts=3)
    result = fetch_raw("https://example.com/", fetcher=fetcher, resilience=res)
    assert result.ok is True
    assert result.status == 200
    assert calls["n"] == 2  # one retry then success


def test_fetch_raw_429_retried() -> None:
    # A plain 429 (no captcha/bot-block marker) is transient rate-limiting and is
    # retried; a marker-bearing 429 (see captcha test) is a persistent challenge.
    calls = {"n": 0}
    def handler(r: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, text="slow down", headers={"retry-after": "0"})
    fetcher, res = _kit(handler, max_attempts=3)
    result = fetch_raw("https://example.com/", fetcher=fetcher, resilience=res)
    assert result.ok is False
    assert calls["n"] > 1  # retried with backoff


def test_fetch_raw_captcha_detected() -> None:
    def handler(r: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="Just a moment... cf-mitigated",
                              headers={"server": "cloudflare"})
    fetcher, res = _kit(handler)
    result = fetch_raw("https://example.com/", fetcher=fetcher, resilience=res)
    assert result.ok is False
    assert result.error == WebError.CAPTCHA_DETECTED
    assert result.meta["blocked_by"]["vendor"] == "cloudflare"


def test_fetch_parsed_extracts() -> None:
    html = (
        '<html lang="en"><head><title>My Page</title>'
        '<meta name="description" content="d">'
        '<link rel="canonical" href="https://example.com/canon">'
        '<style>.x{}</style></head>'
        '<body><script>var x=1;</script><p>Visible text here</p>'
        '<a href="/next">Next</a></body></html>'
    )
    def handler(r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})
    fetcher, res = _kit(handler)
    result = fetch_parsed("https://example.com/", fetcher=fetcher, resilience=res)
    assert result.ok is True
    assert result.parsed is not None
    assert result.parsed.title == "My Page"
    assert "Visible text here" in result.parsed.text
    assert "var x=1" not in result.parsed.text  # script stripped
    assert result.parsed.lang == "en"
    assert result.parsed.meta["description"] == "d"
    assert result.parsed.meta["canonical"] == "https://example.com/canon"
    assert any(link.href == "https://example.com/next" for link in result.parsed.links)


def test_fetch_parsed_failed_fetch_passthrough() -> None:
    def handler(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")
    fetcher, res = _kit(handler)
    result = fetch_parsed("https://example.com/", fetcher=fetcher, resilience=res)
    assert result.ok is False
    assert result.parsed is None
