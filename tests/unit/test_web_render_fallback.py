"""Offline tests for render_page: lazy-import fallback + remote-endpoint SSRF guard.

No browser is ever launched: nothing extra is installed (so the default path
returns RENDERER_UNAVAILABLE) and an injected fake backend exercises the success
path. The remote-endpoint SSRF guard is the critical surface here.
"""

from __future__ import annotations

import socket

import pytest

from general_ludd.web import render as rmod
from general_ludd.web import ssrf_client as sc
from general_ludd.web.render import RenderConfig, render_page
from general_ludd.web.results import WebError

_PUBLIC_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    # render's resolve_and_screen lives in ssrf_client, so patch sc.socket.
    def _fake(host, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_PUBLIC_IP, 9222))]

    monkeypatch.setattr(sc.socket, "getaddrinfo", _fake)


def _fake_backend(url, cfg):
    return ("<html>rendered</html>", "rendered", None, 200, url)


# -- lazy import fallback -----------------------------------------------------

def test_renderer_unavailable_default(monkeypatch):
    # Force the lazy import to fail regardless of whether playwright is installed.
    def _boom(url, cfg):
        raise rmod._RendererUnavailable("not installed")

    monkeypatch.setattr(rmod, "_render_playwright", _boom)
    res = render_page("https://example.com/", config=RenderConfig())
    assert res.ok is False
    assert res.error == WebError.RENDERER_UNAVAILABLE


def test_injected_backend_success():
    res = render_page("https://example.com/", config=RenderConfig(), _backend=_fake_backend)
    assert res.ok is True
    assert res.html == "<html>rendered</html>"
    assert res.mode == "headless_local"


# -- target SSRF guard --------------------------------------------------------

def test_target_url_ssrf_blocked():
    res = render_page("https://127.0.0.1/x", config=RenderConfig(), _backend=_fake_backend)
    assert res.ok is False
    assert res.error == WebError.SSRF_BLOCKED


def test_target_http_blocked():
    res = render_page("http://example.com/x", config=RenderConfig(), _backend=_fake_backend)
    assert res.ok is False
    assert res.error == WebError.SSRF_BLOCKED


# -- remote endpoint SSRF guard (CRITICAL) ------------------------------------

def test_remote_cdp_requires_allowlist():
    cfg = RenderConfig(mode="remote_cdp", endpoint="http://grid.example.com:9222",
                       remote_host_allowlist=[])  # empty allowlist -> blocked
    res = render_page("https://example.com/", config=cfg, _backend=_fake_backend)
    assert res.ok is False
    assert res.error == WebError.SSRF_BLOCKED


def test_remote_cdp_localhost_blocked_even_if_allowlisted(monkeypatch):
    # localhost:9222 resolves to a loopback IP -> blocked by the IP reblock even
    # though it is on the allowlist.
    def _loopback(host, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 9222))]

    monkeypatch.setattr(sc.socket, "getaddrinfo", _loopback)
    cfg = RenderConfig(mode="remote_cdp", endpoint="http://localhost:9222",
                       remote_host_allowlist=["localhost"])
    res = render_page("https://example.com/", config=cfg, _backend=_fake_backend)
    assert res.ok is False
    assert res.error == WebError.SSRF_BLOCKED


def test_remote_cdp_allowlisted_public_ok():
    cfg = RenderConfig(mode="remote_cdp", endpoint="http://grid.example.com:9222",
                       remote_host_allowlist=["grid.example.com"])
    res = render_page("https://example.com/", config=cfg, _backend=_fake_backend)
    assert res.ok is True
    assert res.mode == "remote_cdp"


def test_remote_cdp_ws_scheme_normalized_and_guarded():
    cfg = RenderConfig(mode="remote_cdp", endpoint="ws://grid.example.com:9222/devtools",
                       remote_host_allowlist=["grid.example.com"])
    res = render_page("https://example.com/", config=cfg, _backend=_fake_backend)
    assert res.ok is True


def test_webdriver_remote_internal_endpoint_blocked(monkeypatch):
    def _internal(host, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 4444))]

    monkeypatch.setattr(sc.socket, "getaddrinfo", _internal)
    cfg = RenderConfig(mode="webdriver_remote", endpoint="http://grid.internal:4444/wd/hub",
                       remote_host_allowlist=["grid.internal"])
    res = render_page("https://example.com/", config=cfg, _backend=_fake_backend)
    assert res.ok is False
    assert res.error == WebError.SSRF_BLOCKED


def test_unknown_mode_structured():
    res = render_page("https://example.com/", config=RenderConfig(mode="teleport"),
                      _backend=_fake_backend)
    assert res.ok is False
    assert res.error == WebError.RENDER_CONNECT_FAILED


def test_backend_exception_structured():
    def _boom(url, cfg):
        raise RuntimeError("connect refused")

    res = render_page("https://example.com/", config=RenderConfig(), _backend=_boom)
    assert res.ok is False
    assert res.error == WebError.RENDER_CONNECT_FAILED
    assert "connect refused" not in res.detail or "RuntimeError" in res.detail


# -- (issue 3) DNS-rebind PINNING of the remote endpoint ----------------------

def test_remote_cdp_endpoint_pinned_to_screened_ip():
    """The endpoint handed to the backend must be rewritten to the screened IP
    LITERAL so the browser cannot re-resolve the hostname to an internal IP."""
    captured = {}

    def _capture_backend(url, cfg):
        captured["pinned_endpoint"] = cfg.pinned_endpoint
        captured["raw_endpoint"] = cfg.endpoint
        return ("<html>x</html>", "x", None, 200, url)

    cfg = RenderConfig(mode="remote_cdp", endpoint="http://grid.example.com:9222",
                       remote_host_allowlist=["grid.example.com"])
    res = render_page("https://example.com/", config=cfg, _backend=_capture_backend)
    assert res.ok is True
    # Endpoint pinned to the screened public IP literal, port + scheme preserved.
    assert captured["pinned_endpoint"] == f"http://{_PUBLIC_IP}:9222"
    # The original (unpinned) hostname endpoint is unchanged on the field.
    assert captured["raw_endpoint"] == "http://grid.example.com:9222"


def test_remote_cdp_ws_endpoint_pinned_preserves_scheme_and_path():
    captured = {}

    def _capture_backend(url, cfg):
        captured["ep"] = cfg.pinned_endpoint
        return ("<html>x</html>", "x", None, 200, url)

    cfg = RenderConfig(mode="remote_cdp",
                       endpoint="ws://grid.example.com:9222/devtools/browser/abc",
                       remote_host_allowlist=["grid.example.com"])
    res = render_page("https://example.com/", config=cfg, _backend=_capture_backend)
    assert res.ok is True
    # ws scheme + port + path preserved, host swapped to the screened IP literal.
    assert captured["ep"] == f"ws://{_PUBLIC_IP}:9222/devtools/browser/abc"


def test_pin_endpoint_host_helper_ipv6():
    out = rmod._pin_endpoint_host(
        "http://grid.example.com:9222/json", "grid.example.com", "2606:4700::1111"
    )
    # IPv6 literal must be bracketed in the rewritten netloc.
    assert "[2606:4700::1111]:9222" in out


def test_target_screened_ips_recorded_on_config():
    captured = {}

    def _capture(url, cfg):
        captured["ips"] = list(cfg.target_screened_ips)
        return ("<html>x</html>", "x", None, 200, url)

    render_page("https://example.com/", config=RenderConfig(), _backend=_capture)
    assert captured["ips"] == [_PUBLIC_IP]


# -- (issue 3) Playwright peer re-screen (navigation rebind defence) -----------

def test_assert_peer_screened_blocks_internal_peer():
    class _Resp:
        def server_addr(self):
            return {"ipAddress": "10.0.0.9", "port": 443}

    with pytest.raises(rmod.SSRFBlocked):
        rmod._assert_peer_screened(_Resp(), [_PUBLIC_IP])


def test_assert_peer_screened_blocks_unscreened_peer():
    class _Resp:
        def server_addr(self):
            # public but NOT the screened IP -> rebind to a different host: block.
            return {"ipAddress": "8.8.8.8", "port": 443}

    with pytest.raises(rmod.SSRFBlocked):
        rmod._assert_peer_screened(_Resp(), [_PUBLIC_IP])


def test_assert_peer_screened_allows_screened_peer():
    class _Resp:
        def server_addr(self):
            return {"ipAddress": _PUBLIC_IP, "port": 443}

    # Must not raise.
    rmod._assert_peer_screened(_Resp(), [_PUBLIC_IP])


def test_assert_peer_screened_missing_addr_fails_closed():
    class _Resp:
        def server_addr(self):
            return None

    with pytest.raises(rmod.SSRFBlocked):
        rmod._assert_peer_screened(_Resp(), [_PUBLIC_IP])
