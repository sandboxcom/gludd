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
import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from general_ludd.security.ssrf import host_is_blocked
from general_ludd.security.ssrf import is_url_blocked as _is_url_blocked


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

    psk: str = field(repr=False)
    require_auth: bool
    no_auth: bool
    surface: str


def load_auth_posture(surface: str, env: Mapping[str, str] | None = None) -> AuthPosture:
    """Resolve the shared PSK auth posture from the environment.

    Reads ``GLUDD_AUTH_PSK`` (the pre-shared key) and ``GLUDD_REQUIRE_AUTH`` (the
    fail-closed opt-in) so the daemon and the worker derive an identical posture
    and cannot drift. Emits the LOUD no-PSK startup warning when auth is required
    but no key is configured. Performs no I/O beyond reading env + logging.
    """
    source = env if env is not None else os.environ
    psk = (source.get("GLUDD_AUTH_PSK", "") or "").strip()
    no_auth = not psk
    # Default-secure: when NO PSK is configured, REQUIRE auth (fail-closed) unless
    # the operator explicitly opts into no-auth via GLUDD_PSK_DISABLE=1.
    # Without this the worker served unauthenticated by default (fail-open)
    # whenever no PSK + no GLUDD_REQUIRE_AUTH.
    _auth_disabled_psk_disable = source.get("GLUDD_PSK_DISABLE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    _auth_disabled_allow_no_auth = source.get("GLUDD_ALLOW_NO_AUTH", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    _auth_disabled = _auth_disabled_psk_disable or _auth_disabled_allow_no_auth
    require_auth = require_auth_env(source) or (no_auth and not _auth_disabled)
    if no_auth and require_auth:
        import logging

        logging.getLogger("general_ludd.security.auth").warning(
            "No GLUDD_AUTH_PSK configured for the %s surface: failing CLOSED on "
            "all non-public paths. Set GLUDD_AUTH_PSK to enable auth, or "
            "GLUDD_PSK_DISABLE=1 / GLUDD_ALLOW_NO_AUTH=1 to explicitly disable it.",
            surface,
        )
    return AuthPosture(psk=psk, require_auth=require_auth, no_auth=no_auth, surface=surface)


def check_bearer_token(auth_header: str, expected: str) -> bool:
    """Constant-time check of a ``Authorization: Bearer <token>`` header.

    Extracts the token after the ``Bearer `` prefix and removes surrounding
    optional whitespace before comparing it to ``expected`` via
    :func:`verify_psk`. Returns ``False`` for a
    missing/malformed header or an empty expected key.
    """
    if not auth_header or not auth_header.startswith("Bearer "):
        return False
    token = auth_header[len("Bearer ") :].strip()
    return verify_psk(token, expected)


def verify_psk(presented: str, expected: str) -> bool:
    """Constant-time check that ``presented`` matches the configured ``expected``.

    Returns ``False`` for an empty presented token or an empty expected key.
    Raises ``TypeError`` for non-string inputs. Uses
    :func:`hmac.compare_digest` (on UTF-8 encodings of both values) so the
    comparison does not leak the secret via a timing side channel and supports
    non-ASCII keys.
    """
    if not isinstance(presented, str) or not isinstance(expected, str):
        raise TypeError("verify_psk requires str inputs")
    if not presented or not expected:
        return False
    return hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))


def _load_admin_token(env: Mapping[str, str] | None = None) -> str:
    source = env if env is not None else os.environ
    return source.get("GLUDD_ADMIN_TOKEN", "") or ""


def check_admin_token(header_value: str, expected: str | None = None) -> bool:
    """Constant-time comparison of a presented admin token against the expected value."""
    if expected is None:
        expected = _load_admin_token()
    if not header_value or not expected:
        return False
    if not isinstance(header_value, str) or not isinstance(expected, str):
        return False
    return hmac.compare_digest(header_value.strip().encode("utf-8"), expected.encode("utf-8"))


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


# Back-compat alias: ``is_path_within`` was the original name before the rename
# to ``is_join_within``. Both names must resolve to the SAME object so that
# identity checks (``is_path_within is is_join_within``) pass and call sites
# that import either name get identical behaviour.
is_path_within = is_join_within


def _host_is_blocked(host: str) -> bool:
    """LITERAL deny check for a URL host — no DNS, no network.

    Thin wrapper over the canonical :func:`general_ludd.security.ssrf.
    host_is_blocked` so this module's historical name stays importable while the
    blocklist lives in exactly one place. Blocks empty hosts, loopback/metadata
    names, the literal metadata IPs, and any host that is already a private /
    loopback / link-local / reserved / multicast / unspecified IP literal.
    """
    return host_is_blocked(host)


def is_safe_fetch_url(url: str) -> bool:
    """SSRF guard for outbound skill fetches. https-only + literal host deny.

    Returns ``True`` only when the URL is well-formed, uses the ``https`` scheme,
    and its LITERAL host is not a loopback / link-local / RFC-1918 / metadata
    target. Delegates the host/scheme decision to the canonical
    :func:`general_ludd.security.ssrf.is_url_blocked` with an https-only scheme
    allowlist. Performs NO DNS resolution and NO network I/O, so it is safe to
    call on any hot path and can never block.
    """
    if not url or not isinstance(url, str):
        return False
    return not _is_url_blocked(url, scheme_allowlist={"https"})
