"""Shared SSRF guard for connector base-URL validation.

Provides ``_assert_safe_base_url`` — a literal-host, no-DNS check that rejects
obviously-internal backend URLs before any HTTP request is made.  It is used by
every connector that accepts a caller-supplied ``base_url``; keeping it here
ensures a single authoritative implementation rather than per-connector copies.

Public surface
--------------
``_ALLOWED_SCHEMES``
    ``frozenset`` of the only URL schemes that are ever permitted (``http``,
    ``https``).

``_BLOCKED_HOST_NAMES``
    ``frozenset`` of literal hostnames that are always rejected (unless
    ``allow_private=True``): loopback aliases and well-known cloud metadata
    endpoints.

``_assert_safe_base_url(url, *, allow_private)``
    Raises ``ValueError`` on any policy violation; returns ``None`` otherwise.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_BLOCKED_HOST_NAMES = frozenset(
    {"localhost", "localhost.localdomain", "metadata", "metadata.google.internal"}
)


def _assert_safe_base_url(url: str, *, allow_private: bool) -> None:
    """Reject obviously-internal backend URLs by *literal* host inspection.

    Never resolves DNS: a bare hostname is accepted (egress allowlisting is the
    deployment's job). Raises ``ValueError`` on a non-http(s) scheme, a missing
    host, a named metadata host, or — unless ``allow_private`` — a loopback /
    private / link-local / reserved / multicast / unspecified literal IP.
    """
    try:
        parts = urlsplit(url)
    except ValueError as exc:  # pragma: no cover - urlsplit rarely raises
        raise ValueError(f"unparseable base_url: {url!r}") from exc

    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise ValueError(f"base_url scheme must be http/https: {url!r}")

    host = parts.hostname
    if not host:
        raise ValueError(f"base_url has no host: {url!r}")

    if host.lower() in _BLOCKED_HOST_NAMES and not allow_private:
        raise ValueError(f"base_url host is blocked: {host!r}")

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # A DNS name — do not resolve; literal-host policy accepts it.
        return

    if allow_private:
        return

    if (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        raise ValueError(f"base_url resolves to an internal/private IP: {host!r}")
