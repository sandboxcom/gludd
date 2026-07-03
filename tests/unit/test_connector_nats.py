"""Unit tests for the NATS monitoring-endpoint observability connector.

Self-contained: imports only the connector module under test and uses an
injectable, fully-mocked HTTP transport (no real network, no DNS, no shell).
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.nats import NatsSource

# ---------------------------------------------------------------------------
# Mock transport — routes by URL path.
# ---------------------------------------------------------------------------


class RoutingTransport:
    """Returns a queued (status, json) per URL substring.

    http_get(url, params, headers, timeout) -> (status:int, json)
    """

    def __init__(self, routes: dict[str, tuple[int, Any]]) -> None:
        self._routes = routes
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> tuple[int, Any]:
        self.calls.append(
            {"url": url, "params": params, "headers": headers or {}, "timeout": timeout}
        )
        for needle, resp in self._routes.items():
            if needle in url:
                return resp
        raise AssertionError(f"no route for {url}")


VARZ = {
    "server_id": "NABC123",
    "version": "2.10.7",
    "connections": 42,
    "total_connections": 1000,
    "in_msgs": 50000,
    "out_msgs": 49000,
    "in_bytes": 1234567,
    "out_bytes": 1200000,
    "slow_consumers": 2,
    "subscriptions": 300,
    "mem": 67108864,
}

CONNZ = {"server_id": "NABC123", "num_connections": 42, "total": 42}

SUBSZ = {
    "server_id": "NABC123",
    "num_subscriptions": 300,
    "num_inserts": 1200,
    "num_matches": 980000,
}

GOOD_URL = "https://nats.internal.example.com"


def full_routes() -> dict[str, tuple[int, Any]]:
    return {
        "/varz": (200, VARZ),
        "/connz": (200, CONNZ),
        "/subsz": (200, SUBSZ),
    }


# ---------------------------------------------------------------------------
# Construction / contract
# ---------------------------------------------------------------------------


def test_kind_name_and_construction():
    src = NatsSource({"base_url": GOOD_URL}, http_get=RoutingTransport({}))
    assert NatsSource.KIND == "metrics"
    assert src.kind == "metrics"
    assert isinstance(src.name, str) and src.name


def test_constructs_with_default_transport():
    # No transport injected: the connector falls back to a real stdlib transport
    # so the registry's single-arg ``factory(config)`` build succeeds (the source
    # is NOT silently dropped from ``list_sources()``). No network at construction.
    from general_ludd.connectors.nats import _default_http_get

    src = NatsSource({"base_url": GOOD_URL})
    assert src._http_get is _default_http_get
    assert src.name


def test_default_monitor_port_appended():
    t = RoutingTransport(full_routes())
    src = NatsSource({"base_url": GOOD_URL}, http_get=t)
    src.query({"endpoints": ["varz"]})
    assert ":8222/varz" in t.calls[0]["url"]


def test_explicit_port_preserved():
    t = RoutingTransport(full_routes())
    src = NatsSource({"base_url": f"{GOOD_URL}:7777"}, http_get=t)
    src.query({"endpoints": ["varz"]})
    assert ":7777/varz" in t.calls[0]["url"]


def test_query_is_time_bound():
    t = RoutingTransport(full_routes())
    src = NatsSource({"base_url": GOOD_URL}, http_get=t, timeout=4.0)
    src.query({"endpoints": ["varz"]})
    assert t.calls[0]["timeout"] == 4.0


def test_token_env_injects_bearer(monkeypatch):
    monkeypatch.setenv("NATS_TOKEN", "tok")
    t = RoutingTransport(full_routes())
    src = NatsSource({"base_url": GOOD_URL, "token_env": "NATS_TOKEN"}, http_get=t)
    src.query({"endpoints": ["varz"]})
    assert t.calls[0]["headers"].get("Authorization") == "Bearer tok"


def test_missing_token_env_tolerated(monkeypatch):
    monkeypatch.delenv("NATS_TOKEN", raising=False)
    t = RoutingTransport(full_routes())
    src = NatsSource({"base_url": GOOD_URL, "token_env": "NATS_TOKEN"}, http_get=t)
    src.query({"endpoints": ["varz"]})
    assert "Authorization" not in t.calls[0]["headers"]


# ---------------------------------------------------------------------------
# SSRF literal-host blocking (no DNS)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://127.0.0.1",
        "https://localhost",
        "http://[::1]",
        "http://169.254.169.254/",
        "http://10.0.0.5",
        "http://192.168.1.10",
        "http://172.16.0.9",
        "http://0.0.0.0",
        "http://metadata.google.internal/",
    ],
)
def test_ssrf_rejects_internal_hosts(bad_url):
    with pytest.raises(ValueError):
        NatsSource({"base_url": bad_url}, http_get=RoutingTransport({}))


def test_ssrf_allows_http_for_allowlisted_host():
    src = NatsSource(
        {"base_url": "http://nats.example.com"}, http_get=RoutingTransport({})
    )
    assert src.name


# Canonical SSRF guard coverage — 100.100.100.200 is the Alibaba metadata IP
# the shared general_ludd.security.ssrf.is_url_blocked guarantees.
_CANONICAL_SSRF_URLS = [
    "http://localhost/",
    "http://metadata.google.internal/",
    "http://169.254.169.254/",
    "http://100.100.100.200/",
]


@pytest.mark.parametrize("bad_url", _CANONICAL_SSRF_URLS)
def test_canonical_ssrf_urls_rejected(bad_url):
    with pytest.raises(ValueError):
        NatsSource({"base_url": bad_url}, http_get=RoutingTransport({}))


def test_public_base_url_constructs_after_consolidation():
    src = NatsSource(
        {"base_url": "https://api.example.com"}, http_get=RoutingTransport({})
    )
    assert src.name


def test_ssrf_rejects_non_http_scheme():
    with pytest.raises(ValueError):
        NatsSource(
            {"base_url": "gopher://nats.example.com"}, http_get=RoutingTransport({})
        )


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def test_varz_connections_and_msgs():
    t = RoutingTransport(full_routes())
    src = NatsSource({"base_url": GOOD_URL}, http_get=t)
    records = src.query({"endpoints": ["varz"]})

    conns = next(
        r
        for r in records
        if r["labels"].get("endpoint") == "varz"
        and r["labels"].get("metric") == "connections"
    )
    assert conns["value"] == 42.0
    assert conns["kind"] == "metrics"
    assert conns["labels"]["server_id"] == "NABC123"
    assert conns["level_or_status"] == ""

    in_msgs = next(r for r in records if r["labels"].get("metric") == "in_msgs")
    assert in_msgs["value"] == 50000.0


def test_subsz_subjects_record():
    t = RoutingTransport(full_routes())
    src = NatsSource({"base_url": GOOD_URL}, http_get=t)
    records = src.query({"endpoints": ["subsz"]})
    subs = next(
        r for r in records if r["labels"].get("metric") == "num_subscriptions"
    )
    assert subs["value"] == 300.0
    assert subs["labels"]["endpoint"] == "subsz"


def test_connz_num_connections_record():
    t = RoutingTransport(full_routes())
    src = NatsSource({"base_url": GOOD_URL}, http_get=t)
    records = src.query({"endpoints": ["connz"]})
    num = next(
        r for r in records if r["labels"].get("metric") == "num_connections"
    )
    assert num["value"] == 42.0
    assert num["labels"]["endpoint"] == "connz"


def test_query_all_endpoints_hits_three_paths():
    t = RoutingTransport(full_routes())
    src = NatsSource({"base_url": GOOD_URL}, http_get=t)
    src.query({})
    paths = [c["url"] for c in t.calls]
    assert any("/varz" in p for p in paths)
    assert any("/connz" in p for p in paths)
    assert any("/subsz" in p for p in paths)


# ---------------------------------------------------------------------------
# Error handling — query never raises, resilient per-endpoint
# ---------------------------------------------------------------------------


def test_query_bad_status_one_endpoint_still_returns_others():
    routes = full_routes()
    routes["/connz"] = (500, {"error": "boom"})
    t = RoutingTransport(routes)
    src = NatsSource({"base_url": GOOD_URL}, http_get=t)
    records = src.query({})
    assert any(r["level_or_status"] == "error" for r in records)
    assert any(r["labels"].get("metric") == "connections" for r in records)


def test_query_transport_exception_yields_error_record():
    class Boom:
        def __call__(self, url, params=None, headers=None, timeout=None):
            raise RuntimeError("connection refused")

    src = NatsSource({"base_url": GOOD_URL}, http_get=Boom())
    records = src.query({"endpoints": ["varz"]})
    assert len(records) == 1
    assert records[0]["level_or_status"] == "error"
    assert "connection refused" in records[0]["message"]


# ---------------------------------------------------------------------------
# health()
# ---------------------------------------------------------------------------


def test_health_ok():
    t = RoutingTransport(full_routes())
    src = NatsSource({"base_url": GOOD_URL}, http_get=t)
    h = src.health()
    assert h["ok"] is True
    assert "detail" in h


def test_health_not_ok_on_bad_status():
    t = RoutingTransport({"/varz": (503, {"error": "down"})})
    src = NatsSource({"base_url": GOOD_URL}, http_get=t)
    h = src.health()
    assert h["ok"] is False
    assert "detail" in h


def test_health_never_raises_on_transport_exception():
    class Boom:
        def __call__(self, url, params=None, headers=None, timeout=None):
            raise RuntimeError("dns/socket failure")

    src = NatsSource({"base_url": GOOD_URL}, http_get=Boom())
    h = src.health()
    assert h["ok"] is False
    assert "dns/socket failure" in h["detail"]
