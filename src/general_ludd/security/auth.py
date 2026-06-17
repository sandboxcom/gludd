"""Shared auth, path-confinement, and SSRF primitives.

Centralizes the security checks that were previously duplicated (and in places
weaker) across the daemon, the worker, the skill fetcher, and the model/integrity
routers:

  * ``verify_psk``        — constant-time PSK comparison (hmac.compare_digest).
  * ``require_auth_env``  — read the GLUDD_REQUIRE_AUTH opt-in fail-closed flag.
  * ``is_path_within``    — realpath+commonpath jail; refuses absolute paths and
                            ``../`` escapes. Mirrors ExecutionEngine's guard.
  * ``is_safe_fetch_url`` — SSRF guard for remote skill fetches. https-only,
                            and blocks loopback / link-local / RFC-1918 targets
                            by a LITERAL host check (NO DNS resolution, so it is
                            import-safe and can never block on the network).

Hang-safety contract: NOTHING in this module performs blocking I/O — no socket
binds, no DNS lookups, no network calls, no sleeps. ``is_safe_fetch_url`` parses
the URL string and matches the literal host against deny patterns only.
"""

from __future__ import annotations

import hmac
import ipaddress
import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class AuthPosture:
    """Resolved PSK auth posture for a daemon/worker surface.

    Attributes:
        psk:          The configured pre-shared key (empty string if none).
        require_auth: Whether GLUDD_REQUIRE_AUTH opted into fail-closed mode.
        no_auth:      Whether NO PSK is configured (``not psk``). When this is
                      True *and* ``require_auth`` is True, the surface must
                      fail closed (503) rather than serve unauthenticated.
        surface:      Name of the surface this posture was resolved for
                      (e.g. "worker", "daemon") — used only for logging.
    """

    psk: str
    require_auth: bool
    no_auth: bool
    surface: str


def load_auth_posture(
    surface: str, env: Mapping[str, str] | None = None
) -> AuthPosture:
    """Resolve the shared PSK auth posture from the environment.

    Reads ``GLUDD_PSK`` (the pre-shared key) and ``GLUDD_REQUIRE_AUTH`` (the
    fail-closed opt-in) so the daemon and the worker derive an identical posture
    and cannot drift. Emits the LOUD no-PSK startup warning when auth is required
    but no key is configured. Performs no I/O beyond reading env + logging.
    """
    source = env if env is not None else os.environ
    psk = (source.get("GLUDD_PSK", "") or "").strip()
    require_auth = require_auth_env(source)
    no_auth = not psk
    if no_auth and require_auth:
        import logging

        logging.getLogger("general_ludd.security.auth").warning(
            "GLUDD_REQUIRE_AUTH is set but no GLUDD_PSK configured for the %s "
            "surface: failing CLOSED (503) on all non-public paths.",
            surface,
        )
    return AuthPosture(
        psk=psk, require_auth=require_auth, no_auth=no_auth, surface=surface
    )


def check_bearer_token(auth_header: str, expected: str) -> bool:
    """Constant-time check of a ``Authorization: Bearer <token>`` header.

    Extracts the token after the ``Bearer `` prefix and compares it to
    ``expected`` via :func:`verify_psk` (hmac.compare_digest). Returns ``False``
    for a missing/malformed header or an empty expected key.
    """
    if not auth_header or not auth_header.startswith("Bearer "):
        return False
    token = auth_header[len("Bearer ") :].strip()
    return verify_psk(token, expected)


def verify_psk(presented: str, expected: str) -> bool:
    """Constant-time check that ``presented`` matches the configured ``expected``.

    Returns ``False`` for an empty presented token or an empty expected key.
    Uses :func:`hmac.compare_digest` so the comparison does not leak the secret
    via a timing side channel.
    """
    if not presented or not expected:
        return False
    return hmac.compare_digest(presented, expected)


def require_auth_env(env: Mapping[str, str] | None = None) -> bool:
    """Return whether GLUDD_REQUIRE_AUTH requests a fail-closed posture."""
    source = env if env is not None else os.environ
    return source.get("GLUDD_REQUIRE_AUTH", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def is_join_within(base: str, candidate: str) -> bool:
    """True iff ``candidate`` resolves to a path inside ``base``.

    ``candidate`` is joined onto ``base`` first, so a relative path is taken
    relative to the base while an ABSOLUTE candidate replaces the base entirely
    (the classic escape) — which this function then catches via ``commonpath``.
    Both paths are passed through ``realpath`` so symlink and ``../`` escapes are
    resolved before comparison. Pure string/filesystem-metadata work only; no
    network, no blocking.
    """
    try:
        base_real = os.path.realpath(base)
        full = os.path.realpath(os.path.join(base_real, candidate))
        common = os.path.commonpath([base_real, full])
    except (ValueError, OSError):
        # Mixed drives, embedded NULs, etc. -> treat as not contained.
        return False
    return common == base_real


# Back-compat alias
is_path_within = is_join_within


def _host_is_blocked(host: str) -> bool:
    """LITERAL deny check for a URL host — no DNS, no network.

    Blocks empty hosts, loopback names, the cloud metadata endpoint, and any
    host that is *already* a private / loopback / link-local / reserved IP
    literal. Hostnames that are not IP literals are NOT resolved (that would be
    blocking DNS and is the documented hang risk); they pass the IP checks and
    are only caught by the explicit name blocklist below.
    """
    host = host.strip().lower()
    if not host:
        return True
    # Strip an IPv6 bracket wrapper, e.g. "[::1]" -> "::1".
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    # Explicit name blocklist (cloud metadata + loopback names).
    blocked_names = {
        "localhost",
        "localhost.localdomain",
        "metadata",
        "metadata.google.internal",
        "instance-data",
    }
    if host in blocked_names:
        return True
    # The AWS/GCP/Azure metadata service literal.
    if host in {"169.254.169.254", "100.100.100.200"}:
        return True
    # If the host is an IP literal, reject private / loopback / link-local /
    # reserved / multicast / unspecified ranges (covers RFC-1918, 127/8, ::1,
    # fc00::/7, fe80::/10, etc.). Non-literal hostnames raise ValueError and
    # fall through — we never resolve them.
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def is_safe_fetch_url(url: str) -> bool:
    """SSRF guard for outbound skill fetches. https-only + literal host deny.

    Returns ``True`` only when the URL is well-formed, uses the ``https`` scheme,
    and its LITERAL host is not a loopback / link-local / RFC-1918 / metadata
    target. Performs NO DNS resolution and NO network I/O, so it is safe to call
    on any hot path and can never block.
    """
    if not url or not isinstance(url, str):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme.lower() != "https":
        return False
    if not parts.hostname:
        return False
    return not _host_is_blocked(parts.hostname)
