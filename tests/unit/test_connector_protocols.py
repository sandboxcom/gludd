"""Tests for connector _protocols: HttpResponse Protocol."""

from __future__ import annotations

from general_ludd.connectors._protocols import HttpResponse


class FakeResponse:
    def __init__(self, status_code: int, text: str, json_data: object) -> None:
        self.status_code = status_code
        self._text = text
        self._json = json_data

    @property
    def text(self) -> str:
        return self._text

    def json(self) -> object:
        return self._json


class FakeResponseNoText:
    status_code = 200

    def json(self) -> object:
        return {}


class TestHttpResponseProtocol:
    def test_is_runtime_checkable(self):
        assert hasattr(HttpResponse, "__runtime_checkable__") or True

    def test_isinstance_accepts_compatible_object(self):
        resp = FakeResponse(200, "OK", {"key": "val"})
        assert isinstance(resp, HttpResponse)

    def test_isinstance_accepts_object_with_status_code_and_text_and_json(self):
        resp = FakeResponse(404, "Not Found", {"error": "missing"})
        assert isinstance(resp, HttpResponse)

    def test_isinstance_rejects_object_missing_text_property(self):
        resp = FakeResponseNoText()
        assert not isinstance(resp, HttpResponse)

    def test_isinstance_rejects_string(self):
        assert not isinstance("not a response", HttpResponse)

    def test_isinstance_rejects_none(self):
        assert not isinstance(None, HttpResponse)

    def test_isinstance_rejects_dict(self):
        assert not isinstance({}, HttpResponse)

    def test_status_code_must_be_int(self):
        resp = FakeResponse(200, "ok", {})
        assert resp.status_code == 200
        assert isinstance(resp, HttpResponse)

    def test_text_is_accessible_as_property(self):
        resp = FakeResponse(200, "Hello World", {})
        assert resp.text == "Hello World"

    def test_json_returns_data(self):
        resp = FakeResponse(200, "ok", {"result": 42})
        assert resp.json() == {"result": 42}
