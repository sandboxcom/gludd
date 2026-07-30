"""Unit tests for OpsgenieSource incident connector (mocked transport)."""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.opsgenie import OpsgenieSource


class FakeResponse:
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


CANNED_ALERTS = {
    "data": [
        {
            "id": "alert-1",
            "tinyId": "42",
            "message": "CPU saturation on web-01",
            "status": "open",
            "priority": "P1",
            "createdAt": "2026-06-10T12:34:56.789Z",
            "owner": "ada@example.com",
            "tags": ["cpu", "web"],
            "source": "prometheus",
        },
        {
            "id": "alert-2",
            "tinyId": "43",
            "message": "Disk almost full",
            "status": "closed",
            "priority": "P3",
            "createdAt": "2026-06-11T01:02:03.000Z",
            "owner": "grace@example.com",
            "tags": ["disk"],
            "source": "node-exporter",
        },
    ]
}


def _config(token_env: str = "OG_TOKEN", **extra: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {"token_env": token_env}
    cfg.update(extra)
    return cfg


def test_kind_and_name_attributes() -> None:
    src = OpsgenieSource(_config())
    assert OpsgenieSource.KIND == "incidents"
    assert src.name


def test_query_normalizes_alerts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OG_TOKEN", "genie-secret")
    transport = FakeTransport(FakeResponse(200, CANNED_ALERTS))
    src = OpsgenieSource(_config(), transport=transport)

    records = src.query({"query": "status:open", "limit": 50})

    assert isinstance(records, list)
    assert len(records) == 2

    rec = records[0]
    assert rec["source"] == src.name
    assert rec["kind"] == "incidents"
    # createdAt parsed to an ISO timestamp string
    assert rec["ts"].startswith("2026-06-10T12:34:56")
    assert "open" in rec["level_or_status"]
    assert "P1" in rec["level_or_status"]
    assert rec["message"] == "CPU saturation on web-01"
    labels = rec["labels"]
    assert labels["id"] == "alert-1"
    assert labels["tinyId"] == "42"
    assert labels["owner"] == "ada@example.com"
    assert labels["tags"] == ["cpu", "web"]
    assert labels["source"] == "prometheus"
    assert rec["raw"]["id"] == "alert-1"


def test_status_mapping_open_and_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OG_TOKEN", "genie-secret")
    transport = FakeTransport(FakeResponse(200, CANNED_ALERTS))
    src = OpsgenieSource(_config(), transport=transport)
    records = src.query({})
    statuses = [r["level_or_status"] for r in records]
    assert any("open" in s for s in statuses)
    assert any("closed" in s for s in statuses)


def test_auth_header_genie_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OG_TOKEN", "abc-123")
    transport = FakeTransport(FakeResponse(200, CANNED_ALERTS))
    src = OpsgenieSource(_config(), transport=transport)

    src.query({})

    headers = transport.calls[0]["headers"]
    assert headers["Authorization"] == "GenieKey abc-123"


def test_token_never_hardcoded_missing_env_raises() -> None:
    src = OpsgenieSource(_config(token_env="DEFINITELY_MISSING_OG_TOKEN"))
    with pytest.raises(RuntimeError):
        src.query({})


def test_ssrf_internal_base_rejected_in_constructor() -> None:
    with pytest.raises(ValueError):
        OpsgenieSource(_config(base_url="http://localhost/v2/alerts"))
    with pytest.raises(ValueError):
        OpsgenieSource(_config(base_url="http://169.254.169.254/v2/alerts"))
    with pytest.raises(ValueError):
        OpsgenieSource(_config(base_url="http://192.168.1.10/x"))


def test_health_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OG_TOKEN", "tok")
    transport = FakeTransport(FakeResponse(200, {"data": []}))
    src = OpsgenieSource(_config(), transport=transport)
    result = src.health()
    assert set(result) >= {"ok", "detail"}
    assert result["ok"] is True


def test_health_not_ok_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OG_TOKEN", "tok")
    transport = FakeTransport(RuntimeError("boom"))
    src = OpsgenieSource(_config(), transport=transport)
    result = src.health()
    assert result["ok"] is False
    assert result["detail"]


def test_health_bad_status_not_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OG_TOKEN", "tok")
    transport = FakeTransport(FakeResponse(403, {"message": "forbidden"}))
    src = OpsgenieSource(_config(), transport=transport)
    result = src.health()
    assert result["ok"] is False


def test_query_passes_query_and_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OG_TOKEN", "tok")
    transport = FakeTransport(FakeResponse(200, CANNED_ALERTS))
    src = OpsgenieSource(_config(), transport=transport)

    src.query({"query": "priority:P1", "limit": 25})

    params = transport.calls[0]["params"]
    assert params["query"] == "priority:P1"
    assert params["limit"] == 25
    assert "/v2/alerts" in transport.calls[0]["url"]


def test_callable_transport_compatibility(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OG_TOKEN", "tok")
    calls: list[dict[str, Any]] = []

    def transport(
        method: str,
        url: str,
        **kwargs: Any,
    ) -> tuple[int, object]:
        calls.append({"method": method, "url": url, **kwargs})
        return 200, CANNED_ALERTS

    src = OpsgenieSource(_config(), transport=transport)

    records = src.query({"limit": 25})

    assert len(records) == 2
    assert calls[0]["method"] == "GET"
    assert calls[0]["params"] == {"limit": 25}


def test_timeout_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OG_TOKEN", "tok")
    transport = FakeTransport(FakeResponse(200, {"data": []}))
    src = OpsgenieSource(_config(timeout=9.0), transport=transport)
    src.query({})
    assert transport.calls[0]["timeout"] == 9.0
