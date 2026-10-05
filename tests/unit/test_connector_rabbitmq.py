"""Unit tests for the RabbitMQ management-API observability connector.

Self-contained: imports only the connector module under test and uses an
injectable, fully-mocked HTTP transport (no real network, no DNS, no shell).
"""

from __future__ import annotations

import base64
from typing import Any

import pytest

from general_ludd.connectors.rabbitmq import RabbitMqSource

# ---------------------------------------------------------------------------
# Mock transport — routes by URL path so query() can hit 3 endpoints.
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


OVERVIEW = {
    "rabbitmq_version": "3.13.0",
    "queue_totals": {"messages": 150, "messages_unacknowledged": 7},
    "object_totals": {"connections": 12, "queues": 3},
}

QUEUES = [
    {
        "name": "orders",
        "vhost": "/",
        "messages": 150,
        "messages_ready": 143,
        "messages_unacknowledged": 7,
        "memory": 1048576,
    },
    {
        "name": "dead-letter",
        "vhost": "billing",
        "messages": 0,
        "messages_ready": 0,
        "messages_unacknowledged": 0,
        "memory": 4096,
    },
]

NODES = [
    {
        "name": "rabbit@node-1",
        "mem_used": 268435456,
        "fd_used": 120,
        "sockets_used": 30,
        "running": True,
    }
]

GOOD_URL = "https://rabbit.internal.example.com"


def full_routes() -> dict[str, tuple[int, Any]]:
    return {
        "/api/overview": (200, OVERVIEW),
        "/api/queues": (200, QUEUES),
        "/api/nodes": (200, NODES),
    }


# ---------------------------------------------------------------------------
# Construction / contract
# ---------------------------------------------------------------------------


def test_kind_name_and_construction():
    src = RabbitMqSource({"base_url": GOOD_URL}, http_get=RoutingTransport({}))
    assert RabbitMqSource.KIND == "metrics"
    assert src.kind == "metrics"
    assert isinstance(src.name, str) and src.name


def test_constructs_with_default_transport():
    # No transport injected: the connector falls back to a real stdlib transport
    # so the registry's single-arg ``factory(config)`` build succeeds (the source
    # is NOT silently dropped from ``list_sources()``). No network at construction.
    from general_ludd.connectors.rabbitmq import _default_http_get

    src = RabbitMqSource({"base_url": GOOD_URL})
    assert src._http_get is _default_http_get
    assert src.name


def test_default_management_port_appended():
    t = RoutingTransport(full_routes())
    src = RabbitMqSource({"base_url": GOOD_URL}, http_get=t)
    src.query({"endpoints": ["overview"]})
    assert ":15672/api/overview" in t.calls[0]["url"]


def test_explicit_port_preserved():
    t = RoutingTransport(full_routes())
    src = RabbitMqSource({"base_url": f"{GOOD_URL}:9999"}, http_get=t)
    src.query({"endpoints": ["overview"]})
    assert ":9999/api/overview" in t.calls[0]["url"]


def test_query_is_time_bound():
    t = RoutingTransport(full_routes())
    src = RabbitMqSource({"base_url": GOOD_URL}, http_get=t, timeout=2.5)
    src.query({"endpoints": ["overview"]})
    assert t.calls[0]["timeout"] == 2.5


# ---------------------------------------------------------------------------
# Basic auth from env
# ---------------------------------------------------------------------------


def test_basic_auth_header_from_env(monkeypatch):
    monkeypatch.setenv("RABBIT_USER", "guest")
    monkeypatch.setenv("RABBIT_PASS", "s3cret")
    t = RoutingTransport(full_routes())
    src = RabbitMqSource(
        {
            "base_url": GOOD_URL,
            "user_env": "RABBIT_USER",
            "password_env": "RABBIT_PASS",
        },
        http_get=t,
    )
    src.query({"endpoints": ["overview"]})
    auth = t.calls[0]["headers"].get("Authorization", "")
    assert auth.startswith("Basic ")
    decoded = base64.b64decode(auth.split(" ", 1)[1]).decode()
    assert decoded == "guest:s3cret"


def test_missing_credentials_no_auth_header(monkeypatch):
    monkeypatch.delenv("RABBIT_USER", raising=False)
    monkeypatch.delenv("RABBIT_PASS", raising=False)
    t = RoutingTransport(full_routes())
    src = RabbitMqSource(
        {
            "base_url": GOOD_URL,
            "user_env": "RABBIT_USER",
            "password_env": "RABBIT_PASS",
        },
        http_get=t,
    )
    src.query({"endpoints": ["overview"]})
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
        RabbitMqSource({"base_url": bad_url}, http_get=RoutingTransport({}))


def test_ssrf_allows_http_for_allowlisted_host():
    src = RabbitMqSource(
        {"base_url": "http://rabbit.example.com"}, http_get=RoutingTransport({})
    )
    assert src.name


def test_ssrf_rejects_non_http_scheme():
    with pytest.raises(ValueError):
        RabbitMqSource(
            {"base_url": "ftp://rabbit.example.com"}, http_get=RoutingTransport({})
        )


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
        RabbitMqSource({"base_url": bad_url}, http_get=RoutingTransport({}))


def test_public_base_url_constructs_after_consolidation():
    src = RabbitMqSource(
        {"base_url": "https://api.example.com"}, http_get=RoutingTransport({})
    )
    assert src.name


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def test_queue_depth_and_unacked_records():
    t = RoutingTransport(full_routes())
    src = RabbitMqSource({"base_url": GOOD_URL}, http_get=t)
    records = src.query({"endpoints": ["queues"]})

    depth = next(
        r
        for r in records
        if r["labels"].get("queue") == "orders"
        and r["labels"].get("metric") == "queue_depth"
    )
    assert depth["value"] == 150.0
    assert depth["kind"] == "metrics"
    assert depth["labels"]["vhost"] == "/"
    assert depth["level_or_status"] == ""

    unacked = next(
        r
        for r in records
        if r["labels"].get("queue") == "orders"
        and r["labels"].get("metric") == "queue_unacked"
    )
    assert unacked["value"] == 7.0


def test_queue_memory_record():
    t = RoutingTransport(full_routes())
    src = RabbitMqSource({"base_url": GOOD_URL}, http_get=t)
    records = src.query({"endpoints": ["queues"]})
    mem = next(
        r
        for r in records
        if r["labels"].get("queue") == "orders"
        and r["labels"].get("metric") == "queue_memory"
    )
    assert mem["value"] == 1048576.0


def test_node_memory_record():
    t = RoutingTransport(full_routes())
    src = RabbitMqSource({"base_url": GOOD_URL}, http_get=t)
    records = src.query({"endpoints": ["nodes"]})
    mem = next(
        r
        for r in records
        if r["labels"].get("node") == "rabbit@node-1"
        and r["labels"].get("metric") == "node_mem_used"
    )
    assert mem["value"] == 268435456.0
    running = next(
        r for r in records if r["labels"].get("metric") == "node_running"
    )
    assert running["value"] == 1.0


def test_overview_cluster_totals():
    t = RoutingTransport(full_routes())
    src = RabbitMqSource({"base_url": GOOD_URL}, http_get=t)
    records = src.query({"endpoints": ["overview"]})
    msgs = next(r for r in records if r["labels"].get("metric") == "cluster_messages")
    assert msgs["value"] == 150.0
    conns = next(
        r for r in records if r["labels"].get("metric") == "cluster_connections"
    )
    assert conns["value"] == 12.0


def test_normalizers_filter_malformed_payloads_and_false_node_state():
    t = RoutingTransport(
        {
            "/api/overview": (200, {"queue_totals": [], "object_totals": []}),
            "/api/queues": (200, [None, {"name": "empty", "messages": "bad"}]),
            "/api/nodes": (200, [None, {"name": "down", "running": False}]),
        }
    )
    src = RabbitMqSource({"base_url": GOOD_URL}, http_get=t)

    records = src.query({"endpoints": ["unknown", "overview", "queues", "nodes"]})

    assert not any(record["labels"].get("metric", "").startswith("cluster_") for record in records)
    running = next(record for record in records if record["labels"].get("metric") == "node_running")
    assert running["value"] == 0.0


@pytest.mark.parametrize("payload", (None, {}, "invalid"))
def test_query_replaces_non_collection_endpoint_selector(payload):
    t = RoutingTransport(full_routes())
    src = RabbitMqSource({"base_url": GOOD_URL}, http_get=t)

    records = src.query({"endpoints": payload})

    assert records
    assert len(t.calls) == 3


def test_normalizers_reject_non_collection_payloads():
    src = RabbitMqSource({"base_url": GOOD_URL}, http_get=RoutingTransport({}))

    assert src._normalize_queues({}, 1.0) == []
    assert src._normalize_nodes({}, 1.0) == []
    assert src._normalize_overview([], 1.0) == []


def test_query_all_endpoints_hits_three_paths():
    t = RoutingTransport(full_routes())
    src = RabbitMqSource({"base_url": GOOD_URL}, http_get=t)
    src.query({})
    paths = [c["url"] for c in t.calls]
    assert any("/api/overview" in p for p in paths)
    assert any("/api/queues" in p for p in paths)
    assert any("/api/nodes" in p for p in paths)


# ---------------------------------------------------------------------------
# Error handling — query never raises, resilient per-endpoint
# ---------------------------------------------------------------------------


def test_query_bad_status_one_endpoint_still_returns_others():
    routes = full_routes()
    routes["/api/queues"] = (500, {"error": "boom"})
    t = RoutingTransport(routes)
    src = RabbitMqSource({"base_url": GOOD_URL}, http_get=t)
    records = src.query({})
    # error record for queues, but overview/nodes data still present.
    assert any(r["level_or_status"] == "error" for r in records)
    assert any(r["labels"].get("metric") == "cluster_messages" for r in records)


def test_query_transport_exception_yields_error_record():
    class Boom:
        def __call__(self, url, params=None, headers=None, timeout=None):
            raise RuntimeError("connection refused")

    src = RabbitMqSource({"base_url": GOOD_URL}, http_get=Boom())
    records = src.query({"endpoints": ["overview"]})
    assert len(records) == 1
    assert records[0]["level_or_status"] == "error"
    assert "connection refused" in records[0]["message"]


# ---------------------------------------------------------------------------
# health()
# ---------------------------------------------------------------------------


def test_health_ok():
    t = RoutingTransport(full_routes())
    src = RabbitMqSource({"base_url": GOOD_URL}, http_get=t)
    h = src.health()
    assert h["ok"] is True
    assert "detail" in h


def test_health_not_ok_on_bad_status():
    t = RoutingTransport({"/api/overview": (401, {"error": "not_authorised"})})
    src = RabbitMqSource({"base_url": GOOD_URL}, http_get=t)
    h = src.health()
    assert h["ok"] is False
    assert "detail" in h


def test_health_never_raises_on_transport_exception():
    class Boom:
        def __call__(self, url, params=None, headers=None, timeout=None):
            raise RuntimeError("dns/socket failure")

    src = RabbitMqSource({"base_url": GOOD_URL}, http_get=Boom())
    h = src.health()
    assert h["ok"] is False
    assert "dns/socket failure" in h["detail"]
