"""Unit tests for the self-contained SearXConnector.

Mocks httpx.Client so no real network is touched.
"""

from __future__ import annotations

import json
from typing import Any
from unittest import mock

import httpx
import pytest

from general_ludd.connectors._errors import ConnectorConfigError
from general_ludd.connectors.searx import SearXConnector, SearXResult


def _fake_response(
    status_code: int = 200,
    json_body: Any = None,
    content: bytes | None = None,
) -> mock.MagicMock:
    resp = mock.MagicMock()
    resp.status_code = status_code
    if json_body is not None:
        resp.content = json.dumps(json_body).encode("utf-8")
    elif content is not None:
        resp.content = content
    else:
        resp.content = b""
    return resp


def _mock_client(responses: list[mock.MagicMock]) -> Any:
    """Build a mock httpx.Client whose .get() returns the given responses in order."""
    mock_cls = mock.MagicMock()
    client_instance = mock.MagicMock()
    client_instance.get.side_effect = responses
    mock_cls.return_value.__enter__.return_value = client_instance
    return mock_cls


_SEARX_RESULTS = {
    "results": [
        {
            "title": "Example Page",
            "url": "https://example.com",
            "content": "This is a test result snippet.",
            "engine": "google",
            "score": 0.95,
        },
        {
            "title": "Another Page",
            "url": "https://another.example.com/page",
            "content": "Second result snippet here.",
            "engine": "duckduckgo",
            "score": 0.72,
        },
    ],
}

_EMPTY_RESULTS = {"results": []}

_MALFORMED_JSON = b"not valid json"


class TestSearXConnectorInit:
    def test_missing_base_url_raises(self) -> None:
        with pytest.raises(ConnectorConfigError, match="base_url is required"):
            SearXConnector({})

    def test_empty_base_url_raises(self) -> None:
        with pytest.raises(ConnectorConfigError, match="base_url is required"):
            SearXConnector({"base_url": ""})

    def test_localhost_allowed(self) -> None:
        conn = SearXConnector({"base_url": "http://localhost:8888"})
        assert conn.base_url == "http://localhost:8888"

    def test_loopback_127_allowed(self) -> None:
        conn = SearXConnector({"base_url": "http://127.0.0.1:8080"})
        assert conn.base_url == "http://127.0.0.1:8080"

    def test_blocked_metadata_host_raises(self) -> None:
        with pytest.raises(ConnectorConfigError, match="blocked"):
            SearXConnector({"base_url": "http://169.254.169.254"})

    def test_base_url_local_with_server(self) -> None:
        class FakeServer:
            def get_instance_url(self) -> str:
                return "http://127.0.0.1:8888"

        conn = SearXConnector({"base_url": "local"}, local_server=FakeServer())
        assert conn.base_url == "http://127.0.0.1:8888"

    def test_from_local_server(self) -> None:
        class FakeServer:
            def get_instance_url(self) -> str:
                return "http://localhost:9999"

        conn = SearXConnector.from_local_server(FakeServer())
        assert conn.base_url == "http://localhost:9999"

    def test_valid_public_host_succeeds(self) -> None:
        conn = SearXConnector({"base_url": "https://searx.example.com"})
        assert conn.base_url == "https://searx.example.com"
        assert conn.timeout == 10.0
        assert conn.verify_ssl is True

    def test_custom_timeout_and_ssl(self) -> None:
        conn = SearXConnector(
            {"base_url": "https://searx.example.com", "timeout": 5, "verify_ssl": False}
        )
        assert conn.timeout == 5.0
        assert conn.verify_ssl is False


class TestSearXSearch:
    def test_parses_valid_json_results(self) -> None:
        conn = SearXConnector({"base_url": "https://searx.example.com"})
        resp = _fake_response(200, _SEARX_RESULTS)
        with mock.patch("httpx.Client", _mock_client([resp])):
            results = conn.search("test query")
        assert len(results) == 2
        assert isinstance(results[0], SearXResult)
        assert results[0].title == "Example Page"
        assert results[0].url == "https://example.com"
        assert results[0].snippet == "This is a test result snippet."
        assert results[0].engine == "google"
        assert results[0].score == 0.95
        assert results[1].engine == "duckduckgo"
        assert results[1].score == 0.72

    def test_parses_result_without_score(self) -> None:
        conn = SearXConnector({"base_url": "https://searx.example.com"})
        body = {
            "results": [
                {
                    "title": "No Score",
                    "url": "https://noscore.example.com",
                    "content": "",
                    "engine": "wikipedia",
                }
            ]
        }
        resp = _fake_response(200, body)
        with mock.patch("httpx.Client", _mock_client([resp])):
            results = conn.search("test")
        assert len(results) == 1
        assert results[0].score == 0.0

    def test_empty_results_list(self) -> None:
        conn = SearXConnector({"base_url": "https://searx.example.com"})
        resp = _fake_response(200, _EMPTY_RESULTS)
        with mock.patch("httpx.Client", _mock_client([resp])):
            results = conn.search("nothing")
        assert results == []

    def test_non_dict_response_returns_empty(self) -> None:
        conn = SearXConnector({"base_url": "https://searx.example.com"})
        resp = _fake_response(200, ["not", "a", "dict"])
        with mock.patch("httpx.Client", _mock_client([resp])):
            results = conn.search("test")
        assert results == []

    def test_4xx_status_returns_empty(self) -> None:
        conn = SearXConnector({"base_url": "https://searx.example.com"})
        resp = _fake_response(404, {"error": "not found"})
        with mock.patch("httpx.Client", _mock_client([resp])):
            results = conn.search("test")
        assert results == []

    def test_5xx_status_returns_empty(self) -> None:
        conn = SearXConnector({"base_url": "https://searx.example.com"})
        resp = _fake_response(500, {"error": "server error"})
        with mock.patch("httpx.Client", _mock_client([resp])):
            results = conn.search("test")
        assert results == []

    def test_null_body_returns_empty(self) -> None:
        conn = SearXConnector({"base_url": "https://searx.example.com"})
        resp = _fake_response(200, content=b"")
        with mock.patch("httpx.Client", _mock_client([resp])):
            results = conn.search("test")
        assert results == []

    def test_includes_query_params(self) -> None:
        conn = SearXConnector({"base_url": "https://searx.example.com"})
        resp = _fake_response(200, _SEARX_RESULTS)
        mock_client_cls = _mock_client([resp])
        with mock.patch("httpx.Client", mock_client_cls):
            conn.search("my query", page=3, categories="news")
        call_args = mock_client_cls.return_value.__enter__.return_value.get.call_args
        params = call_args[1]["params"]
        assert params["q"] == "my query"
        assert params["pageno"] == 3
        assert params["categories"] == "news"
        assert params["format"] == "json"

    def test_malformed_json_returns_empty(self) -> None:
        conn = SearXConnector({"base_url": "https://searx.example.com"})
        resp = mock.MagicMock()
        resp.status_code = 200
        resp.content = _MALFORMED_JSON
        with mock.patch("httpx.Client", _mock_client([resp])):
            results = conn.search("test")
        assert results == []


class TestSearXHealth:
    def test_health_ok(self) -> None:
        conn = SearXConnector({"base_url": "https://searx.example.com"})
        resp = _fake_response(200, content=b"<html></html>")
        with mock.patch("httpx.Client", _mock_client([resp])):
            result = conn.health()
        assert result == {"ok": True}

    def test_health_failure_http_error(self) -> None:
        conn = SearXConnector({"base_url": "https://searx.example.com"})
        resp = _fake_response(502, content=b"Bad Gateway")
        with mock.patch("httpx.Client", _mock_client([resp])):
            result = conn.health()
        assert result["ok"] is False
        assert "502" in result["error"]

    def test_health_never_raises_on_exception(self) -> None:
        conn = SearXConnector({"base_url": "https://searx.example.com"})
        mock_client_cls = mock.MagicMock(side_effect=ConnectionError("refused"))
        with mock.patch("httpx.Client", mock_client_cls):
            result = conn.health()
        assert result["ok"] is False
        assert "error" in result


class TestSearXTimeout:
    def test_search_timeout_returns_empty(self) -> None:
        conn = SearXConnector({"base_url": "https://searx.example.com", "timeout": 1})
        mock_client_cls = mock.MagicMock(side_effect=httpx.TimeoutException("timed out"))
        with mock.patch("httpx.Client", mock_client_cls):
            results = conn.search("timeout test")
        assert results == []

    def test_health_timeout_reports_failure(self) -> None:
        conn = SearXConnector({"base_url": "https://searx.example.com", "timeout": 1})
        mock_client_cls = mock.MagicMock(side_effect=httpx.TimeoutException("timed out"))
        with mock.patch("httpx.Client", mock_client_cls):
            result = conn.health()
        assert result["ok"] is False
        assert result["error"] == "HTTP 0"
