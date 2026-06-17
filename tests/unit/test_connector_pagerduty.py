"""Unit tests for PagerDutySource incident connector (mocked transport)."""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.pagerduty import PagerDutySource


class FakeResponse:
    """Minimal stand-in for an HTTP response object."""

    def __init__(
        self,
        status_code: int = 200,
        json_data: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}

    def json(self) -> dict[str, Any]:
        return self._json_data


class FakeTransport:
    """Injectable transport recording the last request and returning canned data."""

    def __init__(self, response: FakeResponse | Exception) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: Any = None,
        timeout: float | None = None,
    ) -> FakeResponse:
        self.calls.append(
            {
                "url": url,
                "headers": headers or {},
                "params": params,
                "timeout": timeout,
            }
        )
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


CANNED_INCIDENTS = {
    "incidents": [
        {
            "id": "PINC1",
            "title": "Database connection pool exhausted",
            "status": "triggered",
            "urgency": "high",
            "created_at": "2026-06-10T12:34:56Z",
            "service": {"summary": "Payments API"},
            "escalation_policy": {"summary": "Tier-1 oncall"},
            "assignments": [
                {"assignee": {"summary": "Ada Lovelace"}},
                {"assignee": {"summary": "Grace Hopper"}},
            ],
        },
        {
            "id": "PINC2",
            "title": "Latency spike",
            "status": "resolved",
            "urgency": "low",
            "created_at": "2026-06-11T08:00:00Z",
            "service": {"summary": "Search"},
            "escalation_policy": {"summary": "Tier-2 oncall"},
            "assignments": [],
        },
    ]
}


def _config(token_env: str = "PD_TOKEN", **extra: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {"token_env": token_env}
    cfg.update(extra)
    return cfg


def test_kind_and_name_attributes() -> None:
    src = PagerDutySource(_config())
    assert PagerDutySource.KIND == "incidents"
    assert src.name


def test_query_normalizes_incidents(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PD_TOKEN", "secret-key")
    transport = FakeTransport(FakeResponse(200, CANNED_INCIDENTS))
    src = PagerDutySource(_config(), transport=transport)

    records = src.query({"since": "2026-06-10T00:00:00Z"})

    assert isinstance(records, list)
    assert len(records) == 2

    rec = records[0]
    assert rec["source"] == src.name
    assert rec["kind"] == "incidents"
    assert rec["ts"] == "2026-06-10T12:34:56Z"
    assert rec["level_or_status"] == "triggered"
    assert "Database connection pool exhausted" in rec["message"]
    assert "high" in rec["message"]
    labels = rec["labels"]
    assert labels["id"] == "PINC1"
    assert labels["service.summary"] == "Payments API"
    assert labels["escalation_policy"] == "Tier-1 oncall"
    assert "Ada Lovelace" in labels["assignees"]
    assert "Grace Hopper" in labels["assignees"]
    assert rec["raw"]["id"] == "PINC1"
    assert rec["value"] is None or isinstance(rec["value"], (int, float))


def test_status_mapping_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PD_TOKEN", "secret-key")
    transport = FakeTransport(FakeResponse(200, CANNED_INCIDENTS))
    src = PagerDutySource(_config(), transport=transport)
    records = src.query({})
    statuses = {r["level_or_status"] for r in records}
    assert statuses == {"triggered", "resolved"}


def test_auth_header_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PD_TOKEN", "tok-12345")
    transport = FakeTransport(FakeResponse(200, CANNED_INCIDENTS))
    src = PagerDutySource(_config(), transport=transport)

    src.query({})

    assert transport.calls, "transport was not invoked"
    headers = transport.calls[0]["headers"]
    assert headers["Authorization"] == "Token token=tok-12345"
    assert "vnd.pagerduty+json" in headers["Accept"]
    assert "version=2" in headers["Accept"]


def test_token_never_hardcoded_missing_env_raises() -> None:
    # No env var set -> token resolution should fail, not silently use a default.
    src = PagerDutySource(_config(token_env="DEFINITELY_MISSING_PD_TOKEN"))
    with pytest.raises(RuntimeError):
        src.query({})


def test_ssrf_internal_base_rejected_in_constructor() -> None:
    with pytest.raises(ValueError):
        PagerDutySource(_config(base_url="http://localhost/incidents"))
    with pytest.raises(ValueError):
        PagerDutySource(_config(base_url="http://169.254.169.254/latest"))
    with pytest.raises(ValueError):
        PagerDutySource(_config(base_url="http://10.0.0.5/x"))


def test_health_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PD_TOKEN", "tok")
    transport = FakeTransport(FakeResponse(200, {"incidents": []}))
    src = PagerDutySource(_config(), transport=transport)
    result = src.health()
    assert set(result) >= {"ok", "detail"}
    assert result["ok"] is True


def test_health_not_ok_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PD_TOKEN", "tok")
    transport = FakeTransport(RuntimeError("network down"))
    src = PagerDutySource(_config(), transport=transport)
    result = src.health()
    assert result["ok"] is False
    assert result["detail"]


def test_health_bad_status_not_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PD_TOKEN", "tok")
    transport = FakeTransport(FakeResponse(401, {"error": "unauthorized"}))
    src = PagerDutySource(_config(), transport=transport)
    result = src.health()
    assert result["ok"] is False


def test_query_passes_filters_as_params(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PD_TOKEN", "tok")
    transport = FakeTransport(FakeResponse(200, CANNED_INCIDENTS))
    src = PagerDutySource(_config(), transport=transport)

    src.query(
        {
            "since": "2026-06-10T00:00:00Z",
            "until": "2026-06-12T00:00:00Z",
            "statuses": ["triggered", "acknowledged"],
            "service_ids": ["PSVC1"],
        }
    )

    params = transport.calls[0]["params"]
    assert params["since"] == "2026-06-10T00:00:00Z"
    assert params["until"] == "2026-06-12T00:00:00Z"
    assert params["statuses[]"] == ["triggered", "acknowledged"]
    assert params["service_ids[]"] == ["PSVC1"]


def test_fetch_log_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PD_TOKEN", "tok")
    payload = {"log_entries": [{"id": "LE1", "type": "trigger_log_entry"}]}
    transport = FakeTransport(FakeResponse(200, payload))
    src = PagerDutySource(_config(), transport=transport)

    entries = src.fetch_log_entries("PINC1")

    assert entries == payload["log_entries"]
    assert "PINC1" in transport.calls[0]["url"]
    assert "log_entries" in transport.calls[0]["url"]


def test_timeout_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PD_TOKEN", "tok")
    transport = FakeTransport(FakeResponse(200, {"incidents": []}))
    src = PagerDutySource(_config(timeout=7.5), transport=transport)
    src.query({})
    assert transport.calls[0]["timeout"] == 7.5
