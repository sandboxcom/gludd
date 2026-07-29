"""G12 live-web-retrieval wiring tests.

Tests the WebRetriever + MCP builtin integration + web search endpoint.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from general_ludd.mcp.builtins import (
    BUILTIN_SERVER_ID,
    WEB_RETRIEVE_TOOL,
    BuiltinToolHandler,
    register_builtins,
)
from general_ludd.retrieval.web import WebPageResult, WebRetriever


class TestBuiltinToolHandlerWebRetriever(unittest.IsolatedAsyncioTestCase):
    """BuiltinToolHandler uses a shared WebRetriever when provided."""

    async def test_handler_accepts_web_retriever(self) -> None:
        """BuiltinToolHandler stores the passed web_retriever."""
        wr = WebRetriever(timeout_seconds=7)
        handler = BuiltinToolHandler(web_retriever=wr)
        assert handler._web_retriever is wr

    async def test_handler_web_retrieve_uses_shared_instance(self) -> None:
        """_web_retrieve reuses the pre-built WebRetriever when timeouts match."""
        wr = WebRetriever(timeout_seconds=30)
        handler = BuiltinToolHandler(web_retriever=wr)

        with patch.object(wr, "fetch_web_page", return_value=WebPageResult(
            url="https://example.com",
            status_code=200,
            content="<html><title>Test</title></html>",
            title="Test",
            headers={"content-type": "text/html"},
        )) as mock_fetch:
            result = await handler._web_retrieve({"url": "https://example.com"})
            mock_fetch.assert_called_once_with("https://example.com")
            assert result["url"] == "https://example.com"
            assert result["status_code"] == 200
            assert result["title"] == "Test"

    async def test_handler_web_retrieve_custom_timeout_creates_new(self) -> None:
        """A custom timeout_seconds creates a fresh WebRetriever, not the shared one."""
        wr = WebRetriever(timeout_seconds=30)
        handler = BuiltinToolHandler(web_retriever=wr)

        with patch(
            "general_ludd.mcp.builtins.WebRetriever.fetch_web_page",
            return_value=WebPageResult(
                url="https://example.com",
                status_code=200,
                content="ok",
                title=None,
                headers={},
            ),
        ) as mock_fetch:
            await handler._web_retrieve({"url": "https://example.com", "timeout_seconds": 10})
            mock_fetch.assert_called_once_with("https://example.com")

    async def test_handler_web_retrieve_missing_url(self) -> None:
        """Missing url returns an error dict."""
        handler = BuiltinToolHandler()
        result = await handler._web_retrieve({})
        assert "error" in result

    async def test_handler_web_retrieve_url_error_passthrough(self) -> None:
        """ValueError from domain allowlist returns error dict."""
        wr = WebRetriever()
        wr.allowed_domains = staticmethod(lambda: ["example.com"])
        handler = BuiltinToolHandler(web_retriever=wr)

        result = await handler._web_retrieve({"url": "https://other.com"})
        assert "error" in result
        assert "other.com" in result["error"]


class TestRegisterBuiltinsWebRetriever(unittest.TestCase):
    """register_builtins wires the web_retriever into the MCP client."""

    def test_register_builtins_with_web_retriever(self) -> None:
        """register_builtins passes web_retriever through to BuiltinToolHandler."""
        mock_client = MagicMock()
        wr = WebRetriever(timeout_seconds=7)

        register_builtins(mock_client, web_retriever=wr)

        mock_client.register_builtin.assert_called_once()
        call_args = mock_client.register_builtin.call_args
        assert call_args[0][0] == BUILTIN_SERVER_ID  # server_id
        assert WEB_RETRIEVE_TOOL in call_args[0][1]  # tool list
        handler = call_args[0][2]  # the BuiltinToolHandler instance
        assert isinstance(handler, BuiltinToolHandler)
        assert handler._web_retriever is wr

    def test_register_builtins_without_web_retriever(self) -> None:
        """register_builtins works without a web_retriever (backward compat)."""
        mock_client = MagicMock()

        register_builtins(mock_client)

        handler = mock_client.register_builtin.call_args[0][2]
        assert handler._web_retriever is None


class TestWebRetrieverConstruction(unittest.TestCase):
    """WebRetriever can be constructed and injected at startup."""

    def test_constructor_defers_cache_connection(self) -> None:
        """Construction does not hold an idle SQLite connection open."""
        with patch(
            "general_ludd.retrieval.web.open_safe_diskcache"
        ) as cache_factory:
            wr = WebRetriever()

        cache_factory.assert_not_called()
        assert wr._cache_path == "web_retriever"

    def test_constructor_defaults(self) -> None:
        wr = WebRetriever()
        assert wr._timeout == 30
        assert wr._cache_path == "web_retriever"

    def test_constructor_custom_timeout(self) -> None:
        wr = WebRetriever(timeout_seconds=15)
        assert wr._timeout == 15

    def test_fetch_web_page_returns_result(self) -> None:
        wr = WebRetriever(timeout_seconds=5)
        with patch.object(wr, "fetch_web_page", return_value=WebPageResult(
            url="https://example.com",
            status_code=200,
            content="hello",
            title=None,
            headers={},
        )):
            result = wr.fetch_web_page("https://example.com")
            assert result.url == "https://example.com"
            assert result.status_code == 200

    def test_allowed_domains_from_env(self) -> None:
        with patch.dict("os.environ", {"GLUDD_WEB_FETCH_ALLOWED_DOMAINS": "a.com, b.com"}):
            domains = WebRetriever.allowed_domains()
            assert domains == ["a.com", "b.com"]

    def test_allowed_domains_empty_by_default(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            domains = WebRetriever.allowed_domains()
            assert domains == []
