"""Unit tests for the Grafana OnCall incident-source connector.

Transport is fully MOCKED via ``httpx.MockTransport`` — no real network.
"""

from __future__ import annotations

import json
import socket
from typing import Any

import httpx
import pytest

from general_ludd.connectors import grafana_oncall as _gc
from general_ludd.connectors.grafana_oncall import GrafanaOnCallSource

_TOKEN = "grafana-secret-token-DO-NOT-LEAK"
_ENV = "GRAFANA_ONCALL_TOKEN"
_PUBLIC_BASE = "https://oncall.example-grafana.net"


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    """Make the SSRF guard's DNS lookup deterministic in the sandbox.

    The example base_url (``oncall.example-grafana.net``) does not resolve in
    the test sandbox, which would make the fail-closed SSRF guard raise during
    normal construction. Stub ``getaddrinfo`` to a stable PUBLIC IP so the guard
    passes legitimately (the guard logic — including private-address rejection —
    is exercised unchanged). Tests using literal IPs or ``allow_private=True``
    never reach ``getaddrinfo`` and are unaffected.
    """

    def _fake_getaddrinfo(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(_gc.socket, "getaddrinfo", _fake_getaddrinfo)

_CANNED = {
    "results": [
        {
            "id": "ag-1",
            "title": "High error rate on api-gateway",
            "state": "firing",
            "created_at": "2026-06-16T08:30:00Z",
            "integration": "prometheus",
            "team": "platform",
            "acknowledged_by": None,
        },
        {
            "id": "ag-2",
            "title": "Pod crashloop",
            "state": "acknowledged",
            "created_at": "2026-06-16T09:00:00Z",
            "integration": "alertmanager",
            "team": "infra",
            "acknowledged_by": "ada",
        },
    ]
}


def _capture_transport(captured: dict[str, Any], status: int = 200, body: Any = None):
    body = _CANNED if body is None else body

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


def _make(captured: dict[str, Any], extra: dict[str, Any] | None = None, **kw):
    cfg: dict[str, Any] = {"base_url": _PUBLIC_BASE}
    if extra:
        cfg.update(extra)
    return GrafanaOnCallSource(cfg, transport=_capture_transport(captured, **kw))


def test_kind_and_name():
    src = GrafanaOnCallSource(
        {"base_url": _PUBLIC_BASE},
        transport=httpx.MockTransport(lambda r: httpx.Response(200)),
    )
    assert src.KIND == "incidents"
    assert GrafanaOnCallSource.KIND == "incidents"
    assert src.name == "grafana_oncall"
    named = GrafanaOnCallSource(
        {"base_url": _PUBLIC_BASE, "name": "oncall-prod"}, transport=src._transport
    )
    assert named.name == "oncall-prod"


def test_base_url_required():
    with pytest.raises(ValueError):
        GrafanaOnCallSource({})


def test_query_normalizes_alert_groups(monkeypatch):
    monkeypatch.setenv(_ENV, _TOKEN)
    captured: dict[str, Any] = {}
    src = _make(captured)

    records = src.query({"state": "firing", "limit": 25})

    assert len(records) == 2
    rec = records[0]
    assert rec["ts"] == "2026-06-16T08:30:00Z"
    assert rec["source"] == "grafana_oncall"
    assert rec["kind"] == "incidents"
    assert rec["level_or_status"] == "firing"
    assert rec["message"] == "High error rate on api-gateway"
    assert rec["value"] is None
    assert rec["labels"] == {
        "integration": "prometheus",
        "team": "platform",
        "state": "firing",
        "acknowledged_by": None,
    }
    assert rec["raw"]["id"] == "ag-1"
    assert records[1]["labels"]["acknowledged_by"] == "ada"

    req = captured["request"]
    assert req.url.path == "/api/v1/alert_groups"
    assert "state=firing" in str(req.url)
    assert "perpage=25" in str(req.url)


def test_callable_transport_compatibility(monkeypatch):
    monkeypatch.setenv(_ENV, _TOKEN)
    calls: list[dict[str, Any]] = []

    def transport(method: str, url: str, **kwargs: Any) -> tuple[int, object]:
        calls.append({"method": method, "url": url, **kwargs})
        return 200, _CANNED

    src = GrafanaOnCallSource(
        {"base_url": _PUBLIC_BASE},
        transport=transport,
    )

    records = src.query({"state": "firing"})

    assert len(records) == 2
    assert calls[0]["method"] == "GET"
    assert "state=firing" in calls[0]["url"]


@pytest.mark.parametrize("body", [b"raw", "text"])
def test_callable_transport_accepts_textual_bodies(monkeypatch, body):
    monkeypatch.setenv(_ENV, _TOKEN)

    def transport(_method: str, _url: str, **_kwargs: Any) -> tuple[int, object]:
        return 200, body

    src = GrafanaOnCallSource({"base_url": _PUBLIC_BASE}, transport=transport)
    assert src.health()["ok"] is True


def test_query_handles_bare_list_payload(monkeypatch):
    monkeypatch.setenv(_ENV, _TOKEN)
    captured: dict[str, Any] = {}
    src = _make(captured, body=_CANNED["results"])
    records = src.query()
    assert len(records) == 2
    assert records[0]["level_or_status"] == "firing"


def test_query_handles_data_and_unknown_payload_shapes(monkeypatch):
    monkeypatch.setenv(_ENV, _TOKEN)
    data_source = _make({}, body={"data": _CANNED["results"]})
    assert len(data_source.query({"limit": "default"})) == 2
    unknown_source = _make({}, body={"unexpected": True})
    assert unknown_source.query() == []


def test_auth_header_from_env(monkeypatch):
    monkeypatch.setenv(_ENV, _TOKEN)
    captured: dict[str, Any] = {}
    src = _make(captured)
    src.query()
    # raw token, no scheme prefix
    assert captured["request"].headers["Authorization"] == _TOKEN


def test_custom_token_env(monkeypatch):
    monkeypatch.delenv(_ENV, raising=False)
    monkeypatch.setenv("MY_GRAFANA", _TOKEN)
    captured: dict[str, Any] = {}
    src = GrafanaOnCallSource(
        {"base_url": _PUBLIC_BASE, "token_env": "MY_GRAFANA"},
        transport=_capture_transport(captured),
    )
    src.query()
    assert captured["request"].headers["Authorization"] == _TOKEN


def test_missing_token_raises_in_query(monkeypatch):
    monkeypatch.delenv(_ENV, raising=False)
    captured: dict[str, Any] = {}
    src = _make(captured)
    with pytest.raises(RuntimeError):
        src.query()


def test_ssrf_rejects_internal_base_url():
    with pytest.raises(ValueError):
        GrafanaOnCallSource({"base_url": "http://127.0.0.1:8080"})
    with pytest.raises(ValueError):
        GrafanaOnCallSource({"base_url": "http://10.1.2.3"})
    with pytest.raises(ValueError):
        GrafanaOnCallSource({"base_url": "http://169.254.169.254"})


@pytest.mark.parametrize("host", ["metadata", "instance-data", "ip6-localhost"])
def test_ssrf_rejects_metadata_alias_names_without_dns(monkeypatch, host):
    # These names were NOT in this connector's own bespoke blocklist (it only
    # knew "localhost" + 4 TLD suffixes). host_is_blocked catches them and
    # must short-circuit BEFORE any DNS resolution is attempted.
    def _boom(*_args, **_kwargs):
        raise AssertionError("getaddrinfo must not be called for a blocked literal name")

    monkeypatch.setattr(_gc.socket, "getaddrinfo", _boom)

    with pytest.raises(ValueError):
        GrafanaOnCallSource({"base_url": f"http://{host}:8080"})


def test_ssrf_rejects_resolved_cgnat_address(monkeypatch):
    # 100.70.1.1 sits in the 100.64.0.0/10 carrier-grade-NAT range: is_private
    # is False for it in Python's ipaddress module, so the OLD local flag set
    # (missing `not is_global`) would NOT have blocked it once resolved.
    def _fake_getaddrinfo(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("100.70.1.1", 443))]

    monkeypatch.setattr(_gc.socket, "getaddrinfo", _fake_getaddrinfo)

    with pytest.raises(ValueError, match="non-public"):
        GrafanaOnCallSource({"base_url": "https://cgnat-internal.example.com"})


def test_ssrf_allow_private_opt_in():
    src = GrafanaOnCallSource(
        {"base_url": "http://oncall.internal.lan:8080", "allow_private": True},
        transport=httpx.MockTransport(lambda r: httpx.Response(200)),
    )
    assert src._base_url == "http://oncall.internal.lan:8080"


def test_tuple_response_and_callable_client_edge_contracts():
    response = _gc._TupleResponse("bad-status", {})
    assert response.status_code == 0
    assert response.json() == {}
    failing = _gc._TupleResponse(503, {})
    with pytest.raises(RuntimeError, match="HTTP 503"):
        failing.raise_for_status()

    returned = _gc._TupleResponse(200, {"results": []})
    client = _gc._CallableClient(lambda *_args, **_kwargs: returned)
    assert client.get("https://example.com") is returned


def test_token_never_in_records_or_labels(monkeypatch):
    monkeypatch.setenv(_ENV, _TOKEN)
    captured: dict[str, Any] = {}
    src = _make(captured)
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

    src = GrafanaOnCallSource({"base_url": _PUBLIC_BASE}, transport=httpx.MockTransport(boom))
    result = src.health()
    assert result["ok"] is False
    assert _TOKEN not in json.dumps(result)


def test_health_ok(monkeypatch):
    monkeypatch.setenv(_ENV, _TOKEN)
    captured: dict[str, Any] = {}
    src = _make(captured)
    result = src.health()
    assert result["ok"] is True
    assert "detail" in result


def test_health_not_ok_on_bad_status(monkeypatch):
    monkeypatch.setenv(_ENV, _TOKEN)
    src = GrafanaOnCallSource(
        {"base_url": _PUBLIC_BASE},
        transport=httpx.MockTransport(lambda r: httpx.Response(500, json={})),
    )
    result = src.health()
    assert result["ok"] is False
    assert result["detail"] == "unexpected status 500"


def test_health_missing_token(monkeypatch):
    monkeypatch.delenv(_ENV, raising=False)
    captured: dict[str, Any] = {}
    src = _make(captured)
    result = src.health()
    assert result["ok"] is False


def test_health_never_raises(monkeypatch):
    monkeypatch.setenv(_ENV, _TOKEN)

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    src = GrafanaOnCallSource({"base_url": _PUBLIC_BASE}, transport=httpx.MockTransport(boom))
    result = src.health()
    assert result["ok"] is False
