"""Offline tests for the SSRF-hardened fetch client.

Transport is MOCKED via ``httpx.MockTransport`` and DNS is MOCKED via an autouse
``_public_dns`` fixture that monkeypatches ``web.ssrf_client.socket.getaddrinfo``
to a fixed PUBLIC IP — mirroring tests/unit/test_connector_grafana_oncall.py. No
real network is ever touched. Failure modes exercised: DNS-to-internal block,
redirect-to-internal block, redirect cap, offline -> structured OFFLINE, 429
retry-then-TIMEOUT, breaker trip -> CIRCUIT_OPEN, captcha-detect, peer pin.
"""

from __future__ import annotations

import socket

import httpx
import pytest

from general_ludd.web import ssrf_client as sc
from general_ludd.web.breaker import HostCircuitBreaker
from general_ludd.web.results import WebError
from general_ludd.web.ssrf_client import SsrfSafeClient

_PUBLIC_IP = "93.184.216.34"
_URL = "https://example.com/page"


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    """Resolve every host to a stable PUBLIC IP so the SSRF DNS-recheck is deterministic."""

    def _fake(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_PUBLIC_IP, 443))]

    monkeypatch.setattr(sc.socket, "getaddrinfo", _fake)


def _client(handler, **kw) -> SsrfSafeClient:
    return SsrfSafeClient(transport=httpx.MockTransport(handler), max_attempts=kw.pop("max_attempts", 2), **kw)


# -- happy path ---------------------------------------------------------------

def test_fetch_ok_returns_structured_result():
    def handler(req):
        return httpx.Response(200, text="<html><title>Hi</title></html>",
                              headers={"content-type": "text/html"})

    res = _client(handler).fetch(_URL)
    assert res.ok is True
    assert res.status == 200
    assert "Hi" in res.body
    assert res.resolved_ip == _PUBLIC_IP
    assert res.error is None


# -- (a) string gate: non-https rejected --------------------------------------

def test_http_scheme_rejected_string_gate():
    res = _client(lambda r: httpx.Response(200)).fetch("http://example.com/x")
    assert res.ok is False
    assert res.error == WebError.SSRF_BLOCKED


def test_literal_internal_host_rejected():
    res = _client(lambda r: httpx.Response(200)).fetch("https://127.0.0.1/x")
    assert res.ok is False
    assert res.error == WebError.SSRF_BLOCKED


def test_metadata_literal_rejected():
    res = _client(lambda r: httpx.Response(200)).fetch("https://169.254.169.254/latest")
    assert res.ok is False
    assert res.error == WebError.SSRF_BLOCKED


# -- (b) DNS-to-internal: name resolves to a private IP -> blocked ------------

def test_dns_resolving_to_internal_blocked(monkeypatch):
    def _internal(host, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443))]

    monkeypatch.setattr(sc.socket, "getaddrinfo", _internal)
    res = _client(lambda r: httpx.Response(200)).fetch(_URL)
    assert res.ok is False
    assert res.error == WebError.SSRF_BLOCKED


def test_dns_resolving_to_metadata_blocked(monkeypatch):
    def _meta(host, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443))]

    monkeypatch.setattr(sc.socket, "getaddrinfo", _meta)
    res = _client(lambda r: httpx.Response(200)).fetch(_URL)
    assert res.ok is False
    assert res.error == WebError.SSRF_BLOCKED


def test_dns_rebind_second_call_internal_blocked(monkeypatch):
    """getaddrinfo public on the first (initial-url) call, private on a later one."""
    calls = {"n": 0}

    def _rebind(host, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_PUBLIC_IP, 443))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.10", 443))]

    monkeypatch.setattr(sc.socket, "getaddrinfo", _rebind)

    # Redirect to a second host so a SECOND resolve happens on the hop.
    def handler(req):
        if req.url.path == "/page":
            return httpx.Response(302, headers={"location": "https://internal.example.net/x"})
        return httpx.Response(200, text="ok")

    res = _client(handler).fetch(_URL)
    assert res.ok is False
    assert res.error == WebError.SSRF_BLOCKED


# -- (d/e) redirect handling --------------------------------------------------

def test_redirect_to_internal_blocked_on_hop(monkeypatch):
    def _resolve(host, *a, **k):
        if host == "example.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_PUBLIC_IP, 443))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]

    monkeypatch.setattr(sc.socket, "getaddrinfo", _resolve)

    def handler(req):
        return httpx.Response(302, headers={"location": "https://localhost-evil.test/x"})

    res = _client(handler).fetch(_URL)
    assert res.ok is False
    assert res.error == WebError.SSRF_BLOCKED


def test_redirect_to_internal_literal_blocked():
    def handler(req):
        return httpx.Response(302, headers={"location": "https://10.0.0.9/x"})

    res = _client(handler).fetch(_URL)
    assert res.ok is False
    assert res.error == WebError.SSRF_BLOCKED


def test_redirect_cap_enforced():
    def handler(req):
        # Always redirect to a public sibling -> loop until cap.
        n = req.url.path.count("a")
        return httpx.Response(302, headers={"location": f"https://example.com/a{'a' * n}"})

    res = SsrfSafeClient(transport=httpx.MockTransport(handler), max_redirects=3,
                         max_attempts=1).fetch(_URL)
    assert res.ok is False
    assert res.error == WebError.REDIRECT_LIMIT


def test_redirect_followed_to_public_ok():
    def handler(req):
        if req.url.path == "/page":
            return httpx.Response(301, headers={"location": "https://example.com/final"})
        return httpx.Response(200, text="landed", headers={"content-type": "text/html"})

    res = _client(handler).fetch(_URL)
    assert res.ok is True
    assert "landed" in res.body
    assert res.redirect_chain  # at least one hop recorded


# -- offline / timeout / 4xx / 5xx --------------------------------------------

def test_offline_connect_error_structured():
    def handler(req):
        raise httpx.ConnectError("no route", request=req)

    res = SsrfSafeClient(transport=httpx.MockTransport(handler), max_attempts=2).fetch(_URL)
    assert res.ok is False
    assert res.error == WebError.OFFLINE


def test_read_timeout_structured():
    def handler(req):
        raise httpx.ReadTimeout("slow", request=req)

    res = SsrfSafeClient(transport=httpx.MockTransport(handler), max_attempts=2).fetch(_URL)
    assert res.ok is False
    assert res.error == WebError.TIMEOUT


def test_nxdomain_offline(monkeypatch):
    def _boom(host, *a, **k):
        raise socket.gaierror("nxdomain")

    monkeypatch.setattr(sc.socket, "getaddrinfo", _boom)
    res = _client(lambda r: httpx.Response(200)).fetch(_URL)
    assert res.ok is False
    assert res.error == WebError.OFFLINE


def test_auth_4xx_not_retried():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(403, text="forbidden")

    res = SsrfSafeClient(transport=httpx.MockTransport(handler), max_attempts=4).fetch(_URL)
    assert res.ok is False
    # 403 maps to HTTP_4XX and must NOT be retried.
    assert res.error == WebError.HTTP_4XX
    assert calls["n"] == 1


def test_404_returns_http_4xx_single_attempt():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(404, text="missing")

    res = SsrfSafeClient(transport=httpx.MockTransport(handler), max_attempts=4).fetch(_URL)
    assert res.error == WebError.HTTP_4XX
    assert calls["n"] == 1


def test_429_retried_then_exhausted():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(429, text="slow down")

    res = SsrfSafeClient(transport=httpx.MockTransport(handler), max_attempts=2).fetch(_URL)
    assert res.ok is False
    # Retried (transient) then surfaced as TIMEOUT on exhaustion.
    assert calls["n"] >= 2
    assert res.error in (WebError.TIMEOUT, WebError.OFFLINE)


def test_500_retried():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(500, text="boom")

    SsrfSafeClient(transport=httpx.MockTransport(handler), max_attempts=2).fetch(_URL)
    assert calls["n"] >= 2


# -- circuit breaker ----------------------------------------------------------

def test_breaker_trips_to_circuit_open():
    breaker = HostCircuitBreaker(failure_threshold=2, cooldown_seconds=60)

    def handler(req):
        raise httpx.ConnectError("dead", request=req)

    cli = SsrfSafeClient(transport=httpx.MockTransport(handler), breaker=breaker, max_attempts=1)
    cli.fetch(_URL)
    cli.fetch(_URL)
    res = cli.fetch(_URL)
    assert res.ok is False
    assert res.error == WebError.CIRCUIT_OPEN


# -- captcha during fetch -----------------------------------------------------

def test_captcha_detected_attaches_signal_and_blocks():
    def handler(req):
        return httpx.Response(
            403,
            text="<html>Just a moment...</html>",
            headers={"content-type": "text/html"},
        )

    res = _client(handler).fetch(_URL)
    assert res.ok is False
    assert res.error == WebError.CAPTCHA_DETECTED
    assert res.captcha is not None
    assert res.captcha.detected is True
    # Partial body retained.
    assert "Just a moment" in res.body


def test_detail_scrubbed_single_line():
    def handler(req):
        raise httpx.ConnectError("line1\nSECRET=should-not-leak", request=req)

    res = SsrfSafeClient(transport=httpx.MockTransport(handler), max_attempts=1).fetch(_URL)
    assert "should-not-leak" not in res.detail


# -- helper unit tests --------------------------------------------------------

def test_screen_ip_predicate():
    assert sc._screen_ip("8.8.8.8") is True
    assert sc._screen_ip("10.0.0.1") is False
    assert sc._screen_ip("127.0.0.1") is False
    assert sc._screen_ip("169.254.169.254") is False
    assert sc._screen_ip("::1") is False
    assert sc._screen_ip("not-an-ip") is False


# -- (issue 2) IPv4-mapped IPv6 bypass of the metadata literals ----------------

def test_screen_ip_v4mapped_alibaba_metadata_blocked():
    """::ffff:100.100.100.200 must be BLOCKED (was a bypass: public-looking
    literal, only in the string set, and the mapped form misses the raw match)."""
    assert sc._screen_ip("100.100.100.200") is False
    assert sc._screen_ip("::ffff:100.100.100.200") is False
    # bracketed form (as it may arrive from a sockaddr) also blocked.
    assert sc._screen_ip("[::ffff:100.100.100.200]") is False


def test_screen_ip_v4mapped_aws_metadata_blocked():
    assert sc._screen_ip("::ffff:169.254.169.254") is False


def test_screen_ip_v4mapped_private_blocked():
    assert sc._screen_ip("::ffff:10.0.0.1") is False
    assert sc._screen_ip("::ffff:127.0.0.1") is False


def test_screen_ip_v4mapped_public_allowed():
    assert sc._screen_ip("::ffff:93.184.216.34") is True


def test_dns_resolving_to_v4mapped_metadata_blocked(monkeypatch):
    """A host resolving to the IPv4-mapped Alibaba metadata literal is blocked."""

    def _mapped(host, *a, **k):
        return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::ffff:100.100.100.200", 443, 0, 0))]

    monkeypatch.setattr(sc.socket, "getaddrinfo", _mapped)
    res = _client(lambda r: httpx.Response(200)).fetch(_URL)
    assert res.ok is False
    assert res.error == WebError.SSRF_BLOCKED


# -- (issue 1) true IP pinning in _PinnedTransport -----------------------------

def test_pinned_transport_rewrites_connect_host_to_screened_ip():
    """The pinned transport must dial the screened IP LITERAL (not re-resolve the
    hostname) while preserving the original Host header + SNI for TLS."""
    seen = {}

    def _fake_super(s, r):
        seen["host"] = r.url.host
        seen["header_host"] = r.headers.get("Host")
        seen["sni"] = r.extensions.get("sni_hostname")
        return httpx.Response(200, text="ok", request=r, extensions={})

    t = sc._PinnedTransport("example.com", [_PUBLIC_IP], verify=True)
    orig = httpx.HTTPTransport.handle_request
    httpx.HTTPTransport.handle_request = lambda s, r: _fake_super(s, r)
    try:
        req = httpx.Request("GET", "https://example.com/page")
        sc._PinnedTransport.handle_request(t, req)
    finally:
        httpx.HTTPTransport.handle_request = orig
    assert seen["host"] == _PUBLIC_IP  # dialed the screened IP literal
    assert seen["header_host"] == "example.com"  # Host preserved
    assert seen["sni"] == "example.com"  # SNI preserved for TLS verification


def test_pinned_transport_skips_rewrite_for_ip_literal_host():
    """When the URL host is already the screened IP, no rewrite/SNI is needed."""
    seen = {}

    def _fake_super(s, r):
        seen["host"] = r.url.host
        seen["sni"] = r.extensions.get("sni_hostname")
        return httpx.Response(200, request=r, extensions={})

    t = sc._PinnedTransport(_PUBLIC_IP, [_PUBLIC_IP], verify=True)
    orig = httpx.HTTPTransport.handle_request
    httpx.HTTPTransport.handle_request = lambda s, r: _fake_super(s, r)
    try:
        req = httpx.Request("GET", f"https://{_PUBLIC_IP}/page")
        sc._PinnedTransport.handle_request(t, req)
    finally:
        httpx.HTTPTransport.handle_request = orig
    assert seen["host"] == _PUBLIC_IP
    assert seen["sni"] is None  # no SNI override when already an IP literal


def test_pinned_transport_peer_mismatch_fails_closed():
    """If the actual connected peer is NOT in the screened set, abort (SSRFBlocked)."""

    class _Stream:
        def get_extra_info(self, name):
            return ("10.0.0.7", 443)  # internal peer not in screened set

    def _fake_super(s, r):
        return httpx.Response(200, request=r, extensions={"network_stream": _Stream()})

    t = sc._PinnedTransport("example.com", [_PUBLIC_IP], verify=True)
    orig = httpx.HTTPTransport.handle_request
    httpx.HTTPTransport.handle_request = lambda s, r: _fake_super(s, r)
    try:
        req = httpx.Request("GET", "https://example.com/page")
        with pytest.raises(sc.SSRFBlocked):
            sc._PinnedTransport.handle_request(t, req)
    finally:
        httpx.HTTPTransport.handle_request = orig
