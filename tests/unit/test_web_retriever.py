"""Tests for G12 live web retrieval MCP tool."""

from __future__ import annotations

import io
import os
from unittest.mock import patch

import pytest

from general_ludd.retrieval.web import WebPageResult, WebRetriever


class TestWebPageResult:
    def test_dataclass_fields(self) -> None:
        result = WebPageResult(
            url="https://example.com",
            status_code=200,
            content="Hello, world!",
            title="Example Domain",
        )
        assert result.url == "https://example.com"
        assert result.status_code == 200
        assert result.content == "Hello, world!"
        assert result.title == "Example Domain"


class TestWebRetrieverFetch:
    def test_fetch_with_mock_urllib(self) -> None:
        html = (
            "<html><head><title>Example</title></head>"
            "<body>Hello</body></html>"
        )
        mock_response = io.BytesIO(html.encode("utf-8"))
        mock_response.status = 200
        mock_response.headers = {}

        with patch("urllib.request.build_opener") as mock_build_opener:
            mock_build_opener.return_value.open.return_value = mock_response
            retriever = WebRetriever()
            result = retriever.fetch_web_page("https://example.com")

        assert result.url == "https://example.com"
        assert result.status_code == 200
        assert result.content == html
        assert result.title == "Example"

    def test_fetch_without_title(self) -> None:
        html = "<html><body>No title here</body></html>"
        mock_response = io.BytesIO(html.encode("utf-8"))
        mock_response.status = 200
        mock_response.headers = {}

        with patch("urllib.request.build_opener") as mock_build_opener:
            mock_build_opener.return_value.open.return_value = mock_response
            retriever = WebRetriever()
            result = retriever.fetch_web_page("https://notitle.example.com/untitled")

        assert result.title is None

    def test_domain_allowlist_blocks_disallowed(self, monkeypatch) -> None:
        monkeypatch.setenv("GLUDD_WEB_FETCH_ALLOWED_DOMAINS", "example.com,trusted.org")
        retriever = WebRetriever()
        with pytest.raises(ValueError, match="not in the web fetch allowlist"):
            retriever.fetch_web_page("https://evil.com/page")

    def test_domain_allowlist_allows_whitelisted(self, monkeypatch) -> None:
        html = "<html><body>trusted</body></html>"
        mock_response = io.BytesIO(html.encode("utf-8"))
        mock_response.status = 200
        mock_response.headers = {}

        monkeypatch.setenv("GLUDD_WEB_FETCH_ALLOWED_DOMAINS", "example.com,trusted.org")
        with patch("urllib.request.build_opener") as mock_build_opener:
            mock_build_opener.return_value.open.return_value = mock_response
            retriever = WebRetriever()
            result = retriever.fetch_web_page("https://trusted.org/page")
        assert result.status_code == 200

    def test_empty_allowlist_allows_all(self) -> None:
        html = "<html><body>anything</body></html>"
        mock_response = io.BytesIO(html.encode("utf-8"))
        mock_response.status = 200
        mock_response.headers = {}

        assert os.environ.get("GLUDD_WEB_FETCH_ALLOWED_DOMAINS") is None
        with patch("urllib.request.build_opener") as mock_build_opener:
            mock_build_opener.return_value.open.return_value = mock_response
            retriever = WebRetriever()
            result = retriever.fetch_web_page("https://any.example.com/page")
        assert result.status_code == 200

    def test_caching_second_fetch_returns_cached(self) -> None:
        html = "<html><body>first fetch</body></html>"
        mock_response = io.BytesIO(html.encode("utf-8"))
        mock_response.status = 200
        mock_response.headers = {}

        with patch("urllib.request.build_opener") as mock_build_opener:
            mock_build_opener.return_value.open.return_value = mock_response
            retriever = WebRetriever()
            result1 = retriever.fetch_web_page("https://example.com/cache-test")
            result2 = retriever.fetch_web_page("https://example.com/cache-test")

        assert result1.content == result2.content

    def test_http_error_returns_result_with_error_code(self) -> None:
        import urllib.error
        body = io.BytesIO(b"")
        with patch("urllib.request.build_opener") as mock_build_opener:
            mock_build_opener.return_value.open.side_effect = urllib.error.HTTPError(
                "https://example.com", 404, "Not Found", {}, body
            )
            retriever = WebRetriever()
            result = retriever.fetch_web_page("https://example.com/missing")
        assert result.status_code == 404
        assert result.content == ""
        assert body.closed

    def test_network_error_returns_negative_status(self) -> None:
        with patch("urllib.request.build_opener") as mock_build_opener:
            mock_build_opener.return_value.open.side_effect = OSError("network unreachable")
            retriever = WebRetriever()
            result = retriever.fetch_web_page("https://down.example.com")
        assert result.status_code == -1
        assert "Fetch error" in result.content

    def test_title_extraction_with_attributes(self) -> None:
        html = '<html><head><title lang="en">English Title</title></head><body>content</body></html>'
        mock_response = io.BytesIO(html.encode("utf-8"))
        mock_response.status = 200
        mock_response.headers = {}

        with patch("urllib.request.build_opener") as mock_build_opener:
            mock_build_opener.return_value.open.return_value = mock_response
            retriever = WebRetriever()
            result = retriever.fetch_web_page("https://example.com/title-attr")
        assert result.title == "English Title"

    def test_content_truncation_at_limit(self) -> None:
        large_html = "<html><body>" + "x" * (2 * 1024 * 1024) + "</body></html>"
        mock_response = io.BytesIO(large_html.encode("utf-8"))
        mock_response.status = 200
        mock_response.headers = {}

        with patch("urllib.request.build_opener") as mock_build_opener:
            mock_build_opener.return_value.open.return_value = mock_response
            retriever = WebRetriever()
            result = retriever.fetch_web_page("https://example.com/large")
        assert len(result.content) <= 1 * 1024 * 1024 + 1

    def test_allowed_domains_empty_by_default(self) -> None:
        retriever = WebRetriever()
        assert retriever.allowed_domains() == []

    def test_allowed_domains_from_env(self, monkeypatch) -> None:
        monkeypatch.setenv("GLUDD_WEB_FETCH_ALLOWED_DOMAINS", "a.com,b.com,c.org")
        retriever = WebRetriever()
        assert retriever.allowed_domains() == ["a.com", "b.com", "c.org"]

    def test_fetch_with_response_headers(self) -> None:
        html = "<html><body>test</body></html>"
        mock_response = io.BytesIO(html.encode("utf-8"))
        mock_response.status = 200
        mock_response.headers = {"Content-Type": "text/html", "Server": "nginx"}

        with patch("urllib.request.build_opener") as mock_build_opener:
            mock_build_opener.return_value.open.return_value = mock_response
            retriever = WebRetriever()
            result = retriever.fetch_web_page("https://example.com/with-headers")
        assert result.headers is not None
        assert result.headers.get("content-type") == "text/html"

    def test_timeout_configurable(self) -> None:
        retriever = WebRetriever(timeout_seconds=60)
        assert retriever._timeout == 60

    def test_content_limit_env_override(self) -> None:
        from general_ludd.retrieval import web
        original = web._MAX_CONTENT_BYTES
        try:
            web._MAX_CONTENT_BYTES = 500
            mock_response = io.BytesIO(b"x" * 1000)
            mock_response.status = 200
            mock_response.headers = {}
            with patch("urllib.request.build_opener") as mock_build_opener:
                mock_build_opener.return_value.open.return_value = mock_response
                retriever = WebRetriever()
                result = retriever.fetch_web_page("https://example.com/small")
            assert len(result.content) <= 501
        finally:
            web._MAX_CONTENT_BYTES = original
