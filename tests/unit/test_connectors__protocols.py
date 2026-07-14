"""Structural tests for connectors/_protocols.py — HTTP response Protocol."""

from __future__ import annotations

from general_ludd.connectors._protocols import HttpResponse


class TestHttpResponseProtocol:
    def test_protocol_exists(self) -> None:
        assert HttpResponse is not None

    def test_protocol_has_status_code(self) -> None:
        assert hasattr(HttpResponse, "status_code")

    def test_protocol_has_text_property(self) -> None:
        protocol_annotations = HttpResponse.__annotations__
        assert "text" in protocol_annotations

    def test_protocol_has_json_method(self) -> None:
        assert hasattr(HttpResponse, "json")


class MockResponse:
    status_code: int = 200

    @property
    def text(self) -> str:
        return "ok"

    def json(self) -> dict:
        return {"key": "value"}


class TestHttpResponseStructuralConformance:
    def test_mock_satisfies_protocol(self) -> None:
        from typing import runtime_checkable

        assert runtime_checkable(HttpResponse)
        mock = MockResponse()
        assert isinstance(mock, HttpResponse)
