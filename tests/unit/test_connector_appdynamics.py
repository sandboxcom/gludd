"""Unit tests for the AppDynamics APM connector (mocked transport, no network)."""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.appdynamics import (
    AppDynamicsSource,
    ConnectorConfigError,
)


class FakeTransport:
    """Records the last request and returns a scripted ``(status, body)``."""

    def __init__(self, status: int = 200, body: dict[str, Any] | None = None) -> None:
        self.status = status
        self.body = body if body is not None else {}
        self.calls: list[dict[str, Any]] = []

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> tuple[int, dict[str, Any]]:
        self.calls.append({"url": url, "headers": headers, "timeout": timeout})
        return self.status, self.body


class RaisingTransport:
    """Transport that always raises, to prove health() never propagates."""

    def get(self, url: str, *, headers: dict[str, str], timeout: float) -> tuple[int, dict[str, Any]]:
        raise TimeoutError("controller unreachable")


# Controller returns a JSON array of metric series; the injected transport may
# wrap it under "data" (mirroring how the default urllib transport wraps a bare
# top-level list).
METRIC_PAYLOAD: dict[str, Any] = {
    "data": [
        {
            "metricId": 12345,
            "metricName": "Average Response Time (ms)",
            "metricPath": "Overall Application Performance|Average Response Time (ms)",
            "frequency": "ONE_MIN",
            "metricValues": [
                {
                    "startTimeInMillis": 1718000000000,
                    "occurrences": 1,
                    "current": 250,
                    "min": 100,
                    "max": 400,
                    "value": 250,
                    "sum": 250,
                    "count": 1,
                },
                {
                    "startTimeInMillis": 1718000060000,
                    "occurrences": 1,
                    "current": 275,
                    "min": 110,
                    "max": 420,
                    "value": 275,
                    "sum": 275,
                    "count": 1,
                },
            ],
        }
    ]
}


def _config(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "appd-prod",
        "base_url": "https://mycorp.saas.appdynamics.com",
        "application": "ECommerce App",
        "token_env": "APPD_TEST_TOKEN",
    }
    base.update(over)
    return base


def test_kind_and_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPD_TEST_TOKEN", "tok")
    src = AppDynamicsSource(_config(), transport=FakeTransport())
    assert AppDynamicsSource.KIND == "metrics"
    assert src.KIND in {"metrics", "traces"}
    assert src.name == "appd-prod"


def test_secret_read_from_env_not_hardcoded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPD_TEST_TOKEN", "bearer-secret-789")
    t = FakeTransport(body=METRIC_PAYLOAD)
    src = AppDynamicsSource(_config(), transport=t)
    src.query({"metric_path": "Overall Application Performance|Average Response Time (ms)"})
    assert t.calls, "transport was never called"
    assert t.calls[0]["headers"]["Authorization"] == "Bearer bearer-secret-789"


def test_missing_env_var_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APPD_TEST_TOKEN", raising=False)
    with pytest.raises(ConnectorConfigError):
        AppDynamicsSource(_config(), transport=FakeTransport())


def test_missing_application_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPD_TEST_TOKEN", "tok")
    cfg = _config()
    del cfg["application"]
    with pytest.raises(ConnectorConfigError):
        AppDynamicsSource(cfg, transport=FakeTransport())


def test_normalization_of_metric_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPD_TEST_TOKEN", "tok")
    t = FakeTransport(body=METRIC_PAYLOAD)
    src = AppDynamicsSource(_config(), transport=t)
    records = src.query(
        {
            "metric_path": "Overall Application Performance|Average Response Time (ms)",
            "duration_in_mins": 60,
        }
    )

    assert len(records) == 2
    first = records[0]
    assert set(first) == {
        "ts",
        "source",
        "kind",
        "level_or_status",
        "message",
        "value",
        "labels",
        "raw",
    }
    assert first["kind"] == "metrics"
    assert first["source"] == "appd-prod"
    assert first["value"] == 250
    assert first["ts"] == 1718000000000 / 1000.0
    assert first["labels"]["metricName"] == "Average Response Time (ms)"
    assert first["labels"]["metricPath"].startswith("Overall Application Performance")
    assert first["raw"]["startTimeInMillis"] == 1718000000000

    call_url = t.calls[0]["url"]
    assert "/controller/rest/applications/" in call_url
    assert "/metric-data" in call_url
    assert "metric-path=" in call_url
    assert "output=JSON" in call_url
    # Application name is URL-encoded (space -> %20).
    assert "ECommerce%20App" in call_url


def test_health_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPD_TEST_TOKEN", "tok")
    src = AppDynamicsSource(_config(), transport=FakeTransport(status=200, body={}))
    h = src.health()
    assert h["ok"] is True
    assert "200" in h["detail"]


def test_health_not_ok_on_error_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPD_TEST_TOKEN", "tok")
    src = AppDynamicsSource(_config(), transport=FakeTransport(status=403, body={}))
    h = src.health()
    assert h["ok"] is False
    assert "403" in h["detail"]


def test_health_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPD_TEST_TOKEN", "tok")
    src = AppDynamicsSource(_config(), transport=RaisingTransport())
    h = src.health()
    assert h["ok"] is False
    assert "TimeoutError" in h["detail"]


@pytest.mark.parametrize(
    "bad_url",
    [
        "https://127.0.0.1",
        "https://localhost",
        "http://10.1.2.3",
        "https://172.16.5.4",
        "https://192.168.0.1",
        "https://169.254.169.254",
        "http://metadata",
        "https://[::1]",
        "https://[fd00::abcd]",
    ],
)
def test_internal_base_url_rejected(bad_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPD_TEST_TOKEN", "tok")
    with pytest.raises(ConnectorConfigError):
        AppDynamicsSource(_config(base_url=bad_url), transport=FakeTransport())


def test_public_base_url_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPD_TEST_TOKEN", "tok")
    src = AppDynamicsSource(_config(base_url="https://1.1.1.1/controller"), transport=FakeTransport())
    assert src.base_url == "https://1.1.1.1/controller"


# Canonical SSRF guard coverage — 100.100.100.200 is the Alibaba metadata IP
# the shared general_ludd.security.ssrf.is_url_blocked guarantees.
_CANONICAL_SSRF_URLS = [
    "http://localhost/",
    "http://metadata.google.internal/",
    "http://169.254.169.254/",
    "http://100.100.100.200/",
]


@pytest.mark.parametrize("bad_url", _CANONICAL_SSRF_URLS)
def test_canonical_ssrf_urls_rejected(
    bad_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APPD_TEST_TOKEN", "tok")
    with pytest.raises(ConnectorConfigError):
        AppDynamicsSource(_config(base_url=bad_url), transport=FakeTransport())


def test_public_base_url_constructs_after_consolidation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPD_TEST_TOKEN", "tok")
    src = AppDynamicsSource(
        _config(base_url="https://api.example.com"), transport=FakeTransport()
    )
    assert src.base_url == "https://api.example.com"


def test_empty_metric_path_returns_no_records(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPD_TEST_TOKEN", "tok")
    t = FakeTransport(body=METRIC_PAYLOAD)
    src = AppDynamicsSource(_config(), transport=t)
    assert src.query({}) == []
    assert t.calls == []  # no request issued without a metric path
