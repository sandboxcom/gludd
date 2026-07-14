"""Structural tests for general_ludd.connectors._protocols."""

from __future__ import annotations

from general_ludd.connectors._protocols import HttpResponse


def test_http_response_is_runtime_checkable() -> None:
    mock_resp = MockResponse(200, "{}")
    assert isinstance(mock_resp, HttpResponse)


class MockResponse:
    status_code: int

    def __init__(self, status: int, body: str) -> None:
        self.status_code = status
        self._body = body

    @property
    def text(self) -> str:
        return self._body

    def json(self) -> object:
        import json

        return json.loads(self._body)


class TestHttpResponseProtocol:
    def test_mock_with_all_attributes_passes_isinstance(self) -> None:
        resp = MockResponse(200, '{"ok":true}')
        assert isinstance(resp, HttpResponse)

    def test_response_missing_status_code_fails_isinstance(self) -> None:
        class BadResponse:
            @property
            def text(self) -> str:
                return ""

            def json(self) -> object:
                return {}

        assert not isinstance(BadResponse(), HttpResponse)

    def test_response_missing_text_property_fails_isinstance(self) -> None:
        class BadResponse:
            status_code: int = 200

            def json(self) -> object:
                return {}

        assert not isinstance(BadResponse(), HttpResponse)

    def test_response_missing_json_method_fails_isinstance(self) -> None:
        class BadResponse:
            status_code: int = 200

            @property
            def text(self) -> str:
                return ""

        assert not isinstance(BadResponse(), HttpResponse)

    def test_requests_response_is_compatible(self) -> None:
        try:
            import requests
        except ImportError:
            import pytest

            pytest.skip("requests not installed")

        resp = requests.Response()
        resp.status_code = 200
        resp._content = b"{}"
        assert isinstance(resp, HttpResponse)

    def test_httpx_response_is_compatible(self) -> None:
        try:
            import httpx
        except ImportError:
            import pytest

            pytest.skip("httpx not installed")

        resp = httpx.Response(200, content=b"{}")
        assert isinstance(resp, HttpResponse)


def test_module_exports_http_response() -> None:
    from general_ludd.connectors import _protocols

    assert hasattr(_protocols, "HttpResponse")
