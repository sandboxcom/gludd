"""SSRF blocklist PARITY across every entrypoint — no drift, ever.

A verified audit found the private-host/IP deny predicate implemented 3-4 times
with DRIFT: ``connectors.base.is_safe_endpoint`` and ``sanitize.validate_fetch_url``
were MISSING ``instance-data`` / ``localhost.localdomain`` / the Alibaba metadata
IP ``100.100.100.200`` that ``auth.is_safe_fetch_url`` already blocked.

This module pins the fix: all three public guards now delegate to the single
canonical predicate in :mod:`general_ludd.security.ssrf`, so they MUST block the
exact same canonical hostile set (and all allow a normal public host). Each guard
keeps its own scheme policy (auth = https-only, connectors = http+https), so the
hostile URLs are expressed per-guard with that guard's permitted scheme.

Pure literal-host checks — no DNS, no network, no blocking.
"""

from __future__ import annotations

import pytest

from general_ludd.connectors.base import is_safe_endpoint
from general_ludd.security.auth import is_safe_fetch_url
from general_ludd.security.sanitize import validate_fetch_url
from general_ludd.security.ssrf import host_is_blocked, is_url_blocked

# The canonical hostile host/IP set every entrypoint MUST deny. Includes the
# names/IPs that used to be missing from the connector + sanitize blocklists.
CANONICAL_HOSTILE_HOSTS = [
    "localhost",
    "localhost.localdomain",
    "metadata",
    "metadata.google.internal",
    "metadata.goog",  # GCP metadata alias — connector-consolidation regression fix
    "instance-data",  # <- was missing from connectors + sanitize
    "ip6-localhost",  # IPv6 loopback alias — connector-consolidation regression fix
    "ip6-loopback",  # IPv6 loopback alias — connector-consolidation regression fix
    "169.254.169.254",  # AWS/GCP/Azure metadata (also link-local)
    "100.100.100.200",  # <- Alibaba metadata, was missing from connectors + sanitize
    "127.0.0.1",  # loopback v4
    "::1",  # loopback v6
    "10.0.0.5",  # RFC-1918
    "192.168.1.10",  # RFC-1918
    "172.16.0.4",  # RFC-1918
    "169.254.0.1",  # link-local
    "0.0.0.0",  # unspecified
    "fc00::1",  # unique-local v6
    "192.0.2.1",  # TEST-NET-1 / documentation range (not is_global)
]

# A normal public host every entrypoint MUST allow.
PUBLIC_HOST = "api.public.example.com"


def _https(host: str) -> str:
    """An https URL for ``host`` (auth + sanitize are https-only)."""
    h = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"https://{h}/path"


def _http(host: str) -> str:
    """An http URL for ``host`` (connectors permit http + https)."""
    h = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"http://{h}/path"


# --------------------------------------------------------------------------- #
# The shared primitive denies every canonical hostile host.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("host", CANONICAL_HOSTILE_HOSTS)
def test_host_is_blocked_denies_canonical_set(host: str) -> None:
    assert host_is_blocked(host) is True


def test_host_is_blocked_allows_public_host() -> None:
    assert host_is_blocked(PUBLIC_HOST) is False


# --------------------------------------------------------------------------- #
# Hardening: trailing-dot (FQDN root) and NUL-byte host bypasses are denied.
# Without the fix, "localhost." slips past the name blocklist (it != "localhost")
# and "127.0.0.1." fails ip_address() parsing, while "localhost\x00.evil.com"
# may be truncated to "localhost" by a downstream resolver/client.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "host",
    [
        "localhost.",  # trailing dot on a blocked name
        "metadata.",
        "127.0.0.1.",  # trailing dot on a blocked IP literal
        "169.254.169.254.",  # cloud metadata IP with trailing dot
        "::1.",  # trailing dot after IPv6 loopback (bracket already stripped)
        "[::1].",  # bracketed IPv6 loopback + trailing dot (dot must strip first)
        "[::1]",  # bracketed IPv6 loopback (no dot)
        "127.0.0.1..",  # DOUBLE trailing dot (rstrip must remove all)
        "metadata..",
        "localhost\x00",  # NUL after a blocked name
        "localhost\x00.evil.example.com",  # NUL-truncation smuggling a blocked host
        "127.0.0.1\x00.evil.example.com",
    ],
)
def test_host_is_blocked_denies_trailing_dot_and_nul_bypasses(host: str) -> None:
    assert host_is_blocked(host) is True


def test_host_is_blocked_allows_public_host_with_trailing_dot() -> None:
    # A trailing FQDN dot on a legitimately-public host must NOT be over-blocked.
    assert host_is_blocked("api.public.example.com.") is False


# --------------------------------------------------------------------------- #
# RFC-6761 reserves the ENTIRE ``.localhost`` TLD for loopback, so any
# subdomain must be denied — not just the bare ``localhost`` name. This closes
# the regression where prom_scrape's bespoke guard blocked ``*.localhost`` but
# the canonical guard (which every connector now delegates to) only blocked the
# exact name ``localhost``.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "host",
    [
        "foo.localhost",
        "api.svc.localhost",
        "FOO.LOCALHOST",  # case-insensitive
        "app.localhost.",  # trailing FQDN dot
        "sub.foo.localhost",
    ],
)
def test_host_is_blocked_denies_dot_localhost_subdomains(host: str) -> None:
    assert host_is_blocked(host) is True


def test_is_url_blocked_denies_dot_localhost_subdomain() -> None:
    # The scheme-aware wrapper every connector guard funnels through must also
    # deny a ``.localhost`` subdomain over http.
    assert is_url_blocked("http://foo.localhost") is True
    assert is_url_blocked("https://api.svc.localhost/path") is True


def test_all_entrypoints_deny_dot_localhost_subdomain() -> None:
    # Parity across all three public entrypoints for the ``.localhost`` TLD.
    assert is_safe_fetch_url(_https("foo.localhost")) is False
    assert validate_fetch_url(_https("foo.localhost")) is None
    assert is_safe_endpoint(_http("foo.localhost")) is False
    assert is_safe_endpoint(_https("foo.localhost")) is False


def test_host_is_blocked_allows_public_host_ending_in_localhost_label() -> None:
    # A public host whose final label merely CONTAINS "localhost" (but is not
    # the ".localhost" TLD) must NOT be over-blocked.
    assert host_is_blocked("notlocalhost.example.com") is False
    assert host_is_blocked("mylocalhost.com") is False


# --------------------------------------------------------------------------- #
# All THREE public entrypoints deny the SAME canonical hostile set.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("host", CANONICAL_HOSTILE_HOSTS)
def test_auth_is_safe_fetch_url_blocks_canonical_set(host: str) -> None:
    # https-only guard.
    assert is_safe_fetch_url(_https(host)) is False


@pytest.mark.parametrize("host", CANONICAL_HOSTILE_HOSTS)
def test_validate_fetch_url_blocks_canonical_set(host: str) -> None:
    # https-only guard; returns the URL (truthy) when safe, None when blocked.
    assert validate_fetch_url(_https(host)) is None


@pytest.mark.parametrize("host", CANONICAL_HOSTILE_HOSTS)
def test_is_safe_endpoint_blocks_canonical_set_http(host: str) -> None:
    # connectors permit http — the hostile host must STILL be denied over http.
    assert is_safe_endpoint(_http(host)) is False


@pytest.mark.parametrize("host", CANONICAL_HOSTILE_HOSTS)
def test_is_safe_endpoint_blocks_canonical_set_https(host: str) -> None:
    assert is_safe_endpoint(_https(host)) is False


# --------------------------------------------------------------------------- #
# All THREE entrypoints ALLOW a normal public host (parity in the allow path).
# --------------------------------------------------------------------------- #
def test_all_entrypoints_allow_public_host() -> None:
    assert is_safe_fetch_url(_https(PUBLIC_HOST)) is True
    assert validate_fetch_url(_https(PUBLIC_HOST)) == _https(PUBLIC_HOST)
    assert is_safe_endpoint(_https(PUBLIC_HOST)) is True
    assert is_safe_endpoint(_http(PUBLIC_HOST)) is True


# --------------------------------------------------------------------------- #
# Scheme policy is preserved per guard.
# --------------------------------------------------------------------------- #
def test_auth_and_sanitize_reject_plain_http_public_host() -> None:
    # auth + sanitize are https-only: a public host over plain http is rejected.
    assert is_safe_fetch_url(_http(PUBLIC_HOST)) is False
    assert validate_fetch_url(_http(PUBLIC_HOST)) is None


def test_all_entrypoints_reject_disallowed_scheme() -> None:
    for url in ("ftp://example.com/x", "file:///etc/passwd", "gopher://x/"):
        assert is_safe_fetch_url(url) is False
        assert validate_fetch_url(url) is None
        assert is_safe_endpoint(url) is False
