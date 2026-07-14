"""Structural tests for connectors/grafana_loki.py — GrafanaLokiSource."""

from __future__ import annotations

import pytest

from general_ludd.connectors.grafana_loki import (
    GrafanaLokiSource,
    _validate_base_url,
)


class _FakeTransport:
    def __init__(self, status_code: int = 200, payload: object | None = None) -> None:
        self.status_code = status_code
        self.payload = payload or {}

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: object | None = None,
        params: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> tuple[int, object]:
        return self.status_code, self.payload


def _make_loki_payload():
    return {
        "data": {
            "result": [
                {
                    "stream": {"detected_level": "error", "job": "app"},
                    "values": [
                        ["1718000000000000000", "error: connection refused"],
                    ],
                }
            ]
        }
    }


class TestValidateBaseUrl:
    def test_valid_https(self):
        url = _validate_base_url("https://loki.example.com")
        assert url == "https://loki.example.com"

    def test_valid_http(self):
        url = _validate_base_url("http://loki.example.com")
        assert url == "http://loki.example.com"

    def test_strips_trailing_slash(self):
        url = _validate_base_url("https://loki.example.com/")
        assert url == "https://loki.example.com"

    def test_invalid_scheme(self):
        with pytest.raises(ValueError, match="unsupported URL scheme"):
            _validate_base_url("ftp://loki.example.com")

    def test_blocked_host_raises(self):
        with pytest.raises(ValueError, match="refusing internal"):
            _validate_base_url("https://localhost")


class TestGrafanaLokiSource:
    def test_constructs_with_transport(self):
        transport = _FakeTransport()
        src = GrafanaLokiSource(
            {"base_url": "https://loki.example.com"},
            transport=transport,
        )
        assert src.name == "grafana_loki"
        assert src.KIND == "logs"

    def test_auth_headers_without_token(self):
        transport = _FakeTransport()
        src = GrafanaLokiSource(
            {"base_url": "https://loki.example.com"},
            transport=transport,
        )
        headers = src._auth_headers()
        assert "Authorization" not in headers

    def test_auth_headers_with_token(self, monkeypatch):
        monkeypatch.setenv("LOKI_TOKEN", "test-token-123")
        transport = _FakeTransport()
        src = GrafanaLokiSource(
            {"base_url": "https://loki.example.com", "token_env": "LOKI_TOKEN"},
            transport=transport,
        )
        headers = src._auth_headers()
        assert headers["Authorization"] == "Bearer test-token-123"

    def test_detect_level_from_stream_labels(self):
        src = GrafanaLokiSource(
            {"base_url": "https://loki.example.com"},
            transport=_FakeTransport(),
        )
        level = src._detect_level({"detected_level": "warn"})
        assert level == "warn"

    def test_detect_level_empty_when_missing(self):
        src = GrafanaLokiSource(
            {"base_url": "https://loki.example.com"},
            transport=_FakeTransport(),
        )
        level = src._detect_level({"job": "app"})
        assert level == ""

    def test_ns_to_seconds(self):
        src = GrafanaLokiSource(
            {"base_url": "https://loki.example.com"},
            transport=_FakeTransport(),
        )
        assert src._ns_to_seconds("1718000000000000000") == 1718000000.0

    def test_ns_to_seconds_invalid(self):
        src = GrafanaLokiSource(
            {"base_url": "https://loki.example.com"},
            transport=_FakeTransport(),
        )
        assert src._ns_to_seconds("garbage") == 0.0

    def test_maybe_value_with_metric(self):
        src = GrafanaLokiSource(
            {"base_url": "https://loki.example.com"},
            transport=_FakeTransport(),
        )
        assert src._maybe_value(["1000", "log line", 3.14]) == 3.14

    def test_maybe_value_without_metric(self):
        src = GrafanaLokiSource(
            {"base_url": "https://loki.example.com"},
            transport=_FakeTransport(),
        )
        assert src._maybe_value(["1000", "log line"]) is None

    def test_normalize_entry(self):
        src = GrafanaLokiSource(
            {"base_url": "https://loki.example.com"},
            transport=_FakeTransport(),
        )
        rec = src._normalize_entry(
            ["1718000000000000000", "log message", 1.5],
            {"app": "test"},
            "info",
        )
        assert rec["kind"] == "logs"
        assert rec["source"] == "grafana_loki"
        assert rec["message"] == "log message"
        assert rec["value"] == 1.5
        assert rec["labels"] == {"app": "test"}

    def test_iter_records_from_payload(self):
        transport = _FakeTransport()
        src = GrafanaLokiSource(
            {"base_url": "https://loki.example.com"},
            transport=transport,
        )
        records = src._iter_records(_make_loki_payload())
        assert len(records) == 1
        assert records[0]["message"] == "error: connection refused"
        assert records[0]["level_or_status"] == "error"

    def test_iter_records_non_dict_payload(self):
        transport = _FakeTransport()
        src = GrafanaLokiSource(
            {"base_url": "https://loki.example.com"},
            transport=transport,
        )
        records = src._iter_records([])
        assert records == []

    def test_query_success(self):
        transport = _FakeTransport(payload=_make_loki_payload())
        src = GrafanaLokiSource(
            {"base_url": "https://loki.example.com"},
            transport=transport,
        )
        records = src.query({"query": '{job="app"}'})
        assert len(records) == 1
        assert records[0]["message"] == "error: connection refused"

    def test_query_error_status_returns_empty(self):
        transport = _FakeTransport(status_code=500)
        src = GrafanaLokiSource(
            {"base_url": "https://loki.example.com"},
            transport=transport,
        )
        records = src.query({"query": '{job="app"}'})
        assert records == []

    def test_health_ok(self):
        transport = _FakeTransport(status_code=200)
        src = GrafanaLokiSource(
            {"base_url": "https://loki.example.com"},
            transport=transport,
        )
        h = src.health()
        assert h["ok"] is True

    def test_health_unhealthy(self):
        transport = _FakeTransport(status_code=503)
        src = GrafanaLokiSource(
            {"base_url": "https://loki.example.com"},
            transport=transport,
        )
        h = src.health()
        assert h["ok"] is False

    def test_health_transport_error(self):
        class _ErrorTransport:
            def request(self, *args, **kwargs):
                raise RuntimeError("network down")
        src = GrafanaLokiSource(
            {"base_url": "https://loki.example.com"},
            transport=_ErrorTransport(),
        )
        h = src.health()
        assert h["ok"] is False
        assert "error" in h
