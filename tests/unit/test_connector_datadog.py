"""Structural tests for Datadog connector."""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.datadog import DatadogSource, _validate_site


class RecordingTransport:
    def __init__(self, responses: list[tuple[int, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self, method: str, url: str, *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> tuple[int, Any]:
        self.calls.append({
            "method": method,
            "url": url,
            "params": params or {},
            "json": json,
            "headers": headers or {},
            "timeout": timeout,
        })
        if not self._responses:
            raise AssertionError("too many calls")
        return self._responses.pop(0)


class BoomTransport:
    def __call__(self, *args: Any, **kwargs: Any) -> tuple[int, Any]:
        raise RuntimeError("down")


LOGS_PAYLOAD = {
    "data": [
        {
            "id": "evt-1",
            "type": "log",
            "attributes": {
                "timestamp": 1718000000000,
                "status": "error",
                "message": "boom",
                "service": "checkout",
                "host": "ip-10-0-0-1",
                "tags": ["env:prod"],
            },
        }
    ]
}

METRICS_PAYLOAD = {
    "series": [
        {
            "metric": "system.cpu.user",
            "scope": "host:i-123",
            "tag_set": ["env:prod"],
            "pointlist": [[1718000000, 45.5]],
        }
    ]
}


class TestValidateSite:
    def test_public_ok(self) -> None:
        assert _validate_site("https://api.datadoghq.com") == "https://api.datadoghq.com"

    def test_trailing_slash_stripped(self) -> None:
        assert _validate_site("https://api.datadoghq.com/") == "https://api.datadoghq.com"

    def test_loopback_rejected(self) -> None:
        with pytest.raises(ValueError):
            _validate_site("http://localhost/")

    def test_metadata_rejected(self) -> None:
        with pytest.raises(ValueError):
            _validate_site("http://169.254.169.254/")


class TestContract:
    def test_kind(self) -> None:
        assert DatadogSource.KIND == "logs"

    def test_name_default(self) -> None:
        src = DatadogSource({"site": "https://api.datadoghq.com"})
        assert "datadog:" in src.name


class TestQueryLogs:
    def test_search_returns_records(self) -> None:
        t = RecordingTransport([(200, LOGS_PAYLOAD)])
        src = DatadogSource({"site": "https://api.datadoghq.com"}, http_request=t)
        records = src.query({"mode": "logs"})
        assert len(records) == 1
        r = records[0]
        assert r["source"] == src.name
        assert r["kind"] == "logs"
        assert r["level_or_status"] == "error"
        assert r["message"] == "boom"
        assert r["labels"]["service"] == "checkout"
        assert r["labels"]["host"] == "ip-10-0-0-1"

    def test_empty_data(self) -> None:
        t = RecordingTransport([(200, {"data": []})])
        src = DatadogSource({"site": "https://api.datadoghq.com"}, http_request=t)
        assert src.query({"mode": "logs"}) == []

    def test_http_error_returns_error_record(self) -> None:
        t = RecordingTransport([(500, {"error": "server error"})])
        src = DatadogSource({"site": "https://api.datadoghq.com"}, http_request=t)
        records = src.query({"mode": "logs"})
        assert len(records) == 1
        assert records[0]["level_or_status"] == "error"

    def test_non_dict_payload(self) -> None:
        t = RecordingTransport([(200, "not a dict")])
        src = DatadogSource({"site": "https://api.datadoghq.com"}, http_request=t)
        records = src.query({"mode": "logs"})
        assert len(records) == 1
        assert records[0]["level_or_status"] == "error"

    def test_transport_error(self) -> None:
        src = DatadogSource({"site": "https://api.datadoghq.com"}, http_request=BoomTransport())
        records = src.query({"mode": "logs"})
        assert len(records) == 1
        assert records[0]["level_or_status"] == "error"


class TestQueryMetrics:
    def test_metrics_returns_points(self) -> None:
        t = RecordingTransport([(200, METRICS_PAYLOAD)])
        src = DatadogSource({"site": "https://api.datadoghq.com"}, http_request=t)
        records = src.query({"mode": "metrics"})
        assert len(records) == 1
        r = records[0]
        assert r["kind"] == "metrics"
        assert r["message"] == "system.cpu.user"
        assert r["value"] == 45.5

    def test_no_series(self) -> None:
        t = RecordingTransport([(200, {"series": []})])
        src = DatadogSource({"site": "https://api.datadoghq.com"}, http_request=t)
        assert src.query({"mode": "metrics"}) == []

    def test_http_error_returns_error_record(self) -> None:
        t = RecordingTransport([(503, {})])
        src = DatadogSource({"site": "https://api.datadoghq.com"}, http_request=t)
        records = src.query({"mode": "metrics"})
        assert len(records) == 1
        assert records[0]["level_or_status"] == "error"

    def test_transport_error(self) -> None:
        src = DatadogSource({"site": "https://api.datadoghq.com"}, http_request=BoomTransport())
        records = src.query({"mode": "metrics"})
        assert len(records) == 1
        assert records[0]["level_or_status"] == "error"


class TestUnknownMode:
    def test_unsupported_mode_returns_error(self) -> None:
        src = DatadogSource({"site": "https://api.datadoghq.com"})
        records = src.query({"mode": "traces"})
        assert len(records) == 1
        assert records[0]["level_or_status"] == "error"


class TestHealth:
    def test_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DD_API_KEY", "test-key")
        t = RecordingTransport([(200, {"valid": True})])
        src = DatadogSource(
            {"site": "https://api.datadoghq.com", "api_key_env": "DD_API_KEY"},
            http_request=t,
        )
        r = src.health()
        assert r["ok"] is True

    def test_invalid_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DD_API_KEY", "bad-key")
        t = RecordingTransport([(200, {"valid": False})])
        src = DatadogSource(
            {"site": "https://api.datadoghq.com", "api_key_env": "DD_API_KEY"},
            http_request=t,
        )
        r = src.health()
        assert r["ok"] is False

    def test_transport_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DD_API_KEY", "key")
        src = DatadogSource(
            {"site": "https://api.datadoghq.com", "api_key_env": "DD_API_KEY"},
            http_request=BoomTransport(),
        )
        r = src.health()
        assert r["ok"] is False

    def test_http_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DD_API_KEY", "key")
        t = RecordingTransport([(403, {})])
        src = DatadogSource(
            {"site": "https://api.datadoghq.com", "api_key_env": "DD_API_KEY"},
            http_request=t,
        )
        r = src.health()
        assert r["ok"] is False


class TestAuthHeaders:
    def test_api_key_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DD_KEY", "abc123")
        src = DatadogSource({"site": "https://api.datadoghq.com", "api_key_env": "DD_KEY"})
        headers = src._headers()
        assert headers["DD-API-KEY"] == "abc123"

    def test_app_key_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DD_KEY", "api"), monkeypatch.setenv("DD_APP", "app123")
        src = DatadogSource(
            {"site": "https://api.datadoghq.com", "api_key_env": "DD_KEY", "app_key_env": "DD_APP"}
        )
        headers = src._headers()
        assert headers["DD-API-KEY"] == "api"
        assert headers["DD-APPLICATION-KEY"] == "app123"

    def test_missing_env_var_omitted(self) -> None:
        src = DatadogSource({"site": "https://api.datadoghq.com"})
        headers = src._headers()
        assert "DD-API-KEY" not in headers
