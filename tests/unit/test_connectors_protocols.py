"""Structural tests for connectors/_protocols.py — HttpResponse Protocol."""

from __future__ import annotations

from general_ludd.connectors._protocols import HttpResponse


class TestHttpResponseProtocol:

    class MinimalResponse:
        status_code: int = 200
        text: str = "OK"

        def json(self):
            return {"key": "value"}

    class ErrorResponse:
        status_code: int = 404
        text: str = "Not Found"

        def json(self):
            return {"error": "missing"}

    def test_minimal_response_satisfies_protocol(self):
        resp = self.MinimalResponse()
        assert isinstance(resp, HttpResponse)

    def test_error_response_satisfies_protocol(self):
        resp = self.ErrorResponse()
        assert isinstance(resp, HttpResponse)

    def test_dict_does_not_satisfy(self):
        assert not isinstance({"status_code": 200, "text": "ok"}, HttpResponse)

    def test_int_does_not_satisfy(self):
        assert not isinstance(42, HttpResponse)

    def test_bare_object_does_not_satisfy(self):
        class NoAttr:
            pass

        assert not isinstance(NoAttr(), HttpResponse)

    def test_reads_status_code(self):
        resp = self.MinimalResponse()
        assert resp.status_code == 200

    def test_reads_text_property(self):
        resp = self.MinimalResponse()
        assert resp.text == "OK"

    def test_calls_json(self):
        resp = self.MinimalResponse()
        data = resp.json()
        assert data["key"] == "value"
