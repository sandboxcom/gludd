"""Offline SSRF tests for SafeFetcher — httpx.MockTransport + fake resolver.

No real network, no real DNS. Asserts: literal-host block, DNS-to-internal block
(the gap is_safe_fetch_url leaves open), https-only, redirect-to-internal block
mid-chain, redirect cap, cross-origin Authorization strip, and byte cap.
"""

from __future__ import annotations

import httpx
import pytest

from general_ludd.web.policy import WebPolicy
from general_ludd.web.safe_fetch import SafeFetcher, SafeFetchError, _ip_is_blocked
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
