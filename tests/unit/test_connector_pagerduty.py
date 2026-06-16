"""Unit tests for the PagerDuty incident-source connector.

Transport is fully MOCKED via ``httpx.MockTransport`` — no real network.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from general_ludd.connectors.pagerduty import PagerDutySource

_TOKEN = "pd-secret-token-DO-NOT-LEAK"
_ENV = "PAGERDUTY_TOKEN"

_CANNED = {
    "incidents": [
        {
            "id": "PINC1",
            "incident_number": 4242,
            "title": "Checkout latency spike",
            "status": "triggered",
            "urgency": "high",
            "created_at": "2026-06-16T08:30:00Z",
            "service": {"summary": "checkout-api"},
            "escalation_policy": {"summary": "Payments EP"},
            "assignments": [{"assignee": {"summary": "Ada Lovelace"}}],
        },
        {
            "id": "PINC2",
            "incident_number": 4243,
            "title": "DB connection pool exhausted",
            "status": "acknowledged",
            "urgency": "low",
            "created_at": "2026-06-16T09:00:00Z",
            "service": {"summary": "orders-db"},
            "escalation_policy": {"summary": "DB EP"},
            "assignments": [],
        },
    ]
}


def _capture_transport(captured: dict[str, Any], status: int = 200, body: Any = None):
    body = _CANNED if body is None else body

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


# --------------------------------------------------------------------------
# contract attributes
# --------------------------------------------------------------------------


def test_kind_and_name():
    src = PagerDutySource({}, transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    assert src.KIND == "incidents"
    assert PagerDutySource.KIND == "incidents"
    assert src.name == "pagerduty"
    named = PagerDutySource({"name": "pd-prod"}, transport=src._transport)
    assert named.name == "pd-prod"


# --------------------------------------------------------------------------
# normalization
# --------------------------------------------------------------------------


def test_query_normalizes_incidents(monkeypatch):
    monkeypatch.setenv(_ENV, _TOKEN)
    captured: dict[str, Any] = {}
    src = PagerDutySource({}, transport=_capture_transport(captured))

    records = src.query({"since": "2026-06-16T00:00:00Z", "until": "2026-06-16T23:59:59Z"})

    assert len(records) == 2
    rec = records[0]
    assert rec["ts"] == "2026-06-16T08:30:00Z"
    assert rec["source"] == "pagerduty"
    assert rec["kind"] == "incidents"
    assert rec["level_or_status"] == "triggered"
    assert rec["message"] == "Checkout latency spike"
    assert rec["value"] is None
    assert rec["labels"] == {
        "service": "checkout-api",
        "urgency": "high",
        "escalation_policy": "Payments EP",
        "incident_number": 4242,
        "assignee": "Ada Lovelace",
    }
    assert rec["raw"]["id"] == "PINC1"
    # second record has no assignment -> assignee None, status used
    assert records[1]["labels"]["assignee"] is None
    assert records[1]["level_or_status"] == "acknowledged"

    # request shape: since/until/statuses[] present
    req = captured["request"]
    assert req.url.path == "/incidents"
    assert "since=2026-06-16T00%3A00%3A00Z" in str(req.url)
    assert "statuses%5B%5D=triggered" in str(req.url) or "statuses[]=triggered" in str(req.url)


# --------------------------------------------------------------------------
# auth header from env
# --------------------------------------------------------------------------


def test_auth_header_from_env(monkeypatch):
    monkeypatch.setenv(_ENV, _TOKEN)
    captured: dict[str, Any] = {}
    src = PagerDutySource({}, transport=_capture_transport(captured))
    src.query()
    assert captured["request"].headers["Authorization"] == f"Token token={_TOKEN}"


def test_custom_token_env(monkeypatch):
    monkeypatch.delenv(_ENV, raising=False)
    monkeypatch.setenv("MY_PD", _TOKEN)
    captured: dict[str, Any] = {}
    src = PagerDutySource({"token_env": "MY_PD"}, transport=_capture_transport(captured))
    src.query()
    assert captured["request"].headers["Authorization"] == f"Token token={_TOKEN}"


def test_missing_token_raises_in_query(monkeypatch):
    monkeypatch.delenv(_ENV, raising=False)
    src = PagerDutySource({}, transport=_capture_transport({}))
    with pytest.raises(RuntimeError):
        src.query()


# --------------------------------------------------------------------------
# SSRF guard
# --------------------------------------------------------------------------


def test_ssrf_rejects_internal_base_url():
    with pytest.raises(ValueError):
        PagerDutySource({"base_url": "http://127.0.0.1:8080"})
    with pytest.raises(ValueError):
        PagerDutySource({"base_url": "http://10.0.0.5"})
    with pytest.raises(ValueError):
        PagerDutySource({"base_url": "http://169.254.169.254"})


def test_ssrf_allow_private_opt_in():
    src = PagerDutySource(
        {"base_url": "http://127.0.0.1:8080", "allow_private": True},
        transport=httpx.MockTransport(lambda r: httpx.Response(200)),
    )
    assert src._base_url == "http://127.0.0.1:8080"


def test_default_base_url_not_blocked(monkeypatch):
    monkeypatch.setenv(_ENV, _TOKEN)
    src = PagerDutySource({}, transport=_capture_transport({}))
    assert src._base_url == "https://api.pagerduty.com"


# --------------------------------------------------------------------------
# no token leakage
# --------------------------------------------------------------------------


def test_token_never_in_records_or_labels(monkeypatch):
    monkeypatch.setenv(_ENV, _TOKEN)
    captured: dict[str, Any] = {}
    src = PagerDutySource({}, transport=_capture_transport(captured))
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

    src = PagerDutySource({}, transport=httpx.MockTransport(boom))
    result = src.health()
    assert result["ok"] is False
    assert _TOKEN not in json.dumps(result)


# --------------------------------------------------------------------------
# health
# --------------------------------------------------------------------------


def test_health_ok(monkeypatch):
    monkeypatch.setenv(_ENV, _TOKEN)
    src = PagerDutySource({}, transport=_capture_transport({}))
    result = src.health()
    assert result["ok"] is True
    assert "detail" in result


def test_health_not_ok_on_bad_status(monkeypatch):
    monkeypatch.setenv(_ENV, _TOKEN)
    src = PagerDutySource(
        {}, transport=httpx.MockTransport(lambda r: httpx.Response(401, json={}))
    )
    result = src.health()
    assert result["ok"] is False
    assert "401" in result["detail"]


def test_health_missing_token(monkeypatch):
    monkeypatch.delenv(_ENV, raising=False)
    src = PagerDutySource({}, transport=_capture_transport({}))
    result = src.health()
    assert result["ok"] is False


def test_health_never_raises(monkeypatch):
    monkeypatch.setenv(_ENV, _TOKEN)

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    src = PagerDutySource({}, transport=httpx.MockTransport(boom))
    result = src.health()  # must not raise
    assert result["ok"] is False
