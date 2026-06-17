"""Offline tests for search_gather — provider seam + aggregation."""

from __future__ import annotations

import httpx

from general_ludd.web.policy import WebPolicy
from general_ludd.web.resilience import WebResilience
from general_ludd.web.safe_fetch import SafeFetcher
from general_ludd.web.search import SearchHit
from general_ludd.web.tools import search_gather
from general_ludd.web.types import WebError


def _public_resolver(host: str, port: int) -> list[str]:
    return ["93.184.216.34"]


def _kit(handler):
    policy = WebPolicy(max_attempts=1, per_host_rps=1000.0)
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    fetcher = SafeFetcher(client=client, resolver=_public_resolver, policy=policy)
    res = WebResilience(policy)
    res._sleep = lambda _s: None
    return fetcher, res


class _FakeProvider:
    configured = True

    def __init__(self, hits: list[SearchHit]) -> None:
        self._hits = hits

    def search(self, query: str, top_n: int) -> list[SearchHit]:
        return self._hits[:top_n]


def test_null_provider_is_provider_unconfigured() -> None:
    result = search_gather("anything", top_n=3)
    assert result.ok is False
    assert result.error == WebError.PROVIDER_UNCONFIGURED
    assert result.results == []  # distinct from a zero-hit success


def test_provider_aggregates_hits() -> None:
    def handler(r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=f"<title>{r.url.path}</title><p>body</p>",
                              headers={"content-type": "text/html"})
    fetcher, res = _kit(handler)
    provider = _FakeProvider([
        SearchHit(url="https://a.example.com/1", title="t1"),
        SearchHit(url="https://b.example.com/2", title="t2"),
    ])
    result = search_gather("q", top_n=2, provider=provider, fetcher=fetcher, resilience=res)
    assert result.ok is True
    assert result.results is not None
    assert len(result.results) == 2
    assert all(g.ok for g in result.results)


def test_per_hit_failure_not_fatal() -> None:
    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.host == "good.example.com":
            return httpx.Response(200, text="<p>ok</p>", headers={"content-type": "text/html"})
        raise httpx.ConnectError("down")
    fetcher, res = _kit(handler)
    provider = _FakeProvider([
        SearchHit(url="https://good.example.com/1"),
        SearchHit(url="https://bad.example.com/2"),
    ])
    result = search_gather("q", top_n=2, provider=provider, fetcher=fetcher, resilience=res)
    assert result.ok is True  # at least one hit succeeded
    assert result.results is not None
    statuses = {g.url: g.ok for g in result.results}
    assert statuses["https://good.example.com/1"] is True
    assert statuses["https://bad.example.com/2"] is False
