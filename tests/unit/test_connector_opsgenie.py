"""Unit tests for the Opsgenie incident-source connector.

Transport is fully MOCKED via ``httpx.MockTransport`` — no real network.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from general_ludd.connectors.opsgenie import OpsgenieSource

_TOKEN = "genie-secret-token-DO-NOT-LEAK"
_ENV = "OPSGENIE_API_KEY"

_CANNED = {
    "data": [
        {
            "id": "og-1",
            "tinyId": "42",
            "message": "Disk almost full on db-1",
            "status": "open",
            "priority": "P1",
            "createdAt": "2026-06-16T08:30:00Z",
            "owner": "ada@example.com",
            "ownerTeamId": "team-payments",
            "acknowledged": False,
        },
        {
            "id": "og-2",
            "tinyId": "43",
            "message": "Latency degraded",
            "status": "acked",
            "priority": "P3",
            "createdAt": "2026-06-16T09:00:00Z",
            "owner": "grace@example.com",
            "team": "team-orders",
            "acknowledged": True,
        },
    ]
}


def _capture_transport(captured: dict[str, Any], status: int = 200, body: Any = None):
    body = _CANNED if body is None else body

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


def test_kind_and_name():
    src = OpsgenieSource({}, transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    assert src.KIND == "incidents"
    assert OpsgenieSource.KIND == "incidents"
    assert src.name == "opsgenie"
    named = OpsgenieSource({"name": "og-eu"}, transport=src._transport)
    assert named.name == "og-eu"


def test_query_normalizes_alerts(monkeypatch):
    monkeypatch.setenv(_ENV, _TOKEN)
    captured: dict[str, Any] = {}
    src = OpsgenieSource({}, transport=_capture_transport(captured))

    records = src.query({"query": "status:open", "limit": 50})

    assert len(records) == 2
    rec = records[0]
    assert rec["ts"] == "2026-06-16T08:30:00Z"
    assert rec["source"] == "opsgenie"
    assert rec["kind"] == "incidents"
    assert rec["level_or_status"] == "open"
    assert rec["message"] == "Disk almost full on db-1"
    assert rec["value"] is None
    assert rec["labels"] == {
        "priority": "P1",
        "owner": "ada@example.com",
        "team": "team-payments",
        "tinyId": "42",
        "acknowledged": False,
    }
    assert rec["raw"]["id"] == "og-1"
    assert records[1]["labels"]["team"] == "team-orders"
    assert records[1]["labels"]["acknowledged"] is True

    req = captured["request"]
    assert req.url.path == "/v2/alerts"
    assert "query=status%3Aopen" in str(req.url)
    assert "limit=50" in str(req.url)


def test_auth_header_from_env(monkeypatch):
    monkeypatch.setenv(_ENV, _TOKEN)
    captured: dict[str, Any] = {}
    src = OpsgenieSource({}, transport=_capture_transport(captured))
    src.query()
    assert captured["request"].headers["Authorization"] == f"GenieKey {_TOKEN}"


def test_custom_token_env(monkeypatch):
    monkeypatch.delenv(_ENV, raising=False)
    monkeypatch.setenv("MY_OG", _TOKEN)
    captured: dict[str, Any] = {}
    src = OpsgenieSource({"token_env": "MY_OG"}, transport=_capture_transport(captured))
    src.query()
    assert captured["request"].headers["Authorization"] == f"GenieKey {_TOKEN}"


def test_missing_token_raises_in_query(monkeypatch):
    monkeypatch.delenv(_ENV, raising=False)
    src = OpsgenieSource({}, transport=_capture_transport({}))
    with pytest.raises(RuntimeError):
        src.query()


def test_ssrf_rejects_internal_base_url():
    with pytest.raises(ValueError):
        OpsgenieSource({"base_url": "http://127.0.0.1:9000"})
    with pytest.raises(ValueError):
        OpsgenieSource({"base_url": "http://192.168.1.10"})
    with pytest.raises(ValueError):
        OpsgenieSource({"base_url": "http://169.254.169.254"})


def test_ssrf_allow_private_opt_in():
    src = OpsgenieSource(
        {"base_url": "http://127.0.0.1:9000", "allow_private": True},
        transport=httpx.MockTransport(lambda r: httpx.Response(200)),
    )
    assert src._base_url == "http://127.0.0.1:9000"


def test_default_base_url_not_blocked(monkeypatch):
    monkeypatch.setenv(_ENV, _TOKEN)
    src = OpsgenieSource({}, transport=_capture_transport({}))
    assert src._base_url == "https://api.opsgenie.com"


def test_token_never_in_records_or_labels(monkeypatch):
    monkeypatch.setenv(_ENV, _TOKEN)
    captured: dict[str, Any] = {}
    src = OpsgenieSource({}, transport=_capture_transport(captured))
    records = src.query()
    blob = json.dumps(records)
    assert _TOKEN not in blob
    for rec in records:
        assert _TOKEN not in json.dumps(rec["raw"])
        assert _TOKEN not in json.dumps(rec["labels"])


def test_token_never_in_health_error(monkeypatch):
    monkeypatch.setenv(_ENV, _TOKEN)

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    src = OpsgenieSource({}, transport=httpx.MockTransport(boom))
    result = src.health()
    assert result["ok"] is False
    assert _TOKEN not in json.dumps(result)


def test_health_ok(monkeypatch):
    monkeypatch.setenv(_ENV, _TOKEN)
    src = OpsgenieSource({}, transport=_capture_transport({}))
    result = src.health()
    assert result["ok"] is True
    assert "detail" in result


def test_health_not_ok_on_bad_status(monkeypatch):
    monkeypatch.setenv(_ENV, _TOKEN)
    src = OpsgenieSource(
        {}, transport=httpx.MockTransport(lambda r: httpx.Response(403, json={}))
    )
    result = src.health()
    assert result["ok"] is False
    assert "403" in result["detail"]


def test_health_missing_token(monkeypatch):
    monkeypatch.delenv(_ENV, raising=False)
    src = OpsgenieSource({}, transport=_capture_transport({}))
    result = src.health()
    assert result["ok"] is False


def test_health_never_raises(monkeypatch):
    monkeypatch.setenv(_ENV, _TOKEN)

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    src = OpsgenieSource({}, transport=httpx.MockTransport(boom))
    result = src.health()
    assert result["ok"] is False
