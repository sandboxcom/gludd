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

        with patch("urllib.request.urlopen", return_value=mock_response):
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

        with patch("urllib.request.urlopen", return_value=mock_response):
            retriever = WebRetriever()
            result = retriever.fetch_web_page("https://notitle.example.com/untitled")

        assert result.title is None

    def test_domain_allowlist_blocks_disallowed(self) -> None:
        os.environ["GLUDD_WEB_FETCH_ALLOWED_DOMAINS"] = "example.com,trusted.org"
        try:
            retriever = WebRetriever()
            with pytest.raises(ValueError, match="not in the web fetch allowlist"):
                retriever.fetch_web_page("https://evil.com/page")
        finally:
            del os.environ["GLUDD_WEB_FETCH_ALLOWED_DOMAINS"]

    def test_domain_allowlist_allows_whitelisted(self) -> None:
        html = "<html><body>trusted</body></html>"
        mock_response = io.BytesIO(html.encode("utf-8"))
        mock_response.status = 200
        mock_response.headers = {}

        os.environ["GLUDD_WEB_FETCH_ALLOWED_DOMAINS"] = "example.com,trusted.org"
        try:
            with patch("urllib.request.urlopen", return_value=mock_response):
                retriever = WebRetriever()
                result = retriever.fetch_web_page("https://trusted.org/page")
            assert result.status_code == 200
        finally:
            del os.environ["GLUDD_WEB_FETCH_ALLOWED_DOMAINS"]

    def test_empty_allowlist_allows_all(self) -> None:
        html = "<html><body>anything</body></html>"
        mock_response = io.BytesIO(html.encode("utf-8"))
        mock_response.status = 200
        mock_response.headers = {}

        assert os.environ.get("GLUDD_WEB_FETCH_ALLOWED_DOMAINS") is None
        with patch("urllib.request.urlopen", return_value=mock_response):
            retriever = WebRetriever()
            result = retriever.fetch_web_page("https://any.example.com/page")
        assert result.status_code == 200

    def test_caching_second_fetch_returns_cached(self) -> None:
        html = "<html><body>first fetch</body></html>"
        mock_response = io.BytesIO(html.encode("utf-8"))
        mock_response.status = 200
        mock_response.headers = {}

        with patch("urllib.request.urlopen", return_value=mock_response):
            retriever = WebRetriever()
            result1 = retriever.fetch_web_page("https://example.com")
            result2 = retriever.fetch_web_page("https://example.com")

        assert result1.content == result2.content
