"""Unit tests for the Cilium Hubble connector.

All transport / flow-runner access is mocked: no real network, no real Hubble.
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.cilium_hubble import CiliumHubbleSource, SSRFError

# -- transport doubles ------------------------------------------------------


def make_transport(
    status: int, body: str, recorder: list[str] | None = None
):
    def _transport(url: str, timeout: float) -> tuple[int, str]:
        if recorder is not None:
            recorder.append(url)
        assert timeout > 0
        return status, body

    return _transport


HUBBLE_METRICS = """
# HELP hubble_flows_processed_total Total flows processed
# TYPE hubble_flows_processed_total counter
hubble_flows_processed_total{verdict="FORWARDED",protocol="TCP"} 42
hubble_flows_processed_total{verdict="DROPPED",protocol="UDP"} 7
hubble_drop_total 3
""".strip()


SAMPLE_FLOWS: list[dict[str, Any]] = [
    {
        "verdict": "DROPPED",
        "time": "2026-06-16T12:00:00Z",
        "node_name": "node-a",
        "source": {"namespace": "default", "pod_name": "client-1"},
        "destination": {"namespace": "kube-system", "pod_name": "coredns-x"},
        "l4": {"UDP": {"source_port": 5353, "destination_port": 53}},
        "l7": {"type": "DNS", "dns": {"query": "evil.example.com"}},
    },
    {
        "verdict": "FORWARDED",
        "time": 1781600000.0,
        "source": {"namespace": "shop", "pod_name": "web-7"},
        "destination": {"namespace": "shop", "pod_name": "api-3"},
        "l4": {"TCP": {"destination_port": 8080}},
        "l7": {
            "type": "HTTP",
            "http": {"method": "GET", "url": "http://api/items"},
        },
    },
]


# -- metrics normalization --------------------------------------------------


def test_metrics_query_normalizes_records():
    src = CiliumHubbleSource(
        {"name": "hub", "base_url": "http://hubble.svc:9965", "allow_private": True,
         "scrape_flows": False},
        transport=make_transport(200, HUBBLE_METRICS),
    )
    records = src.query()
    assert records, "expected metrics records"
    rec = records[0]
    # Contract keys present.
    for key in ("ts", "source", "kind", "level_or_status", "message",
                "value", "labels", "raw"):
        assert key in rec
    assert rec["kind"] == "events"
    assert rec["source"] == "hub"
    # The forwarded counter sample is parsed with labels and float value.
    fwd = [r for r in records if r["labels"].get("verdict") == "FORWARDED"]
    assert fwd and fwd[0]["value"] == 42.0
    # A bare metric (no labels) is still parsed.
    bare = [r for r in records if r["message"] == "hubble_drop_total"]
    assert bare and bare[0]["value"] == 3.0


def test_metrics_query_returns_empty_on_non_2xx():
    src = CiliumHubbleSource(
        {"base_url": "http://hubble.svc:9965", "allow_private": True,
         "scrape_flows": False},
        transport=make_transport(503, "unavailable"),
    )
    assert src.query() == []


# -- flow normalization -----------------------------------------------------


def test_flow_query_normalizes_verdict_and_l7():
    src = CiliumHubbleSource(
        {"name": "hub", "scrape_metrics": False},
        flow_runner=lambda spec: SAMPLE_FLOWS,
    )
    records = src.query({})
    assert len(records) == 2

    dropped = records[0]
    assert dropped["level_or_status"] == "warning"
    assert dropped["labels"]["verdict"] == "DROPPED"
    assert dropped["labels"]["protocol"] == "UDP"
    assert dropped["labels"]["source"] == "default/client-1"
    assert dropped["labels"]["destination"] == "kube-system/coredns-x"
    assert "dns" in dropped["message"]
    assert "evil.example.com" in dropped["message"]
    # RFC3339 timestamp coerced to epoch float.
    assert isinstance(dropped["ts"], float) and dropped["ts"] > 0

    forwarded = records[1]
    assert forwarded["level_or_status"] == "info"
    assert forwarded["labels"]["protocol"] == "TCP"
    assert "http GET" in forwarded["message"]
    assert forwarded["ts"] == 1781600000.0


def test_metrics_and_flows_combined():
    src = CiliumHubbleSource(
        {"base_url": "http://hubble.svc:9965", "allow_private": True},
        transport=make_transport(200, HUBBLE_METRICS),
        flow_runner=lambda spec: SAMPLE_FLOWS,
    )
    records = src.query()
    kinds_from_flows = [r for r in records if r["raw"].__class__ is dict
                        and isinstance(r["raw"], dict) and "verdict" in r["raw"]]
    assert kinds_from_flows  # flow records present
    assert any(isinstance(r["raw"], str) for r in records)  # metric lines present


# -- SSRF / internal-host handling ------------------------------------------


def test_ssrf_blocks_private_target_by_default():
    src = CiliumHubbleSource(
        {"base_url": "http://hubble.svc:9965"},
        transport=make_transport(200, HUBBLE_METRICS),
        resolver=lambda host: ["10.0.0.5"],
    )
    with pytest.raises(SSRFError):
        src.query()


def test_ssrf_blocks_loopback_literal():
    src = CiliumHubbleSource(
        {"base_url": "http://127.0.0.1:9965"},
        transport=make_transport(200, HUBBLE_METRICS),
    )
    with pytest.raises(SSRFError):
        src.query()


@pytest.mark.parametrize(
    "bad_base",
    [
        "http://localhost",
        "http://foo.localhost",
        "http://metadata.google.internal",
        "http://metadata",
        "http://169.254.169.254",
        # Alibaba cloud-metadata endpoint — coverage gained by delegating to the
        # canonical is_url_blocked (the old bespoke _is_blocked_ip missed it).
        "http://100.100.100.200",
    ],
)
def test_ssrf_blocks_metadata_and_loopback_names_without_dns(bad_base: str) -> None:
    # A resolver that would WRONGLY allow the target proves the literal/name
    # decision is made by is_url_blocked BEFORE (and independently of) any DNS
    # resolution.
    def _boom_resolver(host: str) -> list[str]:
        raise AssertionError("resolver must not be consulted for a blocked name")

    src = CiliumHubbleSource(
        {"base_url": f"{bad_base}:9965", "scrape_flows": False},
        transport=make_transport(200, HUBBLE_METRICS),
        resolver=_boom_resolver,
    )
    with pytest.raises(SSRFError):
        src.query()


def test_ssrf_blocks_cgnat_literal_via_url_blocked():
    # 100.70.1.1 sits in the 100.64.0.0/10 carrier-grade-NAT range: is_private
    # is False for it in Python's ipaddress module. Regression pin for the
    # literal is_url_blocked path used by _guard_url (the canonical
    # _ip_addr_is_blocked flag set catches it via `not is_global`).
    src = CiliumHubbleSource(
        {"base_url": "http://100.70.1.1:9965"},
        transport=make_transport(200, HUBBLE_METRICS),
    )
    with pytest.raises(SSRFError):
        src.query()


def test_ssrf_blocks_resolved_cgnat_address():
    # The injected resolver returns a 100.64.0.0/10 CGNAT address for a
    # non-literal hostname. The OLD local _is_blocked_ip classifier (missing
    # the `not is_global` flag) would have let this through since is_private
    # is False for that range; importing the canonical _ip_addr_is_blocked
    # closes that gap.
    src = CiliumHubbleSource(
        {"base_url": "http://hubble.svc:9965"},
        transport=make_transport(200, HUBBLE_METRICS),
        resolver=lambda host: ["100.70.1.1"],
    )
    with pytest.raises(SSRFError):
        src.query()


def test_allow_private_permits_in_cluster():
    recorder: list[str] = []
    src = CiliumHubbleSource(
        {"base_url": "http://10.0.0.5:9965", "allow_private": True,
         "scrape_flows": False},
        transport=make_transport(200, HUBBLE_METRICS, recorder),
    )
    records = src.query()
    assert records
    assert recorder == ["http://10.0.0.5:9965/metrics"]


def test_ssrf_rejects_non_http_scheme():
    src = CiliumHubbleSource(
        {"base_url": "file:///etc/passwd"},
        transport=make_transport(200, ""),
    )
    with pytest.raises(SSRFError):
        src.query()


# -- health -----------------------------------------------------------------


def test_health_ok_on_2xx_metrics():
    src = CiliumHubbleSource(
        {"base_url": "http://hubble:9965", "allow_private": True},
        transport=make_transport(200, HUBBLE_METRICS),
    )
    h = src.health()
    assert h["ok"] is True
    assert "ok" in h and "detail" in h


def test_health_never_raises_on_transport_error():
    def boom(url: str, timeout: float):
        raise ConnectionError("refused")

    src = CiliumHubbleSource(
        {"base_url": "http://hubble:9965", "allow_private": True},
        transport=boom,
    )
    h = src.health()
    assert h["ok"] is False
    assert "fail" in h["detail"].lower() or "refused" in h["detail"].lower()


def test_health_reports_ssrf_block_without_raising():
    src = CiliumHubbleSource(
        {"base_url": "http://127.0.0.1:9965"},
        transport=make_transport(200, ""),
    )
    h = src.health()
    assert h["ok"] is False
    assert "ssrf" in h["detail"].lower()


def test_health_flow_only_mode_ok():
    src = CiliumHubbleSource(
        {"scrape_metrics": False},
        flow_runner=lambda spec: [],
    )
    h = src.health()
    assert h["ok"] is True


def test_health_unconfigured_is_not_ok():
    src = CiliumHubbleSource({})
    h = src.health()
    assert h["ok"] is False


# -- secret handling --------------------------------------------------------


def test_token_resolved_from_env_only(monkeypatch):
    monkeypatch.setenv("HUBBLE_TOKEN", "s3cr3t-token")
    src = CiliumHubbleSource(
        {"base_url": "http://hub:9965", "token_env": "HUBBLE_TOKEN",
         "allow_private": True},
        transport=make_transport(200, HUBBLE_METRICS),
    )
    assert src._token == "s3cr3t-token"
    # Token must not appear inside any normalized record.
    records = src.query()
    blob = repr(records)
    assert "s3cr3t-token" not in blob


def test_kind_class_attribute():
    assert CiliumHubbleSource.KIND == "events"
    assert CiliumHubbleSource({}).kind == "events"
