"""Structural tests for PagerDuty connector."""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.pagerduty import (
    HttpTransport,
    PagerDutySource,
    _validate_base_url,
)


class FakeResponse:
    def __init__(self, status_code: int = 200, json_data: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}

    def json(self) -> dict[str, Any]:
        return self._json_data


class FakeTransport:
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
        self.calls.append({"url": url, "headers": headers or {}, "params": params, "timeout": timeout})
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


CANNED = {
    "incidents": [
        {
            "id": "INC1",
            "title": "DB pool exhausted",
            "status": "triggered",
            "urgency": "high",
            "created_at": "2026-06-10T12:34:56Z",
            "service": {"summary": "API"},
            "escalation_policy": {"summary": "Tier-1"},
            "assignments": [{"assignee": {"summary": "Alice"}}],
        }
    ]
}


def _cfg(**kw: Any) -> dict[str, Any]:
    return dict(kw)


class TestModuleContract:
    def test_kind_is_incidents(self) -> None:
        assert PagerDutySource.KIND == "incidents"

    def test_default_name(self) -> None:
        src = PagerDutySource(_cfg())
        assert src.name == "pagerduty"

    def test_custom_name(self) -> None:
        src = PagerDutySource(_cfg(name="ops-pd"))
        assert src.name == "ops-pd"

    def test_http_transport_protocol_exists(self) -> None:
        assert HttpTransport is not None

    def test_validate_base_url_public_ok(self) -> None:
        result = _validate_base_url("https://api.example.com/v2")
        assert result == "https://api.example.com/v2"

    def test_validate_base_url_trailing_slash_stripped(self) -> None:
        result = _validate_base_url("https://api.example.com/")
        assert result == "https://api.example.com"


class TestSSRF:
    def test_localhost_rejected(self) -> None:
        with pytest.raises(ValueError):
            PagerDutySource(_cfg(base_url="http://localhost/"))

    def test_metadata_ip_rejected(self) -> None:
        with pytest.raises(ValueError):
            PagerDutySource(_cfg(base_url="http://169.254.169.254/latest"))

    def test_private_ip_rejected(self) -> None:
        with pytest.raises(ValueError):
            PagerDutySource(_cfg(base_url="http://10.0.0.5/"))


class TestQuery:
    def test_normalizes_incident(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PAGERDUTY_TOKEN", "tok")
        t = FakeTransport(FakeResponse(200, CANNED))
        src = PagerDutySource(_cfg(), transport=t)
        records = src.query({"since": "2026-06-10T00:00:00Z"})
        assert len(records) == 1
        r = records[0]
        assert r["source"] == src.name
        assert r["kind"] == "incidents"
        assert r["level_or_status"] == "triggered"
        assert r["labels"]["id"] == "INC1"
        assert r["labels"]["service.summary"] == "API"
        assert r["labels"]["escalation_policy"] == "Tier-1"
        assert r["value"] is None

    def test_empty_query_returns_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PAGERDUTY_TOKEN", "tok")
        t = FakeTransport(FakeResponse(200, {"incidents": []}))
        src = PagerDutySource(_cfg(), transport=t)
        assert src.query() == []

    def test_filters_as_params(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PAGERDUTY_TOKEN", "tok")
        t = FakeTransport(FakeResponse(200, CANNED))
        src = PagerDutySource(_cfg(), transport=t)
        src.query({"since": "Z", "until": "Y", "statuses": ["s1"], "service_ids": ["sv1"]})
        p = t.calls[0]["params"]
        assert p["since"] == "Z"
        assert p["until"] == "Y"
        assert p["statuses[]"] == ["s1"]
        assert p["service_ids[]"] == ["sv1"]

    def test_auth_header(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PAGERDUTY_TOKEN", "tok-123")
        t = FakeTransport(FakeResponse(200, CANNED))
        src = PagerDutySource(_cfg(), transport=t)
        src.query({})
        headers = t.calls[0]["headers"]
        assert headers["Authorization"] == "Token token=tok-123"
        assert "vnd.pagerduty+json" in headers["Accept"]

    def test_missing_env_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PAGERDUTY_TOKEN", raising=False)
        src = PagerDutySource(_cfg(token_env="MISSING_VAR"))
        with pytest.raises(RuntimeError):
            src.query({})

    def test_http_error_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PAGERDUTY_TOKEN", "tok")
        t = FakeTransport(FakeResponse(429, {}))
        src = PagerDutySource(_cfg(), transport=t)
        with pytest.raises(RuntimeError):
            src.query({})

    def test_callable_transport_compatibility(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PAGERDUTY_TOKEN", "tok")
        calls: list[dict[str, Any]] = []

        def transport(
            method: str,
            url: str,
            **kwargs: Any,
        ) -> tuple[int, object]:
            calls.append({"method": method, "url": url, **kwargs})
            return 200, CANNED

        src = PagerDutySource(_cfg(), transport=transport)

        records = src.query({"statuses": ["triggered"]})

        assert len(records) == 1
        assert calls[0]["method"] == "GET"
        assert calls[0]["params"] == {"statuses[]": ["triggered"]}


class TestLogEntries:
    def test_fetch_log_entries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PAGERDUTY_TOKEN", "tok")
        payload = {"log_entries": [{"id": "LE1", "type": "trigger"}]}
        t = FakeTransport(FakeResponse(200, payload))
        src = PagerDutySource(_cfg(), transport=t)
        entries = src.fetch_log_entries("INC1")
        assert entries == payload["log_entries"]
        assert "log_entries" in t.calls[0]["url"]

    def test_error_status_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PAGERDUTY_TOKEN", "tok")
        t = FakeTransport(FakeResponse(500, {}))
        src = PagerDutySource(_cfg(), transport=t)
        with pytest.raises(RuntimeError):
            src.fetch_log_entries("INC1")

    def test_empty_payload_no_crash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PAGERDUTY_TOKEN", "tok")
        t = FakeTransport(FakeResponse(200, {}))
        src = PagerDutySource(_cfg(), transport=t)
        assert isinstance(src.fetch_log_entries("INC1"), list)


class TestHealth:
    def test_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PAGERDUTY_TOKEN", "tok")
        t = FakeTransport(FakeResponse(200, {"incidents": []}))
        src = PagerDutySource(_cfg(), transport=t)
        r = src.health()
        assert r["ok"] is True

    def test_transport_error_never_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PAGERDUTY_TOKEN", "tok")
        t = FakeTransport(RuntimeError("network down"))
        src = PagerDutySource(_cfg(), transport=t)
        r = src.health()
        assert r["ok"] is False

    def test_bad_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PAGERDUTY_TOKEN", "tok")
        t = FakeTransport(FakeResponse(401, {}))
        src = PagerDutySource(_cfg(), transport=t)
        r = src.health()
        assert r["ok"] is False


class TestTimeout:
    def test_custom_timeout_applied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PAGERDUTY_TOKEN", "tok")
        t = FakeTransport(FakeResponse(200, {"incidents": []}))
        src = PagerDutySource(_cfg(timeout=7.5), transport=t)
        src.query({})
        assert t.calls[0]["timeout"] == 7.5

    def test_default_timeout_is_float(self) -> None:
        src = PagerDutySource(_cfg())
        assert isinstance(src.timeout, float)
        assert src.timeout > 0
