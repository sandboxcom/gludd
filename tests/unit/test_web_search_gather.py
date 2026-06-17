"""Offline tests for search_gather + the pluggable SearchProvider."""

from __future__ import annotations

import socket

import httpx
import pytest

from general_ludd.web import ssrf_client as sc
from general_ludd.web.results import SearchHit, WebError
from general_ludd.web.search import NullProvider, search_gather

_PUBLIC_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    def _fake(host, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_PUBLIC_IP, 443))]

    monkeypatch.setattr(sc.socket, "getaddrinfo", _fake)


class _FakeProvider:
    def __init__(self, hits):
        self._hits = hits

    def search(self, query, *, top_n):
        return self._hits[:top_n]


def _transport():
    def handler(req):
        return httpx.Response(200, text=f"<title>{req.url.path}</title>",
                              headers={"content-type": "text/html"})

    return httpx.MockTransport(handler)


def test_no_provider_default():
    res = search_gather("anything")
    assert res.ok is False
    assert res.error == WebError.NO_PROVIDER


def test_null_provider_explicit():
    res = search_gather("q", provider=NullProvider())
    assert res.ok is False
    assert res.error == WebError.NO_PROVIDER


def test_gathers_pages():
    hits = [SearchHit(url="https://a.example.com/1"), SearchHit(url="https://b.example.com/2")]
    res = search_gather("q", provider=_FakeProvider(hits), transport=_transport())
    assert res.ok is True
    assert res.gathered == 2
    assert len(res.pages) == 2
    assert all(p.ok for p in res.pages)


def test_partial_success_records_failure():
    hits = [
        SearchHit(url="https://good.example.com/1"),
        SearchHit(url="https://127.0.0.1/blocked"),  # SSRF blocked, recorded not raised
    ]
    res = search_gather("q", provider=_FakeProvider(hits), transport=_transport())
    assert res.ok is True  # >=1 page gathered
    assert res.gathered == 1
    assert res.failed == 1
    assert res.errors


def test_hits_only_no_fetch():
    hits = [SearchHit(url="https://a.example.com/1", title="A")]
    res = search_gather("q", provider=_FakeProvider(hits), fetch_results=False)
    assert res.ok is True
    assert res.gathered == 0
    assert res.hits[0].title == "A"


def test_provider_exception_handled():
    class Boom:
        def search(self, query, *, top_n):
            raise RuntimeError("provider down")

    res = search_gather("q", provider=Boom())
    assert res.ok is False
    assert res.error == WebError.NO_PROVIDER
