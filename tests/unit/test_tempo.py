"""Structural tests for connectors/tempo.py — TempoSource."""

from __future__ import annotations

from general_ludd.connectors.tempo import SSRFError, TempoSource, _TempoResponse


class TestTempoResponse:
    def test_constructs_with_status_and_body(self) -> None:
        resp = _TempoResponse(200, b'{"traces": []}')
        assert resp.status == 200
        assert resp.body == b'{"traces": []}'

    def test_json_parses_body(self) -> None:
        resp = _TempoResponse(200, b'{"traces": [{"traceID": "abc"}]}')
        data = resp.json()
        assert data["traces"][0]["traceID"] == "abc"


class TestTempoModule:
    def test_source_importable(self) -> None:
        assert TempoSource is not None

    def test_ssrf_error_importable(self) -> None:
        assert SSRFError is not None

    def test_kind_is_traces(self) -> None:
        assert TempoSource.KIND == "traces"
