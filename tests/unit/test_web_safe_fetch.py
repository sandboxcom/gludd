"""Offline SSRF tests for SafeFetcher — httpx.MockTransport + fake resolver.

No real network, no real DNS. Asserts: literal-host block, DNS-to-internal block
(the gap is_safe_fetch_url leaves open), https-only, redirect-to-internal block
mid-chain, redirect cap, cross-origin credential strip, byte cap, the per-hop
IP-pin + peer-verify (recorded so it is offline-assertable AND on a real socket),
client-no-leak, and the wall-clock deadline.
"""

from __future__ import annotations

import http.server
import threading

import httpx
import pytest

from general_ludd.web.policy import WebPolicy
from general_ludd.web.safe_fetch import (
    _PINNED_IP_EXT,
    SafeFetcher,
    SafeFetchError,
    _ip_is_blocked,
    _PinnedResolutionTransport,
)
from general_ludd.web.types import WebError


def _public_resolver(host: str, port: int) -> list[str]:
    return ["93.184.216.34"]  # example.com, public


def _internal_resolver(host: str, port: int) -> list[str]:
    return ["10.0.0.5"]


def _ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text="hello", headers={"content-type": "text/plain"})


def _fetcher(handler, resolver=_public_resolver, **policy_over) -> SafeFetcher:
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    return SafeFetcher(client=client, resolver=resolver, policy=WebPolicy(**policy_over))


# -- IP classification -------------------------------------------------------
def test_ip_is_blocked_covers_internal_ranges() -> None:
    for bad in ("10.0.0.1", "127.0.0.1", "169.254.169.254", "100.100.100.200",
                "192.168.1.1", "172.16.0.1", "::1", "fe80::1", "0.0.0.0"):
        assert _ip_is_blocked(bad), bad
    for good in ("93.184.216.34", "1.1.1.1", "8.8.8.8"):
        assert not _ip_is_blocked(good), good


def test_unparseable_ip_fails_closed() -> None:
    assert _ip_is_blocked("not-an-ip")


# -- string guard ------------------------------------------------------------
def test_http_scheme_blocked_https_only() -> None:
    f = _fetcher(_ok_handler)
    with pytest.raises(SafeFetchError) as ei:
        f.fetch("http://example.com/")
    assert ei.value.error == WebError.SSRF_BLOCKED


def test_literal_internal_host_blocked() -> None:
    f = _fetcher(_ok_handler)
    with pytest.raises(SafeFetchError) as ei:
        f.fetch("https://127.0.0.1/")
    assert ei.value.error == WebError.SSRF_BLOCKED


def test_metadata_host_blocked() -> None:
    f = _fetcher(_ok_handler)
    with pytest.raises(SafeFetchError):
        f.fetch("https://169.254.169.254/latest/meta-data/")


# -- the GAP: hostname that RESOLVES to internal --------------------------------
def test_dns_rebind_to_internal_blocked() -> None:
    """A public-looking hostname that resolves to 10.x must be blocked — the
    exact gap is_safe_fetch_url (no DNS) leaves open."""
    f = _fetcher(_ok_handler, resolver=_internal_resolver)
    with pytest.raises(SafeFetchError) as ei:
        f.fetch("https://evil.example.com/")
    assert ei.value.error == WebError.SSRF_BLOCKED
    assert "blocked address" in ei.value.detail


def test_partial_internal_record_blocks_whole_host() -> None:
    """One internal A-record among public ones blocks the whole host (fail-closed)."""
    def mixed(host: str, port: int) -> list[str]:
        return ["93.184.216.34", "10.1.2.3"]
    f = _fetcher(_ok_handler, resolver=mixed)
    with pytest.raises(SafeFetchError) as ei:
        f.fetch("https://roundrobin.example.com/")
    assert ei.value.error == WebError.SSRF_BLOCKED


def test_public_host_succeeds() -> None:
    f = _fetcher(_ok_handler)
    raw = f.fetch("https://example.com/")
    assert raw.status == 200
    assert raw.body == b"hello"


# -- redirects ---------------------------------------------------------------
def test_redirect_to_internal_blocked_on_hop() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "example.com":
            return httpx.Response(302, headers={"location": "https://10.0.0.9/secret"})
        return httpx.Response(200, text="leaked")
    f = _fetcher(handler)
    with pytest.raises(SafeFetchError) as ei:
        f.fetch("https://example.com/")
    assert ei.value.error == WebError.SSRF_BLOCKED


def test_redirect_cap_enforced() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # Always redirect onward to a new public path.
        n = int(request.url.params.get("n", "0"))
        return httpx.Response(302, headers={"location": f"https://example.com/?n={n + 1}"})
    f = _fetcher(handler, max_redirects=3)
    with pytest.raises(SafeFetchError) as ei:
        f.fetch("https://example.com/?n=0")
    assert ei.value.error == WebError.REDIRECT_LIMIT


def test_redirect_followed_to_terminal() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(301, headers={"location": "https://example.com/end"})
        return httpx.Response(200, text="final")
    f = _fetcher(handler)
    raw = f.fetch("https://example.com/start")
    assert raw.status == 200
    assert raw.body == b"final"
    assert len(raw.redirect_chain) == 2


def test_cross_origin_authorization_stripped() -> None:
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "a.example.com":
            return httpx.Response(302, headers={"location": "https://b.example.org/x"})
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, text="ok")

    f = _fetcher(handler)
    f.fetch("https://a.example.com/", headers={"Authorization": "Bearer secret"})
    assert seen["auth"] is None  # stripped on cross-origin hop


def test_body_byte_cap() -> None:
    big = "x" * 10000
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=big)
    f = _fetcher(handler, max_body_bytes=100)
    raw = f.fetch("https://example.com/")
    assert len(raw.body) <= 100


# -- validate_target (render pre-flight) -------------------------------------
def test_validate_target_blocks_internal() -> None:
    f = _fetcher(_ok_handler, resolver=_internal_resolver)
    verdict = f.validate_target("https://evil.example.com/")
    assert verdict.ok is False
    assert verdict.error == WebError.SSRF_BLOCKED


def test_validate_target_ok_public() -> None:
    f = _fetcher(_ok_handler)
    assert f.validate_target("https://example.com/").ok is True


# -- cross-origin credential strip (Cookie + Proxy-Authorization too) ----------
def test_cross_origin_all_credentials_stripped() -> None:
    """Authorization, Cookie AND Proxy-Authorization are ALL stripped on a
    cross-origin redirect hop — not just Authorization (cred-leak close)."""
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "a.example.com":
            return httpx.Response(302, headers={"location": "https://b.example.org/x"})
        seen["authorization"] = request.headers.get("authorization")
        seen["cookie"] = request.headers.get("cookie")
        seen["proxy-authorization"] = request.headers.get("proxy-authorization")
        return httpx.Response(200, text="ok")

    f = _fetcher(handler)
    f.fetch(
        "https://a.example.com/",
        headers={
            "Authorization": "Bearer secret",
            "Cookie": "session=abc",
            "Proxy-Authorization": "Basic zzz",
        },
    )
    assert seen["authorization"] is None
    assert seen["cookie"] is None
    assert seen["proxy-authorization"] is None


def test_same_origin_credentials_preserved() -> None:
    """A same-origin redirect KEEPS credentials (only cross-origin strips)."""
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "https://a.example.com/end"})
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(200, text="ok")

    f = _fetcher(handler)
    f.fetch("https://a.example.com/start", headers={"Cookie": "session=abc"})
    assert seen["cookie"] == "session=abc"


# -- IP-pin recorded + peer-verify (now offline-assertable) --------------------
def test_pin_records_target_ip_offline() -> None:
    """Every hop's pinned transport records the vetted IP it targeted, so the
    peer-verify is NOT a silent no-op even with a mock transport (no socket)."""
    captured: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        resp = httpx.Response(200, text="ok")
        return resp

    f = _fetcher(handler)
    # Patch _verify_peer to capture what the pin recorded for this hop.
    orig = SafeFetcher._verify_peer

    def spy(response, *, expected_ip, url):  # type: ignore[no-untyped-def]
        captured["recorded"] = response.extensions.get(_PINNED_IP_EXT)
        captured["expected"] = expected_ip
        return orig(response, expected_ip=expected_ip, url=url)

    f._verify_peer = spy  # type: ignore[method-assign]
    f.fetch("https://example.com/")
    assert captured["recorded"] == "93.184.216.34"
    assert captured["expected"] == "93.184.216.34"


class _FakeStream:
    """A minimal network_stream exposing a connected peer (server_addr)."""

    def __init__(self, peer_ip: str) -> None:
        self._peer_ip = peer_ip

    def get_extra_info(self, name: str) -> object:
        if name == "server_addr":
            return (self._peer_ip, 443)
        return None


def test_verify_peer_blocks_internal_connected_peer() -> None:
    """If the ACTUAL connected peer (network_stream server_addr) is an internal
    address — a rebind-at-connect — the hop is fatally SSRF-blocked. This proves
    the peer-IP verify runs (not a silent no-op) and reads the real peer first."""

    class _RebindTransport(httpx.MockTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            resp = super().handle_request(request)
            # Simulate the socket having connected to an internal address.
            resp.extensions["network_stream"] = _FakeStream("10.0.0.7")
            return resp

    client = httpx.Client(
        transport=_RebindTransport(lambda r: httpx.Response(200, text="leaked")),
        follow_redirects=False,
    )
    f = SafeFetcher(client=client, resolver=_public_resolver, policy=WebPolicy())
    with pytest.raises(SafeFetchError) as ei:
        f.fetch("https://example.com/")
    assert ei.value.error == WebError.SSRF_BLOCKED
    assert "blocked address" in ei.value.detail


def test_verify_peer_blocks_peer_mismatch() -> None:
    """A connected peer that differs from the pinned/vetted IP (even if itself
    public) is treated as a rebind and blocked."""

    class _MismatchTransport(httpx.MockTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            resp = super().handle_request(request)
            resp.extensions["network_stream"] = _FakeStream("8.8.8.8")  # != vetted
            return resp

    client = httpx.Client(
        transport=_MismatchTransport(lambda r: httpx.Response(200)),
        follow_redirects=False,
    )
    f = SafeFetcher(client=client, resolver=_public_resolver, policy=WebPolicy())
    with pytest.raises(SafeFetchError) as ei:
        f.fetch("https://example.com/")
    assert ei.value.error == WebError.SSRF_BLOCKED
    assert "rebind" in ei.value.detail


def test_injected_client_still_pins_not_silently_downgraded() -> None:
    """An operator-injected client MUST still be routed through the pinned
    transport (no silent TOCTOU downgrade): the request's outgoing transport is a
    _PinnedResolutionTransport wrapping the injected one."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok")

    f = _fetcher(handler)
    orig = SafeFetcher._verify_peer

    def spy(response, *, expected_ip, url):  # type: ignore[no-untyped-def]
        seen["recorded"] = response.extensions.get(_PINNED_IP_EXT)
        return orig(response, expected_ip=expected_ip, url=url)

    f._verify_peer = spy  # type: ignore[method-assign]
    f.fetch("https://example.com/")
    # The pin recorded a value => the injected path went through the pin.
    assert seen["recorded"] == "93.184.216.34"


def test_pinned_transport_rewrites_host_on_network_path() -> None:
    """On the real-network path (rewrite_host=True) the connect URL host is the
    pinned IP while the Host header stays the original name (cert SNI intact)."""
    captured: dict[str, str] = {}

    class _RecordInner(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            captured["connect_host"] = request.url.host
            captured["host_header"] = request.headers.get("host", "")
            return httpx.Response(200, text="ok")

    transport = _PinnedResolutionTransport(
        _RecordInner(), "93.184.216.34", rewrite_host=True
    )
    client = httpx.Client(transport=transport)
    client.get("https://example.com/")
    assert captured["connect_host"] == "93.184.216.34"  # socket targets the IP
    assert captured["host_header"] == "example.com"  # Host header keeps the name
    client.close()


# -- no client/socket leak on the owned per-hop path ---------------------------
def test_no_pinned_client_leak_on_owned_path() -> None:
    """Each owned-path hop builds a fresh per-hop httpx.Client; it MUST be closed
    when the response is closed (no connection-pool/socket leak). We assert the
    per-hop client's close() runs by spying on httpx.Client.close counts."""
    closed = {"n": 0}
    real_close = httpx.Client.close

    def counting_close(self):  # type: ignore[no-untyped-def]
        closed["n"] += 1
        return real_close(self)

    # Force the OWNED path: build a SafeFetcher with NO injected client but a
    # resolver, and a stub real transport via monkeypatching HTTPTransport.
    sent = {"n": 0}

    class _StubHTTP(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            sent["n"] += 1
            return httpx.Response(200, text="ok")

    import general_ludd.web.safe_fetch as sf

    orig_http = sf.httpx.HTTPTransport
    sf.httpx.HTTPTransport = _StubHTTP  # type: ignore[misc,assignment]
    httpx.Client.close = counting_close  # type: ignore[method-assign]
    try:
        f = SafeFetcher(resolver=_public_resolver, policy=WebPolicy())
        raw = f.fetch("https://example.com/")
        assert raw.status == 200
    finally:
        sf.httpx.HTTPTransport = orig_http  # type: ignore[misc]
        httpx.Client.close = real_close  # type: ignore[method-assign]
    # The per-hop pinned client was closed (>=1 close); no leak.
    assert closed["n"] >= 1
    assert sent["n"] == 1


# -- wall-clock deadline actually enforced -------------------------------------
def test_overall_deadline_enforced() -> None:
    """A redirect chain that would otherwise run forever is stopped by the
    monotonic overall_deadline with a structured TIMEOUT (the documented budget,
    previously vaporware)."""

    def handler(request: httpx.Request) -> httpx.Response:
        # Always redirect onward (would loop until the redirect cap); with a
        # zero deadline the FIRST hop's deadline check fires first.
        return httpx.Response(302, headers={"location": "https://example.com/next"})

    f = _fetcher(handler, overall_deadline=0.0, max_redirects=10)
    with pytest.raises(SafeFetchError) as ei:
        f.fetch("https://example.com/")
    assert ei.value.error == WebError.TIMEOUT


# -- REAL-SOCKET integration: pin + peer-verify exercised end to end -----------
def test_pin_and_peer_verify_real_socket() -> None:
    """End-to-end on a REAL loopback HTTP server: the pinned transport connects
    to the vetted IP and the peer-verify reads the actual connected peer. This is
    the real-socket exercise that the offline mock tests cannot give. We allow
    127.0.0.1 here ONLY by stubbing the IP block-check to accept loopback for the
    duration of the test (loopback is normally blocked); the point is to prove the
    pin/connect/peer-read machinery runs against a live socket."""
    server = http.server.HTTPServer(("127.0.0.1", 0), _QuietHandler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        # Allow http + a non-blocklisted hostname ("local.test") that we resolve
        # to 127.0.0.1, so the live server is reachable. The literal-host guard
        # passes (local.test is neither a blocked name nor an IP literal); the pin
        # must still target the resolved 127.0.0.1 and the peer-verify must read
        # the same peer back (matching => no SSRF raise).
        policy = WebPolicy(allow_http=True, overall_deadline=10.0)

        def loopback_resolver(host: str, port_: int) -> list[str]:
            return ["127.0.0.1"]

        import general_ludd.web.safe_fetch as sf

        orig_blocked = sf._ip_is_blocked
        # Treat ONLY loopback as allowed for this test; everything else still blocked.
        sf._ip_is_blocked = lambda ip: orig_blocked(ip) and not ip.startswith("127.")  # type: ignore[assignment]
        try:
            f = SafeFetcher(resolver=loopback_resolver, policy=policy)
            raw = f.fetch(f"http://local.test:{port}/")
        finally:
            sf._ip_is_blocked = orig_blocked  # type: ignore[assignment]
        assert raw.status == 200
        assert raw.body == b"real-socket-ok"
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=2)


class _QuietHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"real-socket-ok"
        self.send_response(200)
        self.send_header("content-type", "text/plain")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:  # silence the test server
        return
