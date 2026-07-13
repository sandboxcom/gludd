"""Structural tests for connectors/_protocols.py — HttpResponse Protocol."""

from __future__ import annotations

from dataclasses import dataclass

from general_ludd.connectors._protocols import HttpResponse


class TestHttpResponseProtocol:
    def test_protocol_structural_match(self):
        @dataclass
        class FakeResponse:
            status_code: int
            text: str

            def json(self):
                return {"key": "val"}

        resp = FakeResponse(status_code=200, text="ok")
        assert isinstance(resp, HttpResponse)

    def test_protocol_rejects_missing_status_code(self):
        class BadResponse:
            text: str = ""

            def json(self):
                return {}

        bad = BadResponse()
        assert not isinstance(bad, HttpResponse)

    def test_protocol_rejects_missing_text_property(self):
        class BadResponse:
            status_code: int = 200

            def json(self):
                return {}

        bad = BadResponse()
        assert not isinstance(bad, HttpResponse)

    def test_protocol_rejects_missing_json_method(self):
        @dataclass
        class BadResponse:
            status_code: int = 200
            text: str = ""

        bad = BadResponse(status_code=200, text="")
        assert not isinstance(bad, HttpResponse)
