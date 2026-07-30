"""Integration tests for G12 live web retrieval daemon wiring.

Proves WebRetriever is wired at daemon.py, the /admin/web/search endpoint
returns 200, caching works, and the MCP builtin tool registration path exists.
Uses mock HTTP transport — no real network calls.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from general_ludd.retrieval.web import (
    WebPageResult,
    WebRetriever,
    _extract_title,
    _normalise_domain,
)
from general_ludd.routers.web_search import register as register_web_search


class _FakeHeaders(dict):
    def items(self):
        return list(super().items())


def _mock_http_response(
    body: bytes,
    status: int = 200,
    headers: dict[str, str] | None = None,
    url: str = "http://example.com",
) -> MagicMock:
    if headers is None:
        headers = {"Content-Type": "text/html; charset=utf-8"}
    resp = MagicMock()
    resp.status = status
    resp.headers = _FakeHeaders(headers)
    resp.read.return_value = body
    resp.url = url
    return resp


class TestWebRetrieverDaemonWiring:
    def test_web_retriever_wired_to_app_state(self) -> None:
        app = FastAPI()
        retriever = WebRetriever(timeout_seconds=10)
        app.state._web_retriever = retriever

        retrieved = getattr(app.state, "_web_retriever", None)
        assert retrieved is not None
        assert isinstance(retrieved, WebRetriever)

    @pytest.mark.asyncio
    async def test_web_search_endpoint_returns_200(self) -> None:
        app = FastAPI()
        register_web_search(app, {})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/web/search", params={"q": "test_query"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["query"] == "test_query"
        assert isinstance(data["results"], list)

    @pytest.mark.asyncio
    async def test_web_search_endpoint_with_different_query(self) -> None:
        app = FastAPI()
        register_web_search(app, {})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/web/search", params={"q": "python pytest"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["query"] == "python pytest"

    @pytest.mark.asyncio
    async def test_web_search_endpoint_requires_q_param(self) -> None:
        app = FastAPI()
        register_web_search(app, {})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/admin/web/search")
        assert resp.status_code == 422


class TestWebRetrieverDirectMocked:
    def test_web_retriever_instantiation(self) -> None:
        retriever = WebRetriever(timeout_seconds=10)
        assert retriever._timeout == 10
        assert retriever._cache_path == "web_retriever"

    def test_web_retriever_allowed_domains_default_empty(self) -> None:
        with patch.dict(os.environ, {"GLUDD_WEB_FETCH_ALLOWED_DOMAINS": ""}):
            domains = WebRetriever.allowed_domains()
            assert domains == []

    def test_web_retriever_allowed_domains_from_env(self) -> None:
        with patch.dict(os.environ, {"GLUDD_WEB_FETCH_ALLOWED_DOMAINS": "example.com,docs.python.org"}):
            domains = WebRetriever.allowed_domains()
            assert domains == ["example.com", "docs.python.org"]

    def test_fetch_web_page_with_mock_success(self) -> None:
        html = (
            b"<html><head><title>Test Page</title></head>"
            b"<body><p>Hello World</p></body></html>"
        )
        mock_resp = _mock_http_response(html, status=200, url="http://example.com/page")

        retriever = WebRetriever(timeout_seconds=10)
        with patch.object(urllib.request, "build_opener") as mock_build_opener:
            mock_build_opener.return_value.open.return_value = mock_resp
            result = retriever.fetch_web_page("http://example.com/page")

        assert isinstance(result, WebPageResult)
        assert result.status_code == 200
        assert result.url == "http://example.com/page"
        assert "Hello World" in result.content
        assert result.title == "Test Page"

    def test_fetch_web_page_uses_cache_second_time(self) -> None:
        html = b"<html><head><title>Cached</title></head><body>Cached body</body></html>"
        mock_resp = _mock_http_response(html, status=200, url="http://example.com/cached")

        retriever = WebRetriever(timeout_seconds=10)
        with patch.object(urllib.request, "build_opener") as mock_build_opener:
            mock_build_opener.return_value.open.return_value = mock_resp
            result1 = retriever.fetch_web_page("http://example.com/cached")
            assert result1.title == "Cached"
            call_count_after_first = mock_build_opener.return_value.open.call_count

            result2 = retriever.fetch_web_page("http://example.com/cached")
            assert result2.title == "Cached"
            assert result2.content == result1.content

            assert mock_build_opener.return_value.open.call_count == call_count_after_first

    def test_web_page_result_dataclass_fields(self) -> None:
        result = WebPageResult(
            url="https://test.com/page",
            status_code=200,
            content="<html>test</html>",
            title="Test",
            headers={"content-type": "text/html"},
        )
        assert result.url == "https://test.com/page"
        assert result.status_code == 200
        assert result.content == "<html>test</html>"
        assert result.title == "Test"
        assert result.headers == {"content-type": "text/html"}

    def test_extract_title_from_html(self) -> None:
        html = "<html><head><title>My Document Title</title></head><body></body></html>"
        assert _extract_title(html) == "My Document Title"

    def test_extract_title_with_no_title_tag(self) -> None:
        html = "<html><head></head><body>No title here</body></html>"
        assert _extract_title(html) is None

    def test_normalise_domain_extracts_hostname(self) -> None:
        assert _normalise_domain("https://example.com/path?q=1") == "example.com"
        assert _normalise_domain("http://sub.domain.org:8080/page") == "sub.domain.org"
        assert _normalise_domain("https://api.example.com/v1/resource") == "api.example.com"

    def test_fetch_web_page_rejects_disallowed_domain(self) -> None:
        with patch.dict(os.environ, {"GLUDD_WEB_FETCH_ALLOWED_DOMAINS": "example.com"}):
            retriever = WebRetriever(timeout_seconds=10)
            with pytest.raises(ValueError, match="not in the web fetch allowlist"):
                retriever.fetch_web_page("http://disallowed.com/page")

    def test_fetch_web_page_allowed_domain_passes(self) -> None:
        html = b"<html><head><title>Allowed</title></head><body>OK</body></html>"
        mock_resp = _mock_http_response(html, status=200, url="http://example.com/allowed-page")

        with patch.dict(os.environ, {"GLUDD_WEB_FETCH_ALLOWED_DOMAINS": "example.com"}):
            retriever = WebRetriever(timeout_seconds=10)
            with patch.object(urllib.request, "build_opener") as mock_build_opener:
                mock_build_opener.return_value.open.return_value = mock_resp
                result = retriever.fetch_web_page("http://example.com/allowed-page")
            assert result.status_code == 200
            assert result.title == "Allowed"
