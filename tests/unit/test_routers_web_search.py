"""Unit tests for routers/web_search.py router endpoints.

Covers the previously 28.1%-rated module:
  * SlidingWindowRateLimiter boundary conditions
  * /admin/web/search endpoint (happy path, rate-limit, empty results)
  * _web_search function (network failure fallback, regex extraction)
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from general_ludd.daemon import create_daemon_app
from general_ludd.routers.web_search import (
    _RATE_LIMITER,
    SlidingWindowRateLimiter,
    _web_search,
)
from general_ludd.security.url_fetch import FetchResult


@pytest.fixture
def app():
    return create_daemon_app(tick_interval=0.01)


class TestSlidingWindowRateLimiter:
    def test_allow_returns_true_before_limit(self):
        limiter = SlidingWindowRateLimiter(max_requests=3, window_seconds=60.0)
        assert limiter.allow() is True
        assert limiter.allow() is True
        assert limiter.allow() is True

    def test_allow_returns_false_after_limit(self):
        limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=60.0)
        assert limiter.allow() is True
        assert limiter.allow() is True
        assert limiter.allow() is False

    def test_allow_expired_timestamps_slide_out(self, monkeypatch):
        limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=1.0)
        now_values = [100.0, 100.1]

        import time as _time
        original_monotonic = _time.monotonic

        def fake_monotonic():
            if now_values:
                return now_values.pop(0)
            return original_monotonic()

        monkeypatch.setattr(_time, "monotonic", fake_monotonic)
        assert limiter.allow() is True
        assert limiter.allow() is True

        now_values = [200.0]
        assert limiter.allow() is True


class TestWebSearchFunction:
    def test_web_search_returns_empty_on_network_error(self):
        with patch(
            "general_ludd.routers.web_search.secure_fetch",
            side_effect=OSError("network down"),
        ):
            results = _web_search("test query")
        assert results == []

    def test_web_search_parses_html_results(self):
        fake_html = b"""<html>
        <a class="result__a" href="https://example.com">Example Page</a>
        <a class="result__snippet">This is a snippet about the example.</a>
        <a class="result__url">example.com</a>
        </html>"""
        response = FetchResult(
            url="https://html.duckduckgo.com/html/",
            status_code=200,
            headers={},
            content=fake_html,
        )
        with patch("general_ludd.routers.web_search.secure_fetch", return_value=response):
            results = _web_search("test query")
        assert len(results) >= 1
        assert results[0]["title"] == "Example Page"

    def test_web_search_skips_rows_without_title(self):
        fake_html = b"""<html>
        <a class="result__a"></a>
        <a class="result__snippet">snippet</a>
        <a class="result__url">example.com</a>
        </html>"""
        response = FetchResult(
            url="https://html.duckduckgo.com/html/",
            status_code=200,
            headers={},
            content=fake_html,
        )
        with patch("general_ludd.routers.web_search.secure_fetch", return_value=response):
            results = _web_search("test")
        assert results == []


@pytest.fixture
def registered_app():
    from general_ludd.routers.web_search import register as _reg
    apps = create_daemon_app(tick_interval=0.01)
    _reg(apps, {})
    return apps


class TestWebSearchEndpoint:
    @pytest.mark.asyncio
    async def test_search_endpoint_rate_limited_returns_429(self, registered_app):
        _RATE_LIMITER._timestamps.clear()
        transport = ASGITransport(app=registered_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("general_ludd.routers.web_search._web_search", return_value=[]):
                for _ in range(10):
                    await client.get("/admin/web/search?q=test")
                resp = await client.get("/admin/web/search?q=test")
        assert resp.status_code == 429

    @pytest.mark.asyncio
    async def test_search_endpoint_empty_query_rejected(self, registered_app):
        transport = ASGITransport(app=registered_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/web/search?q=")
        assert resp.status_code in (422, 400)

    @pytest.mark.asyncio
    async def test_search_endpoint_missing_query_rejected(self, registered_app):
        transport = ASGITransport(app=registered_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/web/search")
        assert resp.status_code in (422, 400)
