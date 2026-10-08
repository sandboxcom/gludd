"""Unit tests for NomadSource (canned-transport payloads + SSRF policy)."""

from __future__ import annotations

import json
import socket
from typing import Any

import pytest

from general_ludd.connectors.nomad import (
    NomadSource,
    NomadTransportError,
)


class FakeResponse:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text


class FakeTransport:
    """Records calls and returns canned responses keyed by URL path suffix."""

    def __init__(self, routes: dict[str, FakeResponse]) -> None:
        self._routes = routes
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        params: dict[str, str] | None,
    ) -> FakeResponse:
        self.calls.append({"method": method, "url": url, "headers": headers, "params": params})
        for suffix, resp in self._routes.items():
            if suffix in url:
                return resp
        return FakeResponse(404, "not found")


# A public-ish host so the default SSRF guard does not block canned tests.
# 'example.com' resolves to public addresses; we use allow_private only where
# we are explicitly exercising private hosts.
PUBLIC_BASE = "http://example.com:4646"


def test_kind_and_name() -> None:
    src = NomadSource({"base_url": PUBLIC_BASE, "name": "nm", "allow_private": True})
    assert NomadSource.KIND == "logs"
    assert src.name == "nm"


def test_requires_base_url() -> None:
    with pytest.raises(ValueError):
        NomadSource({})


def test_rejects_bad_scheme() -> None:
    with pytest.raises(ValueError):
        NomadSource({"base_url": "ftp://example.com:4646"})


# -- SSRF -------------------------------------------------------------------
def test_ssrf_blocks_loopback_by_default() -> None:
    # Lazy SSRF contract: construction succeeds; the guard fires at
    # request time (health() reports ssrf-blocked, transport never called).
    transport = FakeTransport({})
    src = NomadSource({"base_url": "http://127.0.0.1:4646", "transport": transport})
    h = src.health()
    assert h["ok"] is False
    assert "ssrf" in h["detail"]
    # The transport must never have been called.
    assert transport.calls == []


def test_ssrf_blocks_private_literal_by_default() -> None:
    src = NomadSource({"base_url": "http://10.0.0.5:4646", "transport": FakeTransport({})})
    h = src.health()
    assert h["ok"] is False
    assert "ssrf" in h["detail"]


def test_allow_private_opt_in_permits_internal_host() -> None:
    transport = FakeTransport({"/v1/metrics": FakeResponse(200, "nomad_uptime 12.0\n")})
    src = NomadSource(
        {
            "base_url": "http://127.0.0.1:4646",
            "transport": transport,
            "allow_private": True,
        }
    )
    recs = src.query({"type": "metrics"})
    assert len(recs) == 1
    assert recs[0]["message"] == "nomad_uptime"
    assert transport.calls  # transport WAS called this time


def test_health_reports_ssrf_block_after_dns_rebind(monkeypatch: pytest.MonkeyPatch) -> None:
    from general_ludd.connectors import nomad as nomad_mod

    transport = FakeTransport({})
    src = NomadSource({"base_url": PUBLIC_BASE, "transport": transport, "allow_private": True})
    src._allow_private = False
    monkeypatch.setattr(nomad_mod, "_host_is_private", lambda _host: True)
    h = src.health()
    assert h["ok"] is False
    assert "ssrf" in h["detail"]
    assert transport.calls == []


def test_health_ok_with_canned_transport() -> None:
    transport = FakeTransport({"/v1/agent/health": FakeResponse(200, "{}")})
    src = NomadSource({"base_url": "http://127.0.0.1:4646", "transport": transport, "allow_private": True})
    h = src.health()
    assert h["ok"] is True


def test_health_never_raises_on_transport_error() -> None:
    def boom(method: str, url: str, headers: dict[str, str], params: Any) -> Any:
        raise RuntimeError("connection refused")

    src = NomadSource({"base_url": "http://127.0.0.1:4646", "transport": boom, "allow_private": True})
    h = src.health()
    assert h["ok"] is False


# -- alloc logs -------------------------------------------------------------
def test_query_alloc_logs_normalized() -> None:
    transport = FakeTransport({"/v1/client/fs/logs/": FakeResponse(200, "line one\nline two\n")})
    src = NomadSource({"base_url": PUBLIC_BASE, "transport": transport, "allow_private": True})
    recs = src.query({"type": "logs", "alloc_id": "abc123", "log_type": "stderr"})
    assert len(recs) == 2
    r = recs[0]
    assert r["kind"] == "logs"
    assert r["level_or_status"] == "error"  # stderr -> error
    assert r["labels"]["alloc_id"] == "abc123"
    assert r["labels"]["log_type"] == "stderr"
    assert r["message"] == "line one"
    # alloc id present in URL
    assert any("abc123" in c["url"] for c in transport.calls)


def test_logs_require_alloc_id() -> None:
    src = NomadSource({"base_url": PUBLIC_BASE, "transport": FakeTransport({}), "allow_private": True})
    with pytest.raises(ValueError):
        src.query({"type": "logs"})


def test_non_2xx_raises_transport_error() -> None:
    transport = FakeTransport({"/v1/client/fs/logs/": FakeResponse(500, "boom")})
    src = NomadSource({"base_url": PUBLIC_BASE, "transport": transport, "allow_private": True})
    with pytest.raises(NomadTransportError):
        src.query({"type": "logs", "alloc_id": "x"})


# -- event stream -----------------------------------------------------------
def test_query_events_parses_frames() -> None:
    frame = json.dumps(
        {
            "Index": 7,
            "Events": [
                {"Topic": "Allocation", "Type": "AllocationUpdated", "Key": "k1"},
                {"Topic": "Job", "Type": "JobRegistered", "Namespace": "default"},
            ],
        }
    )
    transport = FakeTransport({"/v1/event/stream": FakeResponse(200, frame + "\n")})
    src = NomadSource({"base_url": PUBLIC_BASE, "transport": transport, "allow_private": True})
    recs = src.query({"type": "events"})
    assert len(recs) == 2
    assert recs[0]["kind"] == "pipeline"
    assert recs[0]["labels"]["topic"] == "Allocation"
    assert recs[0]["level_or_status"] == "AllocationUpdated"
    assert recs[0]["labels"]["key"] == "k1"
    assert recs[1]["labels"]["namespace"] == "default"


def test_query_events_tolerates_sse_data_framing() -> None:
    frame = "data: " + json.dumps({"Events": [{"Topic": "Node", "Type": "NodeRegistration"}]})
    transport = FakeTransport({"/v1/event/stream": FakeResponse(200, frame + "\n")})
    src = NomadSource({"base_url": PUBLIC_BASE, "transport": transport, "allow_private": True})
    recs = src.query({"type": "events"})
    assert len(recs) == 1
    assert recs[0]["message"] == "Node:NodeRegistration"


def test_events_skip_heartbeat_and_garbage() -> None:
    body = "\n".join([": heartbeat", "not json", json.dumps({"Events": [{"Topic": "X", "Type": "Y"}]})])
    transport = FakeTransport({"/v1/event/stream": FakeResponse(200, body + "\n")})
    src = NomadSource({"base_url": PUBLIC_BASE, "transport": transport, "allow_private": True})
    recs = src.query({"type": "events"})
    assert len(recs) == 1


# -- metrics ----------------------------------------------------------------
def test_query_metrics_prometheus() -> None:
    text = (
        "# HELP nomad_uptime uptime\n"
        "# TYPE nomad_uptime gauge\n"
        'nomad_runtime_alloc_bytes{host="web1"} 1048576\n'
        "nomad_uptime 99.5\n"
    )
    transport = FakeTransport({"/v1/metrics": FakeResponse(200, text)})
    src = NomadSource({"base_url": PUBLIC_BASE, "transport": transport, "allow_private": True})
    recs = src.query({"type": "metrics"})
    assert len(recs) == 2
    by_name = {r["message"]: r for r in recs}
    assert by_name["nomad_runtime_alloc_bytes"]["value"] == 1048576.0
    assert by_name["nomad_runtime_alloc_bytes"]["labels"]["host"] == "web1"
    assert by_name["nomad_uptime"]["value"] == pytest.approx(99.5)
    assert by_name["nomad_uptime"]["kind"] == "metrics"
    # format=prometheus param sent
    assert any(c["params"] == {"format": "prometheus"} for c in transport.calls)


def test_unknown_query_type_raises() -> None:
    src = NomadSource({"base_url": PUBLIC_BASE, "transport": FakeTransport({}), "allow_private": True})
    with pytest.raises(ValueError):
        src.query({"type": "bogus"})


def test_token_env_sent_as_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOMAD_TOKEN_TEST", "secret-token-value")
    transport = FakeTransport({"/v1/metrics": FakeResponse(200, "m 1\n")})
    src = NomadSource(
        {
            "base_url": PUBLIC_BASE,
            "transport": transport,
            "allow_private": True,
            "token_env": "NOMAD_TOKEN_TEST",
        }
    )
    src.query({"type": "metrics"})
    assert transport.calls[0]["headers"].get("X-Nomad-Token") == "secret-token-value"


def test_normalized_keys_across_all_query_types() -> None:
    required = {"ts", "source", "kind", "level_or_status", "message", "value", "labels", "raw"}
    transport = FakeTransport(
        {
            "/v1/client/fs/logs/": FakeResponse(200, "l\n"),
            "/v1/event/stream": FakeResponse(200, json.dumps({"Events": [{"Topic": "T", "Type": "E"}]}) + "\n"),
            "/v1/metrics": FakeResponse(200, "m 1\n"),
        }
    )
    src = NomadSource({"base_url": PUBLIC_BASE, "transport": transport, "allow_private": True})
    for spec in (
        {"type": "logs", "alloc_id": "a"},
        {"type": "events"},
        {"type": "metrics"},
    ):
        for rec in src.query(spec):
            assert required <= set(rec)


# -- SSRF: CGNAT / resolved-metadata / fail-closed (resolved_host_is_blocked) --
def test_ssrf_blocks_cgnat_literal_without_allow_private() -> None:
    # 100.70.1.1 sits in the 100.64.0.0/10 carrier-grade-NAT range: is_private
    # is False for it in Python's ipaddress module, so the OLD local
    # _host_is_private (missing the `not is_global` flag) would NOT have
    # blocked it. resolved_host_is_blocked (via the canonical _ip_addr_is_blocked
    # flag set) closes this gap. Lazy contract: construction succeeds, the
    # guard fires at request time.
    src = NomadSource({"base_url": "http://100.70.1.1:4646", "transport": FakeTransport({})})
    h = src.health()
    assert h["ok"] is False
    assert "ssrf" in h["detail"]


def test_ssrf_blocks_hostname_resolving_to_cgnat_address(monkeypatch: pytest.MonkeyPatch) -> None:
    from general_ludd.security import ssrf as ssrf_mod

    def _fake_getaddrinfo(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("100.70.1.1", 0))]

    monkeypatch.setattr(ssrf_mod.socket, "getaddrinfo", _fake_getaddrinfo)

    transport = FakeTransport({})
    src = NomadSource({"base_url": "http://internal-nomad.example.com:4646", "transport": transport})
    h = src.health()
    assert h["ok"] is False
    assert "ssrf" in h["detail"]
    assert transport.calls == []


def test_ssrf_blocks_unresolvable_hostname_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from general_ludd.security import ssrf as ssrf_mod

    def _boom(*_args, **_kwargs):
        raise OSError("name or service not known")

    monkeypatch.setattr(ssrf_mod.socket, "getaddrinfo", _boom)

    transport = FakeTransport({})
    src = NomadSource({"base_url": "http://does-not-resolve.example.com:4646", "transport": transport})
    h = src.health()
    assert h["ok"] is False
    assert "ssrf" in h["detail"]
    assert transport.calls == []


# -- resolved_host_is_blocked (canonical DNS-resolving opt-in helper) --------
def test_resolved_host_is_blocked_private_resolved_address(monkeypatch: pytest.MonkeyPatch) -> None:
    from general_ludd.security import ssrf as ssrf_mod
    from general_ludd.security.ssrf import resolved_host_is_blocked

    def _private(*_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))]

    monkeypatch.setattr(ssrf_mod.socket, "getaddrinfo", _private)
    assert resolved_host_is_blocked("internal.example.com") is True


def test_resolved_host_is_blocked_public_resolved_address(monkeypatch: pytest.MonkeyPatch) -> None:
    from general_ludd.security import ssrf as ssrf_mod
    from general_ludd.security.ssrf import resolved_host_is_blocked

    def _public(*_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(ssrf_mod.socket, "getaddrinfo", _public)
    assert resolved_host_is_blocked("public.example.com") is False


def test_resolved_host_is_blocked_fails_closed_on_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    from general_ludd.security import ssrf as ssrf_mod
    from general_ludd.security.ssrf import resolved_host_is_blocked

    def _boom(*_args, **_kwargs):
        raise OSError("nope")

    monkeypatch.setattr(ssrf_mod.socket, "getaddrinfo", _boom)
    assert resolved_host_is_blocked("does-not-resolve.example.com") is True


def test_resolved_host_is_blocked_fails_closed_on_empty_result(monkeypatch: pytest.MonkeyPatch) -> None:
    from general_ludd.security import ssrf as ssrf_mod
    from general_ludd.security.ssrf import resolved_host_is_blocked

    monkeypatch.setattr(ssrf_mod.socket, "getaddrinfo", lambda *a, **k: [])
    assert resolved_host_is_blocked("empty-result.example.com") is True


def test_resolved_host_is_blocked_literal_short_circuits_without_dns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from general_ludd.security import ssrf as ssrf_mod
    from general_ludd.security.ssrf import resolved_host_is_blocked

    def _boom(*_args, **_kwargs):  # pragma: no cover - must never be called
        raise AssertionError("getaddrinfo must not be called for a blocked literal host")

    monkeypatch.setattr(ssrf_mod.socket, "getaddrinfo", _boom)
    assert resolved_host_is_blocked("metadata") is True
