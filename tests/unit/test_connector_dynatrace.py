"""Unit tests for the Dynatrace APM connector (mocked transport, no network)."""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.dynatrace import (
    ConnectorConfigError,
    DynatraceSource,
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
        raise ConnectionError("boom")


METRICS_PAYLOAD: dict[str, Any] = {
    "result": [
        {
            "metricId": "builtin:host.cpu.usage",
            "data": [
                {
                    "dimensions": ["HOST-ABC123"],
                    "dimensionMap": {"dt.entity.host": "HOST-ABC123"},
                    "timestamps": [1718000000000, 1718000060000],
                    "values": [12.5, 13.0],
                }
            ],
        }
    ]
}

PROBLEMS_PAYLOAD: dict[str, Any] = {
    "problems": [
        {
            "problemId": "PROB-1",
            "displayId": "P-1",
            "title": "High CPU on web tier",
            "status": "OPEN",
            "startTime": 1718000000000,
            "severityLevel": "PERFORMANCE",
            "impactLevel": "INFRASTRUCTURE",
        }
    ]
}


def _config(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "dt-prod",
        "base_url": "https://abc12345.live.dynatrace.com",
        "token_env": "DT_TEST_TOKEN",
    }
    base.update(over)
    return base


def test_kind_and_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DT_TEST_TOKEN", "tok")
    t = FakeTransport()
    src = DynatraceSource(_config(), transport=t)
    assert DynatraceSource.KIND == "metrics"
    assert src.KIND in {"metrics", "traces"}
    assert src.name == "dt-prod"


def test_secret_read_from_env_not_hardcoded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DT_TEST_TOKEN", "secret-token-xyz")
    t = FakeTransport(body=METRICS_PAYLOAD)
    src = DynatraceSource(_config(), transport=t)
    src.query({"metric_selector": "builtin:host.cpu.usage"})
    assert t.calls, "transport was never called"
    assert t.calls[0]["headers"]["Authorization"] == "Api-Token secret-token-xyz"


def test_missing_env_var_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DT_TEST_TOKEN", raising=False)
    with pytest.raises(ConnectorConfigError):
        DynatraceSource(_config(), transport=FakeTransport())


def test_normalization_of_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DT_TEST_TOKEN", "tok")
    t = FakeTransport(body=METRICS_PAYLOAD)
    src = DynatraceSource(_config(), transport=t)
    records = src.query({"metric_selector": "builtin:host.cpu.usage", "from": "now-2h"})

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
    assert first["source"] == "dt-prod"
    assert first["message"] == "builtin:host.cpu.usage"
    assert first["value"] == 12.5
    assert first["ts"] == 1718000000000 / 1000.0
    assert first["labels"]["dimensions"] == ["HOST-ABC123"]
    assert first["raw"]["values"] == [12.5, 13.0]

    # metricSelector + from are sent on the query.
    call_url = t.calls[0]["url"]
    assert "/api/v2/metrics/query" in call_url
    assert "metricSelector=builtin" in call_url
    assert "from=now-2h" in call_url


def test_problems_pull_normalizes_incidents(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DT_TEST_TOKEN", "tok")

    class TwoEndpointTransport:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get(self, url: str, *, headers: dict[str, str], timeout: float) -> tuple[int, dict[str, Any]]:
            self.calls.append(url)
            if "/problems" in url:
                return 200, PROBLEMS_PAYLOAD
            return 200, METRICS_PAYLOAD

    t = TwoEndpointTransport()
    src = DynatraceSource(_config(), transport=t)
    records = src.query({"metric_selector": "builtin:host.cpu.usage", "include_problems": True})

    problems = [r for r in records if r["raw"].get("problemId") == "PROB-1"]
    assert len(problems) == 1
    prob = problems[0]
    assert prob["kind"] == "metrics"
    assert prob["level_or_status"] == "open"
    assert prob["message"] == "High CPU on web tier"
    assert prob["ts"] == 1718000000000 / 1000.0
    assert any("/api/v2/problems" in u for u in t.calls)


def test_health_ok_with_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DT_TEST_TOKEN", "tok")
    src = DynatraceSource(_config(), transport=FakeTransport(status=200, body={}))
    h = src.health()
    assert h["ok"] is True
    assert "detail" in h


def test_health_not_ok_on_error_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DT_TEST_TOKEN", "tok")
    src = DynatraceSource(_config(), transport=FakeTransport(status=401, body={}))
    h = src.health()
    assert h["ok"] is False
    assert "HTTP 401" in h["detail"]


def test_health_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DT_TEST_TOKEN", "tok")
    src = DynatraceSource(_config(), transport=RaisingTransport())
    h = src.health()
    assert h["ok"] is False
    assert h["detail"] == "health check failed"


@pytest.mark.parametrize(
    "bad_url",
    [
        "https://127.0.0.1",
        "https://localhost",
        "http://10.0.0.5",
        "https://192.168.1.10:8080",
        "https://169.254.169.254",
        "http://metadata.google.internal",
        "https://[::1]",
        "https://[fd00::1]",
    ],
)
def test_internal_base_url_rejected(bad_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DT_TEST_TOKEN", "tok")
    with pytest.raises(ConnectorConfigError):
        DynatraceSource(_config(base_url=bad_url), transport=FakeTransport())


def test_public_base_url_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DT_TEST_TOKEN", "tok")
    src = DynatraceSource(_config(base_url="https://8.8.8.8/api"), transport=FakeTransport())
    assert src.base_url == "https://8.8.8.8/api"


def test_bad_scheme_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DT_TEST_TOKEN", "tok")
    with pytest.raises(ConnectorConfigError):
        DynatraceSource(_config(base_url="file:///etc/passwd"), transport=FakeTransport())
