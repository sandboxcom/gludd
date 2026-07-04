"""Tests for G12 live web retrieval MCP tool."""

from __future__ import annotations

import pytest

from general_ludd.retrieval.web import WebPageResult, WebRetriever


class TestWebRetriever:
    def test_retriever_is_constructable(self) -> None:
        """WebRetriever instantiates without error."""
        retriever = WebRetriever()
        assert retriever is not None

    def test_fetch_web_page_is_not_implemented(self) -> None:
        """fetch_web_page raises NotImplementedError (stub)."""
        retriever = WebRetriever()
        with pytest.raises(NotImplementedError):
            retriever.fetch_web_page("https://example.com")

    def test_web_page_result_dataclass(self) -> None:
        """WebPageResult dataclass holds expected fields."""
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
